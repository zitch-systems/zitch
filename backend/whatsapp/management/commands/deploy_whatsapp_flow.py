"""Explicit, retry-safe Flow operations for a Render plan without a shell.

The API build may prepare a draft, publish a reviewed ID, or retire an old Flow
after the reviewed replacement has been selected.
It never changes WHATSAPP_FLOW_ID and does nothing unless explicitly armed.
"""
import hashlib
import json
import os
import re
from contextlib import contextmanager

import requests
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from whatsapp.management.commands.publish_flow import (
    ASSET, Command as PublishCommand, _endpoint, _meta_id, _request,
)
from whatsapp.providers import published_flow_report, wa_live
from whatsapp.models import SystemSetting


PREFIX = "WHATSAPP_FLOW_DEPLOY_"
MAX_LIST_PAGES = 10
RETIRED_SETUP_SCREENS = {"VAS_SETUP", "VAS_IDENTITY", "VAS_CODE", "VAS_CODE_RETRY", "VAS_REENTRY"}


def _read(*args, **kwargs):
    # Listing is bounded independently from the asset upload's larger timeout.
    kwargs["timeout"] = (5, 15)
    return requests.get(*args, **kwargs)


@contextmanager
def _operation_lock(waba_id):
    """Serialize builds without holding a database transaction during Graph I/O."""
    if connection.vendor != "postgresql":
        raise CommandError("Flow deployment operations require PostgreSQL for a shared deployment lock.")
    key = int.from_bytes(hashlib.sha256(f"zitch.flow.deploy:{waba_id}".encode()).digest()[:8],
                         "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        if cursor.fetchone() != (True,):
            raise CommandError("Another Flow deployment operation is running; retry after it finishes.")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


def _owned_flows(base, waba_id, headers):
    """Read the bounded owner inventory; never follow a provider-supplied URL."""
    result = []
    params = {"fields": "id,name,status", "limit": 100}
    seen_cursors = set()
    for _ in range(MAX_LIST_PAGES):
        page = _request(_read, f"{base}/{waba_id}/flows", "Flow inventory", headers=headers,
                        params=dict(params))
        entries = page.get("data")
        if (not isinstance(entries, list)
                or any(not isinstance(row, dict) for row in entries)):
            raise CommandError("Flow inventory could not be verified; no changes made.")
        result.extend(entries)
        paging = page.get("paging") or {}
        if not isinstance(paging, dict):
            raise CommandError("Flow inventory pagination could not be verified.")
        if not paging.get("next"):
            return result
        cursors = paging.get("cursors") or {}
        after = cursors.get("after") if isinstance(cursors, dict) else None
        if not isinstance(after, str) or not after or len(after) > 2048 or after in seen_cursors:
            raise CommandError("Flow inventory pagination could not be verified.")
        seen_cursors.add(after)
        params["after"] = after
    raise CommandError("Flow inventory exceeded the bounded page limit; no changes made.")


def _request_record(waba_id, request, digest, endpoint):
    """Keep the review immutable and survive an unknown draft-creation outcome."""
    key = "wa_flow_deploy:" + hashlib.sha256(f"{waba_id}:{request}".encode()).hexdigest()[:48]
    binding = {"sha256": digest, "endpoint": hashlib.sha256(endpoint.encode()).hexdigest()}
    record, _ = SystemSetting.objects.get_or_create(
        key=key, defaults={"value": json.dumps({**binding, "create_attempted": False})})
    try:
        state = json.loads(record.value)
    except (ValueError, TypeError):
        state = None
    if (not isinstance(state, dict) or any(state.get(k) != v for k, v in binding.items())
            or type(state.get("create_attempted")) is not bool):
        raise CommandError("This deployment request has a different or unreadable review binding; use a new reviewed request.")
    return record, state


class Command(BaseCommand):
    help = "Run an explicitly armed create-draft, publish or deprecate operation during an API build."
    requires_system_checks = []

    def handle(self, *args, **options):
        operation = os.environ.get(PREFIX + "OPERATION", "").strip()
        if not operation:
            self.stdout.write("No WhatsApp Flow deployment operation requested.")
            return
        if operation not in {"create-draft", "publish", "deprecate"}:
            raise CommandError("WHATSAPP_FLOW_DEPLOY_OPERATION must be create-draft, publish or deprecate.")
        request = os.environ.get(PREFIX + "REQUEST", "").strip()
        expected = os.environ.get(PREFIX + "SHA256", "").strip()
        endpoint = _endpoint(os.environ.get(PREFIX + "ENDPOINT_URI", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request):
            raise CommandError("Flow deployment REQUEST must contain 8–64 letters, digits, underscores or hyphens.")
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise CommandError("Flow deployment SHA256 must be the reviewed canonical JSON digest.")
        try:
            doc = json.loads(ASSET.read_bytes())
            screens = doc["screens"]
            if (not isinstance(screens, list) or not screens
                    or any(not isinstance(s, dict) or not isinstance(s.get("id"), str)
                           or not s["id"] for s in screens)
                    or len({s["id"] for s in screens}) != len(screens)):
                raise ValueError
            digest = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        except (OSError, ValueError, TypeError, KeyError):
            raise CommandError("Local Flow contract is invalid; no changes made.") from None
        if digest != expected:
            raise CommandError("Local Flow contract differs from the reviewed SHA256; no changes made.")
        self.stdout.write(f"Reviewed Flow: SHA256 {digest}; {len(screens)} screens.")

        cfg = getattr(settings, "WHATSAPP", {}) or {}
        waba_id = _meta_id(cfg.get("WABA_ID") or os.environ.get("WHATSAPP_WABA_ID"), "WABA ID")
        if not wa_live():
            raise CommandError("Flow deployment requires the live WhatsApp channel.")
        base = str(cfg.get("BASE_URL", "https://graph.facebook.com/v26.0")).rstrip("/")
        if not re.fullmatch(r"https://graph\.facebook\.com/v[0-9]+\.[0-9]+", base):
            raise CommandError("WhatsApp BASE_URL must be the versioned HTTPS Meta Graph endpoint.")
        name = f"Zitch Partnership {request}"
        flow_id = os.environ.get(PREFIX + "FLOW_ID", "").strip()
        replacement = os.environ.get(PREFIX + "REPLACEMENT_FLOW_ID", "").strip()
        if operation in {"publish", "deprecate"}:
            flow_id = _meta_id(flow_id, "Explicit target Flow ID")
        elif flow_id:
            raise CommandError("FLOW_ID is only allowed for explicit publish or deprecate operations.")
        if operation == "deprecate":
            replacement = _meta_id(replacement, "Published replacement Flow ID")
            configured = str((getattr(settings, "WHATSAPP_FLOW", {}) or {}).get("FLOW_ID") or "")
            if replacement == flow_id or configured != replacement:
                raise CommandError("Select the verified replacement as WHATSAPP_FLOW_ID before deprecating a different old Flow.")
        elif replacement:
            raise CommandError("REPLACEMENT_FLOW_ID is only allowed for the explicit deprecate operation.")
        headers = {"Authorization": f"Bearer {cfg['TOKEN']}"}
        with _operation_lock(waba_id):
            record, state = _request_record(waba_id, request, digest, endpoint)
            owned = _owned_flows(base, waba_id, headers)
            matches = [row for row in owned if row.get("name") == name]
            if len(matches) > 1:
                raise CommandError("Multiple Flows match this deployment request; inspect Meta before proceeding.")
            if operation == "deprecate":
                return self._deprecate(flow_id, replacement, owned, matches, base, headers, digest, endpoint)
            if operation == "create-draft":
                if not matches:
                    # A retry after a lost response finds the deterministic name
                    # above instead of creating a second draft. If inventory has
                    # not caught up, retain ambiguity rather than duplicate it.
                    if state["create_attempted"]:
                        raise CommandError("A prior draft creation was attempted but its named Flow is not visible. Inspect Meta; no second draft created.")
                    state["create_attempted"] = True
                    record.value = json.dumps(state)
                    record.save(update_fields=["value", "updated"])
                    call_command("publish_flow", create_name=name, waba_id=waba_id,
                                 endpoint_uri=endpoint, stdout=self.stdout, stderr=self.stderr)
                    return
                existing = matches[0]
                if str(existing.get("status") or "").upper() != "DRAFT":
                    raise CommandError("This request already names a non-draft Flow; no new draft created.")
                flow_id = _meta_id(existing.get("id"), "Existing draft Flow ID")
                self.stdout.write(f"Reusing draft Flow {flow_id} for this request.")
                call_command("publish_flow", flow_id=flow_id, endpoint_uri=endpoint,
                             stdout=self.stdout, stderr=self.stderr)
                return

            # Publication cannot select an unrelated account/Flow or silently
            # replace the asset reviewed by the operator with a new upload.
            if len(matches) != 1 or str(matches[0].get("id") or "") != flow_id:
                raise CommandError("Publication ID does not match this request's owned Flow.")
            info = _request(_read, f"{base}/{flow_id}", "Publication inspection", headers=headers,
                            params={"fields": "id,name,status,endpoint_uri,validation_errors"})
            if (str(info.get("id") or "") != flow_id or info.get("name") != name
                    or info.get("endpoint_uri") != endpoint):
                raise CommandError("Publication Flow identity or endpoint changed; nothing published.")
            status = str(info.get("status") or "").lower()
            if status not in {"draft", "published"}:
                raise CommandError("Selected Flow is neither draft nor published; nothing published.")
            validator = PublishCommand(stdout=self.stdout, stderr=self.stderr)
            validator._report_drift(flow_id, digest, strict=True, expected_status=status, endpoint=endpoint)
            if status == "published":
                self.stdout.write(self.style.SUCCESS(f"Flow {flow_id} already published with the reviewed contract."))
                return
            published = _request(requests.post, f"{base}/{flow_id}/publish", "Publish", headers=headers)
            if published.get("success") is not True:
                raise CommandError("Meta did not confirm publication; inspect the Flow before retrying.")
            validator._report_drift(flow_id, digest, strict=True, endpoint=endpoint)
            self.stdout.write(self.style.SUCCESS(
                f"Flow {flow_id} published; SHA256 {digest}; {len(screens)} screens. "
                "WHATSAPP_FLOW_ID remains unchanged."
            ))

    def _deprecate(self, flow_id, replacement, owned, matches, base, headers, digest, endpoint):
        if len(matches) != 1 or str(matches[0].get("id") or "") != replacement:
            raise CommandError("Replacement ID does not match this request's owned Flow.")
        if sum(str(row.get("id") or "") == flow_id for row in owned) != 1:
            raise CommandError("Old Flow ownership could not be verified; nothing deprecated.")
        validator = PublishCommand(stdout=self.stdout, stderr=self.stderr)
        validator._report_drift(replacement, digest, strict=True, endpoint=endpoint)
        info = _request(_read, f"{base}/{flow_id}", "Retirement inspection", headers=headers,
                        params={"fields": "id,status"})
        if str(info.get("id") or "") != flow_id:
            raise CommandError("Retirement inspection returned a different Flow ID.")
        status = str(info.get("status") or "").upper()
        if status == "DEPRECATED":
            self.stdout.write(self.style.SUCCESS(f"Flow {flow_id} is already deprecated; replacement {replacement} verified."))
            return
        if status != "PUBLISHED":
            raise CommandError("Only an explicitly selected published old Flow can be deprecated.")
        report = published_flow_report(force=True, flow_id=flow_id)
        screens = report.get("published_screens")
        if (report.get("flow_id") != flow_id or report.get("status") != "published"
                or not isinstance(screens, list) or not all(isinstance(s, str) for s in screens)
                or not RETIRED_SETUP_SCREENS.intersection(screens)):
            raise CommandError("The old Flow's retired account setup screens could not be verified; nothing deprecated.")
        result = _request(requests.post, f"{base}/{flow_id}/deprecate", "Deprecate", headers=headers)
        if result.get("success") is not True:
            raise CommandError("Meta did not confirm deprecation; inspect the old Flow before retrying.")
        after = _request(_read, f"{base}/{flow_id}", "Retirement verification", headers=headers,
                         params={"fields": "id,status"})
        if str(after.get("id") or "") != flow_id or str(after.get("status") or "").upper() != "DEPRECATED":
            raise CommandError("Old Flow deprecation could not be verified; no success claimed.")
        self.stdout.write(self.style.SUCCESS(
            f"Flow {flow_id} deprecated; selected replacement {replacement} remains published with SHA256 {digest}."
        ))
