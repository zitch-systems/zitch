"""Card readiness must describe the rail dispatch will actually use."""
from django.test import Client, TestCase, override_settings


class CardReadinessTests(TestCase):
    def card_readiness(self, card_issuer):
        with override_settings(
            PUBLIC_HEALTH_DETAILS=True,
            CARD_PROVIDER="issuer",
            CARD_ISSUER=card_issuer,
            WEMA={"SIMULATION": False},
        ):
            return Client().get("/healthz").json()["integrations"]["cards_issuer"]

    def test_key_without_live_gate_is_not_reported_ready(self):
        self.assertFalse(self.card_readiness({
            "API_KEY": "issuer-key",
            "BASE_URL": "https://issuer.invalid",
            "LIVE_ENABLED": False,
        }))

    def test_key_without_base_url_is_not_reported_ready(self):
        self.assertFalse(self.card_readiness({
            "API_KEY": "issuer-key",
            "BASE_URL": "",
            "LIVE_ENABLED": True,
        }))

    def test_gate_key_and_base_url_are_reported_ready(self):
        self.assertTrue(self.card_readiness({
            "API_KEY": "issuer-key",
            "BASE_URL": "https://issuer.invalid",
            "LIVE_ENABLED": True,
        }))
