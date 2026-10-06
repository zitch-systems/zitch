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

    def test_notification_writers_inherit_delivery_credentials_on_both_blueprints(self):
        for filename, suffix in (("render.yaml", ""), ("render.frankfurt.yaml", "-ry6y")):
            text = BLUEPRINT.with_name(filename).read_text(encoding="utf-8")
            for name in ("zitch-whatsapp-worker", "zitch-maturities", "zitch-reconcile-wema"):
                block = re.search(rf"^    name: {name}{suffix}$.*?(?=^  - type:|\Z)",
                                  text, re.MULTILINE | re.DOTALL).group(0)
                for key in ("RESEND_API_KEY", "RESEND_FROM_EMAIL", "TERMII_API_KEY",
                            "TERMII_SENDER_ID", "TERMII_CHANNEL", "TERMII_BASE_URL",
                            "TXN_ALERTS_EMAIL", "TXN_ALERTS_SMS", "TXN_ALERTS_PUSH"):
                    self.assertIn(f"fromService: {{type: web, name: zitch-api{suffix}, envVarKey: {key}}}", block)
                if name == "zitch-reconcile-wema":
                    for key in ("WEMA_UPGRADE_KEY", "WEMA_UPGRADE_BASE_URL"):
                        self.assertIn(f"fromService: {{type: web, name: zitch-api{suffix}, envVarKey: {key}}}", block)

    def test_worker_inherits_shared_runtime_configuration_on_both_blueprints(self):
        """The API accepts/encrypts work that the worker later resumes.

        Independent dashboard copies of these values can make app and WhatsApp
        execute different bank/provider contracts or make queued messages
        undecryptable after a rotation.
        """
        keys = (
            "WEMA_CHANNEL_ID", "WEMA_WALLET_KEY", "WEMA_ACCOUNT_CREATION_KEY",
            "WEMA_BASE_URL", "WEMA_SIMULATION", "WEMA_FACE_VERIFY_URL",
            "WEMA_FACE_CALLBACK_IPS", "WEMA_FACE_CB_MODE", "PREMBLY_BASE_URL",
            "PREMBLY_API_KEY", "PREMBLY_APP_ID", "WHATSAPP_QUEUE_KEY",
            "WHATSAPP_QUEUE_KEY_PREV", "SENTRY_DSN",
        )
        for filename, suffix in (("render.yaml", ""), ("render.frankfurt.yaml", "-ry6y")):
            text = BLUEPRINT.with_name(filename).read_text(encoding="utf-8")
            api_name = f"zitch-api{suffix}"
            worker = re.search(
                rf"^    name: zitch-whatsapp-worker{suffix}$.*?(?=^  - type:|\Z)",
                text, re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(worker)
            self.assertRegex(
                worker.group(0),
                r'- key: DJANGO_REQUIRE_SHARED_CACHE\n\s+value: "true"',
            )
            for key in keys:
                self.assertIn(
                    f"fromService: {{type: web, name: {api_name}, envVarKey: {key}}}",
                    worker.group(0),
                    f"{filename} worker must inherit {key}",
                )

    def test_queue_rotation_secret_is_dashboard_owned_on_both_blueprints(self):
        for filename, suffix in (("render.yaml", ""), ("render.frankfurt.yaml", "-ry6y")):
            text = BLUEPRINT.with_name(filename).read_text(encoding="utf-8")
            api = re.search(
                rf"^    name: zitch-api{suffix}$.*?(?=^  - type:|\Z)",
                text, re.MULTILINE | re.DOTALL,
            ).group(0)
            self.assertRegex(
                api,
                r"- key: WHATSAPP_QUEUE_KEY_PREV\n\s+sync: false",
            )
            self.assertNotRegex(
                api,
                r'- key: WHATSAPP_QUEUE_KEY_PREV\n\s+value: ""',
            )

    def test_scheduled_jobs_do_not_all_start_on_reconciliation_boundary(self):
        """reconcile-wema runs at :00/:10/... and calls the bank.

        Starting the other bank/ledger scans at :00 guaranteed avoidable DB and
        provider bursts, including three jobs together at 06:00.
        """
        for filename in ("render.yaml", "render.frankfurt.yaml"):
            text = BLUEPRINT.with_name(filename).read_text(encoding="utf-8")
            schedules = re.findall(r'^    schedule: "([^"]+)"', text, re.MULTILINE)
            self.assertIn("*/10 * * * *", schedules)
            fixed_minutes = [value.split()[0] for value in schedules if not value.startswith("*/")]
            self.assertNotIn("0", fixed_minutes, filename)

    def test_vas_release_and_identity_configuration_cannot_diverge_between_consumers(self):
        """A worker with a stale rail selector or key cannot safely enroll users."""
        keys = ("BANK_ACCOUNT_PROVIDER", "WEMA_PARTNERSHIP_MODE", "WEMA_VAS_ENABLED",
                "WEMA_VAS_MODE", "WEMA_VAS_PREFIX", "WEMA_VAS_TOKEN", "WEMA_VAS_IDENTITY_KEYS",
                "WEMA_VAS_TRUST_TLS_PROXY", "WEMA_VAS_ENABLE_ENROLLMENT", "WEMA_VAS_RELEASE_PHASE",
                "WEMA_VAS_PILOT_USER_IDS", "WEMA_VAS_LIVE_APPROVAL_REFERENCE",
                "WEMA_VAS_GENERAL_APPROVAL_REFERENCE", "WEMA_VAS_COLLECTION_ACCOUNT")
        flow_keys = ("WHATSAPP_FLOW_ID", "WHATSAPP_FLOW_PRIVATE_KEY", "WHATSAPP_FLOW_PRIVATE_KEY_PASSPHRASE",
                     "WHATSAPP_FLOW_VAS_ENROLLMENT_ENABLED", "WHATSAPP_FLOW_VAS_APPROVED_FLOW_ID")
        for filename, suffix in (("render.yaml", ""), ("render.frankfurt.yaml", "-ry6y")):
            text = BLUEPRINT.with_name(filename).read_text(encoding="utf-8")
            api_name = "zitch-api" + suffix
            services = re.findall(r"^  - type: .*?(?=^  - type: |^databases:|\Z)", text, re.M | re.S)
            for block in services:
                if "    runtime: python\n" not in block:
                    continue
                name = re.search(r"^    name: (.+)$", block, re.M).group(1)
                expected = keys + (flow_keys if "whatsapp-worker" in name or name == api_name else ())
                for key in expected:
                    if name == api_name:
                        self.assertIn(f"- key: {key}\n        sync: false", block)
                    else:
                        self.assertIn(f"fromService: {{type: web, name: {api_name}, envVarKey: {key}}}", block)
