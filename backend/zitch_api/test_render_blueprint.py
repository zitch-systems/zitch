"""Static safety invariants for the shared Render Blueprint."""
import re
from pathlib import Path

from django.test import SimpleTestCase


BLUEPRINT = Path(__file__).resolve().parents[2] / "render.yaml"


class RenderBlueprintSafetyTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.text = BLUEPRINT.read_text(encoding="utf-8")

    def test_every_deployable_service_waits_for_repository_checks(self):
        self.assertNotIn("autoDeployTrigger: commit", self.text)
        self.assertEqual(self.text.count("autoDeployTrigger: checksPass"), 9)

    def test_only_api_generates_secret_and_all_consumers_source_it(self):
        secret_blocks = re.findall(
            r"- key: DJANGO_SECRET_KEY\n(?P<config>\s+[^\n]+)", self.text,
        )
        self.assertEqual(len(secret_blocks), 9)
        self.assertEqual(sum("generateValue: true" in row for row in secret_blocks), 1)
        self.assertEqual(sum("fromService:" in row for row in secret_blocks), 8)

    def test_non_api_builds_refuse_unapplied_shared_migrations(self):
        gate = "python manage.py migrate --check"
        self.assertEqual(self.text.count(gate), 8)

    def test_ledger_writers_share_the_api_whatsapp_configuration(self):
        """A scheduled settlement must not silently lose customer alerts.

        Keep the queue worker, maturity sweep and Wema reconciliation tied to
        the API's single WhatsApp configuration.  Dashboard-owned copies of
        those values drifted during the Frankfurt move and left reconciliation
        unable to deliver transaction alerts.
        """
        keys = (
            "TXN_ALERTS_WHATSAPP",
            "WHATSAPP_MODE",
            "WHATSAPP_BASE_URL",
            "WHATSAPP_TOKEN",
            "WHATSAPP_PHONE_NUMBER_ID",
            "WHATSAPP_VERIFY_TOKEN",
            "WHATSAPP_APP_SECRET",
            "WHATSAPP_BUSINESS_NUMBER",
            "WHATSAPP_TXN_ALERT_TEMPLATE",
            "WHATSAPP_TXN_ALERT_TEMPLATE_LANG",
        )
        for service in ("zitch-whatsapp-worker", "zitch-maturities", "zitch-reconcile-wema"):
            match = re.search(
                rf"^    name: {re.escape(service)}$.*?(?=^  - type:|\Z)",
                self.text,
                re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, service)
            block = match.group(0)
            for key in keys:
                self.assertRegex(
                    block,
                    rf"- key: {re.escape(key)}\n\s+fromService: "
                    rf"\{{type: web, name: zitch-api, envVarKey: {re.escape(key)}\}}",
                    f"{service} must source {key} from zitch-api",
                )
