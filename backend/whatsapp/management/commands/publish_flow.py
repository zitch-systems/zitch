"""Prepare a reviewed Flow draft, then publish and verify that exact contract.

Creating a replacement never changes the configured customer Flow. Creation is
draft-only; use its returned ID in a later --flow-id ... --publish invocation.
"""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

import whatsapp
from whatsapp.providers import published_flow_report, wa_live

ASSET = Path(whatsapp.__file__).resolve().parent / "flow_assets" / "pin_flow.json"


def _meta_id(value, label):
    value = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{1,40}", value):
        raise CommandError(f"{label} must be a numeric Meta ID.")
    return value


def _endpoint(value):
    value = str(value or "").strip()
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname
                 and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment
                 and parsed.port in (None, 443)
                 and not any(c.isspace() for c in value))
    except ValueError:
        valid = False
    if not valid:
        raise CommandError("The Flow endpoint must be an HTTPS URL without credentials, query or fragment.")
    return value


def _request(method, url, action, **kwargs):
    """No retries and no raw provider bodies/tokens in command failures."""
    try:
        response = method(url, timeout=60, allow_redirects=False, **kwargs)
    except requests.RequestException:
        raise CommandError(f"{action} request did not complete; outcome unknown. Inspect Meta before retrying.") from None
    try:
        payload = response.json() if response.content else {}
    except (ValueError, TypeError):
        raise CommandError(f"{action} returned an unreadable response; inspect Meta before retrying.") from None
    if not isinstance(payload, dict):
        raise CommandError(f"{action} returned an invalid response.")
    if not 200 <= response.status_code < 300 or payload.get("error"):
        error = payload.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        suffix = f", Meta code {code}" if isinstance(code, int) else ""
        raise CommandError(f"{action} rejected (HTTP {response.status_code}{suffix}).")
    return payload


def _errors(payload):
    errors = payload.get("validation_errors", [])
    if not isinstance(errors, list):
        raise CommandError("Meta returned an invalid validation result.")
    return errors


class Command(BaseCommand):
    help = "Prepare a Flow draft and optionally publish its verified full JSON contract."

    def add_arguments(self, parser):
        parser.add_argument("--publish", action="store_true",
                            help="Publish the selected draft after endpoint, validation and full JSON checks.")
        parser.add_argument("--flow-id", default="",
                            help="Inspect/update this exact Flow instead of WHATSAPP_FLOW['FLOW_ID'].")
        parser.add_argument("--create-name", default="",
                            help="Create a new named draft; cannot be combined with --publish or --flow-id.")
        parser.add_argument("--waba-id", default="",
                            help="Owning WABA for creation; defaults to WHATSAPP['WABA_ID'].")
        parser.add_argument("--endpoint-uri", default="",
                            help="HTTPS data-exchange endpoint to bind to the selected draft.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Validate locally and inspect the exact selected Flow; perform no writes.")

    def handle(self, *args, **options):
        flow = getattr(settings, "WHATSAPP_FLOW", {}) or {}
        cfg = settings.WHATSAPP
        create_name = options["create_name"].strip()
        if options["create_name"] and not create_name:
            raise CommandError("The draft name must be 1–200 characters without control characters.")
        if create_name and (options["flow_id"] or options["publish"]):
            raise CommandError("Create a draft first, then review it and publish using its explicit --flow-id.")
        if options["waba_id"] and not create_name:
            raise CommandError("--waba-id is only used with --create-name.")
        if create_name and (len(create_name) > 200 or any(ord(c) < 32 for c in create_name)):
            raise CommandError("The draft name must be 1–200 characters without control characters.")

        try:
            raw = ASSET.read_bytes()
            doc = json.loads(raw)
            screens = doc["screens"]
            if (not isinstance(doc, dict) or not isinstance(screens, list) or not screens
                    or any(not isinstance(s, dict) or not isinstance(s.get("id"), str)
                           or not s["id"] for s in screens)
                    or len({s["id"] for s in screens}) != len(screens)):
                raise ValueError
        except (OSError, ValueError, TypeError, KeyError):
            raise CommandError("The local Flow asset is unreadable or has invalid/duplicate screens.") from None
        digest = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.stdout.write(f"Local asset: {len(screens)} screens, {len(raw):,} bytes "
                          f"(v{doc.get('version')}, data_api {doc.get('data_api_version')})")
        self.stdout.write(f"Local contract SHA-256: {digest}")

        endpoint = options["endpoint_uri"] or flow.get("ENDPOINT_URI") or ""
        if endpoint:
            endpoint = _endpoint(endpoint)
        if create_name:
            waba_id = _meta_id(options["waba_id"] or cfg.get("WABA_ID"), "WABA ID")
            if not endpoint:
                raise CommandError("Creating a data-exchange draft requires --endpoint-uri.")
            if options["dry_run"]:
                self.stdout.write("Draft creation and endpoint binding planned; no requests sent.")
                return
        else:
            flow_id = options["flow_id"] or flow.get("FLOW_ID") or ""
            if flow_id:
                flow_id = _meta_id(flow_id, "Flow ID")
            if options["dry_run"]:
                return self._report_drift(flow_id, digest)

        if not wa_live():
            raise CommandError("Publishing requires a live WhatsApp channel "
                               "(WHATSAPP_MODE=live with TOKEN + PHONE_NUMBER_ID).")
        base = str(cfg.get("BASE_URL", "https://graph.facebook.com/v26.0")).rstrip("/")
        if not re.fullmatch(r"https://graph\.facebook\.com/v[0-9]+\.[0-9]+", base):
            raise CommandError("WhatsApp BASE_URL must be the versioned HTTPS Meta Graph endpoint.")
        headers = {"Authorization": f"Bearer {cfg['TOKEN']}"}
        if create_name:
            created = _request(requests.post, f"{base}/{waba_id}/flows", "Draft creation",
                               json={"name": create_name, "categories": ["SIGN_UP", "OTHER"]}, headers=headers)
            flow_id = _meta_id(created.get("id"), "Created Flow ID")
            self.stdout.write(f"Created draft Flow {flow_id}; configured customer Flow unchanged.")
        elif not flow_id:
            raise CommandError("No Flow ID — set WHATSAPP_FLOW['FLOW_ID'] or pass --flow-id.")

        info = _request(requests.get, f"{base}/{flow_id}", "Flow inspection", headers=headers,
                        params={"fields": "id,status,endpoint_uri,validation_errors"})
        if str(info.get("id") or "") != flow_id:
            raise CommandError("Meta returned a different Flow ID; no draft changes made.")
        status = str(info.get("status") or "").upper()
        if status == "PUBLISHED" and options["publish"]:
            expected_endpoint = _endpoint(endpoint or info.get("endpoint_uri"))
            self._report_drift(flow_id, digest, strict=True, endpoint=expected_endpoint)
            self.stdout.write(self.style.SUCCESS(f"Flow {flow_id} is already live with the exact repo contract."))
            return
        if status != "DRAFT":
            raise CommandError("The selected Flow is not a draft. Create a replacement Flow instead.")
        # If no update was requested, pin the initially inspected endpoint.
        # Both readbacks must attest to the same endpoint throughout this run.
        expected_endpoint = _endpoint(endpoint or info.get("endpoint_uri"))

        if endpoint:
            # Meta requires multipart metadata, just as for the Flow JSON asset.
            bound = _request(requests.post, f"{base}/{flow_id}", "Endpoint binding", headers=headers,
                             files={"endpoint_uri": (None, endpoint)})
            if bound.get("success") is not True:
                raise CommandError("Meta did not confirm the endpoint update.")

        uploaded = _request(requests.post, f"{base}/{flow_id}/assets", "Asset upload", headers=headers,
                            data={"name": "flow.json", "asset_type": "FLOW_JSON"},
                            files={"file": ("flow.json", raw, "application/json")})
        errors = _errors(uploaded)
        if errors:
            raise CommandError(f"{len(errors)} validation error(s) — not publishing. Inspect the selected Flow in Meta.")
        if uploaded.get("success") is not True:
            raise CommandError("Meta did not confirm the asset upload.")
        # A 200 upload can still be ignored by Meta. Read the complete JSON back
        # before permitting publish; screen/property equality is insufficient.
        self._report_drift(flow_id, digest, strict=True, expected_status="draft", endpoint=expected_endpoint)
        self.stdout.write(self.style.SUCCESS(f"Draft updated on Flow {flow_id}; full JSON verified."))
        if not options["publish"]:
            self.stdout.write(f"Not published — review, then re-run with --flow-id {flow_id} --publish to make it live.")
            return

        published = _request(requests.post, f"{base}/{flow_id}/publish", "Publish", headers=headers)
        if published.get("success") is not True:
            raise CommandError("Meta did not confirm publication; inspect the selected Flow before retrying.")
        self._report_drift(flow_id, digest, strict=True, endpoint=expected_endpoint)
        self.stdout.write(self.style.SUCCESS(f"Flow {flow_id} is live with the exact repo contract."))

    def _report_drift(self, flow_id, digest, *, strict=False, expected_status="published", endpoint=""):
        if not flow_id:
            self.stdout.write("Flow not configured on this host — nothing to compare.")
            return
        report = published_flow_report(force=True, flow_id=flow_id)
        status = report.get("status")
        self.stdout.write(f"Flow {flow_id} status: {status if status in {'published', 'draft'} else 'unverified'}.")
        if report.get("missing_screens"):
            self.stderr.write("Missing screens on Meta: " + ", ".join(report["missing_screens"]))
        if report.get("drifted_screens"):
            self.stderr.write("Screen properties differ: " + ", ".join(report["drifted_screens"]))
        matched = (report.get("flow_id") == flow_id and status == expected_status
                   and not report.get("validation_errors") and report.get("contract_matches") is True
                   and report.get("local_contract_sha256") == digest
                   and report.get("published_contract_sha256") == digest)
        endpoint_verified = bool(report.get("endpoint_uri"))
        try:
            _endpoint(report.get("endpoint_uri"))
        except CommandError:
            endpoint_verified = False
        if endpoint and report.get("endpoint_uri") != endpoint:
            endpoint_verified = False
        if strict and not (matched and endpoint_verified):
            raise CommandError("The selected Flow's status, endpoint, validation or full JSON could not be verified; no success claimed.")
        if matched:
            label = "Published Meta Flow" if status == "published" else "Meta draft"
            self.stdout.write(self.style.SUCCESS(f"{label} matches the repo (full JSON SHA-256 {digest})."))
        else:
            self.stderr.write("Full JSON match not verified — drift unknown or contract differs.")
