"""Regression guard: retired 9PSB provider is not an active Zitch banking rail.

Historical migrations, immutable audit/export commands, and legacy bank alias
records must remain readable during reconciliation, but they are not callable
provider integrations or customer-facing 9PSB onboarding flows.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import Resolver404, resolve

from utility.providers import bank_account_provider


ROOT = Path(__file__).resolve().parents[2]
PROVIDER_FILES = (
    "backend/utility/providers.py",
    "backend/utility/wema.py",
    "backend/zitch_api/urls.py",
    "backend/zitch_api/settings.py",
    "backend/wallet/wema_callbacks.py",
    "backend/wallet/views.py",
    "backend/wallet/services.py",
    "backend/whatsapp/router.py",
    "backend/accounts/views.py",
)
RETIRED_RAIL = re.compile(
    r"(?i)(?:\b9psb\b|\b9paymentservicebank\b|"
    r"102\.216\.128\.75|/identity/api/v1/authenticate|/waas/api/)"
)


class Retired9PSBIntegrationContractTests(SimpleTestCase):
    def test_wema_partnership_is_the_only_active_account_provider(self):
        self.assertEqual(bank_account_provider(), "partnership")
        self.assertNotIn("banking", settings.INSTALLED_APPS)

    def test_old_provider_routes_are_not_registered(self):
        for url in ("/api/banking/accounts/", "/api/banking/kyc/",
                    "/webhooks/9psb/", "/api/9psb/"):
            with self.subTest(url=url):
                with self.assertRaises(Resolver404):
                    resolve(url)

    def test_core_provider_modules_have_no_9psb_calls(self):
        for path in PROVIDER_FILES:
            with self.subTest(path=path):
                text = (ROOT / path).read_text(encoding="utf-8")
                self.assertIsNone(
                    RETIRED_RAIL.search(text),
                    f"Retired-bank code unexpectedly appeared in live provider module: {path}")

    def test_client_apps_have_no_9psb_provider_refs(self):
        for dirname in ("app", "lib", "components"):
            for path in (ROOT / dirname).rglob("*"):
                if path.suffix not in (".ts", ".tsx", ".js", ".jsx"):
                    continue
                with self.subTest(path=path.relative_to(ROOT).as_posix()):
                    self.assertIsNone(
                        RETIRED_RAIL.search(path.read_text(encoding="utf-8")),
                        f"Retired-bank reference in client code: {path.relative_to(ROOT)}")

    def test_infrastructure_has_no_9psb_auth_configuration(self):
        for path in ("render.yaml", "render.frankfurt.yaml", "backend/.env.example"):
            with self.subTest(path=path):
                text = (ROOT / path).read_text(encoding="utf-8")
                self.assertNotRegex(text, r"(?i)9psb_(?:key|token|url|client|secret|endpoint)")
