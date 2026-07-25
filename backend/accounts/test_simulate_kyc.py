"""Tests for the TEST-ONLY simulated-KYC endpoint (/api/dev/simulate-kyc/).

Gated three ways — WEMA_SIMULATION on, a matching SIMULATE_DEPOSIT_TOKEN (shared
with simulate-deposit), and a valid tier for an existing user — so it can verify a
test user and provision a mock NUBAN for end-to-end testing, but can NEVER run on a
live-money deploy (simulation off => 404).
"""
import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from wallet.services import get_or_create_wallet

User = get_user_model()

_SIM_ON = {"SIMULATION": True}
_SIM_OFF = {"SIMULATION": False}
_TOKEN = "sim-secret-token"
_PHONE = "08160000001"


class SimulateKycTests(TestCase):
    def setUp(self):
        cache.clear()  # isolate the endpoint's rate-limit counter between tests
        self.client = Client()
        self.user = User.objects.create(username=_PHONE, phone=_PHONE)
        get_or_create_wallet(self.user)

    def _post(self, payload):
        res = self.client.post("/api/dev/simulate-kyc/",
                               data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    @override_settings(WEMA=_SIM_OFF, SIMULATE_DEPOSIT_TOKEN=_TOKEN)
    def test_not_found_when_simulation_off(self):
        # Even with the right token, a live-money deploy (simulation off) 404s and
        # changes nothing — it can never fabricate a verified identity.
        res, _ = self._post({"token": _TOKEN, "phone": _PHONE})
        self.assertEqual(res.status_code, 404)
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 0)

    @override_settings(WEMA=_SIM_ON, SIMULATE_DEPOSIT_TOKEN="")
    def test_forbidden_when_token_unset(self):
        res, _ = self._post({"token": "anything", "phone": _PHONE})
        self.assertEqual(res.status_code, 403)

    @override_settings(WEMA=_SIM_ON, SIMULATE_DEPOSIT_TOKEN=_TOKEN)
    def test_forbidden_on_wrong_token(self):
        res, _ = self._post({"token": "nope", "phone": _PHONE})
        self.assertEqual(res.status_code, 403)

    @override_settings(WEMA=_SIM_ON, SIMULATE_DEPOSIT_TOKEN=_TOKEN)
    def test_unknown_phone(self):
        res, _ = self._post({"token": _TOKEN, "phone": "08169999999"})
        self.assertEqual(res.status_code, 404)

    @override_settings(WEMA=_SIM_ON, SIMULATE_DEPOSIT_TOKEN=_TOKEN)
    def test_invalid_tier(self):
        for bad in (0, 4, "x"):
            res, _ = self._post({"token": _TOKEN, "phone": _PHONE, "tier": bad})
            self.assertEqual(res.status_code, 400)

    @override_settings(WEMA=_SIM_ON, SIMULATE_DEPOSIT_TOKEN=_TOKEN)
    def test_full_tier3_verifies_and_provisions(self):
        res, body = self._post({"token": _TOKEN, "phone": _PHONE, "tier": 3})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["tier"], 3)
        self.assertTrue(body["bvn_verified"] and body["nin_verified"]
                        and body["face_verified"] and body["address_verified"]
                        and body["id_document_verified"])
        self.assertTrue(body["account_number"])  # mock NUBAN provisioned
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 3)
        self.assertTrue(get_or_create_wallet(self.user).account_number)

    @override_settings(WEMA=_SIM_ON, SIMULATE_DEPOSIT_TOKEN=_TOKEN)
    def test_tier1_sets_only_bvn_and_nin(self):
        res, body = self._post({"token": _TOKEN, "phone": _PHONE, "tier": 1})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["tier"], 1)
        self.assertTrue(body["bvn_verified"] and body["nin_verified"])
        self.assertFalse(body["face_verified"] or body["address_verified"]
                         or body["id_document_verified"])

    @override_settings(WEMA=_SIM_ON, SIMULATE_DEPOSIT_TOKEN=_TOKEN)
    def test_default_tier_is_3(self):
        _, body = self._post({"token": _TOKEN, "phone": _PHONE})
        self.assertEqual(body["tier"], 3)

    @override_settings(WEMA=_SIM_ON, SIMULATE_DEPOSIT_TOKEN=_TOKEN)
    def test_idempotent_account_number(self):
        _, b1 = self._post({"token": _TOKEN, "phone": _PHONE})
        _, b2 = self._post({"token": _TOKEN, "phone": _PHONE})
        self.assertTrue(b1["account_number"])
        self.assertEqual(b1["account_number"], b2["account_number"])
