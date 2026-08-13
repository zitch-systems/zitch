"""Upload `flow_assets/pin_flow.json` to Meta and (optionally) publish it.

The Flow JSON lives in this repo but was *published by hand* in WhatsApp
Manager, which is a deploy step nobody can perform from a deploy. Every change
to the file therefore shipped as code first and reached Meta whenever someone
remembered — and the two are a contract, not two copies: the endpoint must
answer with exactly the properties the published screen declares. Getting that
ordering wrong shows the customer "Couldn't load content. Try again later." on
every ending of the Flow, which is indistinguishable from a timeout.

So the publish becomes a command that runs where the token already is:

    python manage.py publish_flow            # upload the draft, report validation
    python manage.py publish_flow --publish  # ...and make it live

Upload and publish are deliberately two steps. Uploading only replaces the
DRAFT, so it is safe to run at any time and returns the validation errors Meta
would refuse a publish on — which means the risky half can be inspected before
anyone commits to it.
"""
import json
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

import whatsapp
from whatsapp.providers import published_flow_report, wa_live

ASSET = Path(whatsapp.__file__).resolve().parent / "flow_assets" / "pin_flow.json"


class Command(BaseCommand):
    help = "Upload flow_assets/pin_flow.json to Meta as the Flow draft, and optionally publish it."

    def add_arguments(self, parser):
        parser.add_argument("--publish", action="store_true",
                            help="Make the uploaded JSON live after a clean validation.")
        parser.add_argument("--flow-id", default="",
                            help="Override WHATSAPP_FLOW['FLOW_ID'] (for a staging Flow).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Validate the local file and report drift; send nothing.")

    def handle(self, *args, **options):
        flow = getattr(settings, "WHATSAPP_FLOW", {}) or {}
        flow_id = options["flow_id"] or flow.get("FLOW_ID") or ""
        cfg = settings.WHATSAPP

        if not ASSET.exists():
            raise CommandError(f"Flow asset not found: {ASSET}")
        raw = ASSET.read_text()
        try:
            doc = json.loads(raw)
            screens = [s["id"] for s in doc["screens"]]
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f"{ASSET.name} is not valid Flow JSON: {exc}") from exc
        self.stdout.write(f"Local asset: {len(screens)} screens, {len(raw):,} bytes "
                          f"(v{doc.get('version')}, data_api {doc.get('data_api_version')})")

        if options["dry_run"]:
            return self._report_drift()
        if not wa_live():
            raise CommandError("Publishing requires a live WhatsApp channel "
                               "(WHATSAPP_MODE=live with TOKEN + PHONE_NUMBER_ID).")
        if not flow_id:
            raise CommandError("No Flow ID — set WHATSAPP_FLOW['FLOW_ID'] or pass --flow-id.")

        base = cfg.get("BASE_URL", "https://graph.facebook.com/v21.0")
        headers = {"Authorization": f"Bearer {cfg['TOKEN']}"}

        # Replaces the DRAFT only. Meta returns validation_errors here rather
        # than at publish time, which is why this runs first and separately.
        res = requests.post(
            f"{base}/{flow_id}/assets",
            data={"name": "flow.json", "asset_type": "FLOW_JSON"},
            files={"file": ("flow.json", raw.encode(), "application/json")},
            headers=headers, timeout=60,
        )
        payload = res.json() if res.content else {}
        if res.status_code >= 400:
            raise CommandError("Asset upload rejected: "
                               f"{(payload.get('error') or {}).get('message') or res.status_code}")
        errors = payload.get("validation_errors") or []
        for err in errors:
            self.stderr.write(self.style.WARNING(
                f"  validation: {err.get('error_type') or ''} {err.get('message') or err}"))
        self.stdout.write(self.style.SUCCESS(f"Draft updated on Flow {flow_id}."))

        if not options["publish"]:
            self.stdout.write("Not published — re-run with --publish to make it live.")
            return
        if errors:
            # Meta would refuse anyway; failing here says WHY, and says it once.
            raise CommandError(f"{len(errors)} validation error(s) — not publishing.")

        res = requests.post(f"{base}/{flow_id}/publish", headers=headers, timeout=60)
        payload = res.json() if res.content else {}
        if res.status_code >= 400:
            raise CommandError("Publish rejected: "
                               f"{(payload.get('error') or {}).get('message') or res.status_code}")
        self.stdout.write(self.style.SUCCESS(f"Flow {flow_id} is live."))
        self._report_drift()

    def _report_drift(self):
        """The same probe /healthz uses, so the command and the dashboard cannot
        disagree about whether Meta is up to date."""
        report = published_flow_report()
        status = report.get("status")
        if status == "unconfigured":
            return self.stdout.write("Flow not configured on this host — nothing to compare.")
        if status in ("unreachable", "error"):
            # "stale" is False when the probe could not READ Meta, which is not
            # the same as agreeing with it. Reporting a match here would be the
            # most reassuring possible output for the least informed state.
            return self.stderr.write(self.style.WARNING(
                f"Could not read the published Flow ({status}"
                f"{': ' + report['detail'] if report.get('detail') else ''}) — "
                "drift unknown."))
        if report.get("missing_screens"):
            self.stderr.write(self.style.ERROR(
                f"  missing screens on Meta: {', '.join(report['missing_screens'])}"))
        if report.get("drifted_screens"):
            self.stderr.write(self.style.ERROR(
                f"  screens whose declared properties differ: "
                f"{', '.join(report['drifted_screens'])}"))
        if not report.get("stale"):
            self.stdout.write(self.style.SUCCESS("Meta matches the repo."))
