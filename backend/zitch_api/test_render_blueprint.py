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

    def test_only_the_credentialed_worker_owns_terminal_whatsapp_delivery(self):
        """Money crons leave WhatsApp alerts retryable without copying Meta secrets."""
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
        worker = re.search(
            r"^    name: zitch-whatsapp-worker$.*?(?=^  - type:|\Z)",
            self.text,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(worker)
        for key in keys:
            self.assertRegex(
                worker.group(0),
                rf"- key: {re.escape(key)}\n\s+fromService: "
                rf"\{{type: web, name: zitch-api, envVarKey: {re.escape(key)}\}}",
                f"zitch-whatsapp-worker must source {key} from zitch-api",
            )

        for service in ("zitch-maturities", "zitch-reconcile-wema"):
            match = re.search(
                rf"^    name: {re.escape(service)}$.*?(?=^  - type:|\Z)",
                self.text,
                re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, service)
            block = match.group(0)
            self.assertRegex(block, r'- key: TXN_ALERTS_WHATSAPP\n\s+value: "false"')
            for key in keys[1:] + ("WHATSAPP_QUEUE_KEY", "WHATSAPP_QUEUE_KEY_PREV"):
                self.assertNotIn(f"- key: {key}", block,
                                 f"{service} must not carry WhatsApp credentials")
