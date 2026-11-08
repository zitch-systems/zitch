"""Safety invariants for the proposed consolidation of existing Render resources.

The files are also validated against Render's published JSON schema before a
cutover. These checks deliberately parse only this repository's simple block
layout, without adding a YAML dependency to the banking runtime.
"""
import re
from pathlib import Path
from django.test import SimpleTestCase

BLUEPRINT = Path(__file__).resolve().parents[2] / "render.yaml"
API = "zitch-api-ry6y"
WORKER = "zitch-whatsapp-worker-ry6y"
MAINTENANCE = "zitch-maintenance-ry6y"


def service_blocks(text):
    return {re.search(r"^  name: (.+)$", block, re.M).group(1): block
        for block in re.findall(r"^- type: .+?(?=^- type: |^databases:|\Z)", text, re.M | re.S)}


def env(block, key):
    result = re.search(r"^  - key: " + re.escape(key) + r"\n(.*?)(?=^  - key: |\Z)", block, re.M | re.S)
    return result.group(1) if result else ""


def scalar(block, key):
    result = re.search(r"^  " + re.escape(key) + r": ([^\n]+(?:\n    [^\n]+)*)", block, re.M)
    return " ".join(result.group(1).split()).strip("\"'") if result else ""


class RenderBlueprintSafetyTests(SimpleTestCase):
    def blueprints(self):
        for filename in ("render.yaml", "render.frankfurt.yaml"):
            text = BLUEPRINT.with_name(filename).read_text()
            services = service_blocks(text)
            self.api_source = services[API]
            yield filename, text, services

    def inherited(self, block, key):
        self.assertTrue(env(self.api_source, key), "Missing API source: " + key)
        value = env(block, key)
        self.assertIn("fromService:", value, key)
        self.assertIn("name: " + API, value, key)
        self.assertIn("envVarKey: " + key, value, key)
        self.assertNotIn("value:", value, key)

    def test_adopts_existing_resources_without_duplicate_api_worker_or_cache(self):
        for filename, text, services in self.blueprints():
            with self.subTest(filename=filename):
                self.assertEqual(set(services), {API, WORKER, MAINTENANCE, "zitch-cache-ry6y"})
                self.assertIn("- name: zitch-db-ry6y", text)
                self.assertIn("Blueprint auto-sync remains paused", text)
                self.assertIn("docs/render-maintenance-cutover.md", text)

    def test_every_deployable_service_waits_for_checks_and_uses_paid_compute(self):
        for _, _, services in self.blueprints():
            for name in (API, WORKER, MAINTENANCE):
                self.assertEqual(scalar(services[name], "autoDeployTrigger"), "checksPass")
                self.assertEqual(scalar(services[name], "plan"), "starter")
            self.assertEqual(scalar(services[API], "healthCheckPath"), "/readyz")

    def test_api_alone_generates_the_shared_session_secret(self):
        for _, text, services in self.blueprints():
            self.assertEqual(text.count("- key: DJANGO_SECRET_KEY"), 3)
            self.assertIn("generateValue: true", env(services[API], "DJANGO_SECRET_KEY"))
            for name in (WORKER, MAINTENANCE):
                self.inherited(services[name], "DJANGO_SECRET_KEY")

    def test_only_api_build_migrates_and_other_processes_wait_and_recheck(self):
        for _, _, services in self.blueprints():
            self.assertEqual(scalar(services[API], "buildCommand"), "./build.sh")
            for name in (WORKER, MAINTENANCE):
                self.assertEqual(scalar(services[name], "buildCommand"),
                    "pip install -r requirements.txt && python manage.py wait_for_migrations --timeout 600 --interval 5")
                self.assertTrue(scalar(services[name], "startCommand").startswith("python manage.py migrate --check && "))

    def test_only_worker_carries_whatsapp_delivery_credentials(self):
        keys = ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_APP_SECRET",
            "WHATSAPP_QUEUE_KEY", "WHATSAPP_QUEUE_KEY_PREV", "WHATSAPP_FLOW_PRIVATE_KEY")
        for _, _, services in self.blueprints():
            for key in keys:
                self.inherited(services[WORKER], key)
                self.assertFalse(env(services[MAINTENANCE], key), key)
            self.assertIn("value: 'false'", env(services[MAINTENANCE], "TXN_ALERTS_WHATSAPP"))
            self.assertIn("sync: false", env(services[API], "WHATSAPP_QUEUE_KEY_PREV"))

    def test_notification_writers_share_email_sms_configuration(self):
        keys = ("RESEND_API_KEY", "RESEND_FROM_EMAIL", "TERMII_API_KEY", "TERMII_SENDER_ID",
                "TERMII_CHANNEL", "TERMII_BASE_URL", "TXN_ALERTS_EMAIL", "TXN_ALERTS_SMS", "TXN_ALERTS_PUSH")
        for _, _, services in self.blueprints():
            for name in (WORKER, MAINTENANCE):
                for key in keys:
                    self.inherited(services[name], key)

    def test_bank_configuration_and_encryption_are_shared_by_all_consumers(self):
        for _, _, services in self.blueprints():
            keys = re.findall(r"^  - key: (NINEPSB_[A-Z_]+)$", services[API], re.M)
            self.assertGreater(len(keys), 10)
            for name in (WORKER, MAINTENANCE):
                for key in keys + ["BANKING_ENCRYPTION_KEYS", "PREMBLY_API_KEY"]:
                    self.inherited(services[name], key)
                self.assertIn("value: 'true'", env(services[name], "DJANGO_REQUIRE_SHARED_CACHE"))

    def test_one_durable_scheduler_owns_reconciliation_and_preserves_controls(self):
        from banking.maintenance import JOBS
        for _, text, services in self.blueprints():
            self.assertEqual(text.count("- type: cron"), 1)
            self.assertEqual(scalar(services[MAINTENANCE], "schedule"), "*/5 * * * *")
            self.assertTrue(scalar(services[MAINTENANCE], "startCommand").endswith("manage.py run_maintenance"))
            self.assertIn("value: 'false'", env(services[WORKER], "WHATSAPP_WORKER_RECONCILE"))
        controls = {name: (schedule, options) for name, schedule, options in JOBS}
        self.assertEqual(len(controls), 7)
        self.assertEqual(controls["run_maturities"][0], (1, 5))
        self.assertEqual(controls["settlement_report"], ((6, 15), {"fail_on_breach": True}))
        self.assertEqual(controls["integrity_check"][1], {"fail_nonzero": True})
        self.assertEqual(controls["aml_scan"][1], {"fail_on_overdue": True})

    def test_retired_credentials_and_prembly_face_sdk_are_absent(self):
        for _, text, _ in self.blueprints():
            self.assertNotIn("WEMA_", text)
            self.assertNotIn("PREMBLY_FACE_", text)
            self.assertNotIn("WHATSAPP_FLOW_VAS_", text)
