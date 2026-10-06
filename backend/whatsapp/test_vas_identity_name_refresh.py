"""Prembly ownership refresh repairs missing names without resetting identity."""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.models import IdentityProof, User, hash_identifier
from wallet.models import Wallet
from wema_vas.models import VirtualAccount
from wema_vas.test_enrollment import SETTINGS
from whatsapp import flows, router, vas_flow
from whatsapp.models import WhatsAppLink
from whatsapp.test_vas_flow import FLOW, WA


@override_settings(BANK_ACCOUNT_PROVIDER="wema_vas", WHATSAPP=WA,
                   WHATSAPP_FLOW=FLOW, RATELIMIT_ENABLE=False)
class VasIdentityNameRefreshTests(TestCase):
    def setUp(self):
        cache.clear()
        self.raw = "12345678901"
        self.msisdn = "2348012345678"
        self.digest = hash_identifier(self.raw)
        self.user = User.objects.create(
            username="name-refresh", phone="+" + self.msisdn, phone_verified=True,
            email="contact@example.test", email_verified=True,
            bvn_verified=True, bvn_hash=self.digest, bvn_last4=self.raw[-4:],
        )
        self.wallet = Wallet.objects.create(user=self.user, account_number="0123456789")
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=self.msisdn,
                                   status=WhatsAppLink.ACTIVE)
        self.legacy = IdentityProof.objects.create(
            user=self.user, identity_type="bvn", identity_hash=self.digest,
            source=IdentityProof.WEMA_FACE, verified_name=" \t\n\u2002 ",
        )
        settings = override_settings(WEMA_VAS={
            **SETTINGS, "MODE": "validation", "PREFIX": "711", "RELEASE_PHASE": "closed",
            "ENABLE_ENROLLMENT": False, "ENABLE_VALIDATION_ENROLLMENT": True,
            "VALIDATION_USER_IDS": [self.user.pk],
        })
        settings.enable()
        self.addCleanup(settings.disable)

    def exchange(self, token, data, screen):
        return flows.handle_flow_request({
            "flow_token": token, "action": "data_exchange", "screen": screen, "data": data,
        })

    def request_code(self):
        with patch("whatsapp.vas_flow.ready", return_value=True), \
                patch("whatsapp.providers.send_flow", return_value={"success": True}) as send:
            vas_flow.start(self.user, self.msisdn)
        token = send.call_args.args[1]
        self.assertEqual(self.exchange(token, {"consent": True, "identity_type": "bvn"},
                                       vas_flow.SETUP)["screen"], vas_flow.IDENTITY)
        with patch("utility.providers._prembly_identity_live", return_value=True), \
                patch("utility.providers.prembly_verify_bvn", return_value={
                    "success": True, "first_name": "Ada", "last_name": "Eze",
                    "phone": "08077778888",
                }) as lookup, patch.object(router, "sms_live", return_value=True), \
                patch.object(router, "send_sms", return_value={"success": True}) as sms:
            response = self.exchange(token, {"number": self.raw}, vas_flow.IDENTITY)
        lookup.assert_called_once()
        self.assertEqual(response["screen"], vas_flow.CODE)
        self.assert_identity_unchanged()
        return token, sms.call_args.args[1].split("Zitch: ")[1][:6]

    def assert_identity_unchanged(self):
        self.user.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)
        self.assertEqual(self.user.bvn_hash, self.digest)
        self.assertEqual(self.user.bvn_last4, self.raw[-4:])
        self.assertEqual(self.wallet.account_number, "0123456789")
        self.assertEqual(self.wallet.balance, 0)

    def test_whitespace_legacy_name_can_be_refreshed_then_enrolled(self):
        token, code = self.request_code()
        response = self.exchange(token, {"number": code}, vas_flow.CODE)
        self.assertEqual(response["screen"], vas_flow.REENTRY)
        proof = IdentityProof.objects.get(source=IdentityProof.IDENTITY_PROVIDER_OTP)
        self.assertEqual(proof.verified_name, "Ada Eze")
        self.legacy.refresh_from_db()
        self.assertEqual(self.legacy.verified_name, " \t\n\u2002 ")
        self.assert_identity_unchanged()
        response = self.exchange(token, {"number": self.raw}, vas_flow.REENTRY)
        self.assertEqual(response["data"]["status"], "Test setup complete")
        account = VirtualAccount.objects.get()
        self.assertEqual(account.display_name, "Zitch/Ada Eze")
        self.assertEqual(account.verification_reference, f"IdentityProof:{proof.pk}")
        self.assert_identity_unchanged()

    def test_substantive_conflicting_name_still_requires_review(self):
        token, code = self.request_code()
        IdentityProof.objects.filter(pk=self.legacy.pk).update(verified_name="Another Person")
        response = self.exchange(token, {"number": code}, vas_flow.CODE)
        self.assertEqual(response["data"]["status"], "Not completed")
        self.assertFalse(IdentityProof.objects.filter(source=IdentityProof.IDENTITY_PROVIDER_OTP).exists())
        self.assertFalse(VirtualAccount.objects.exists())
        self.assert_identity_unchanged()

    def test_equivalent_name_case_and_whitespace_do_not_create_a_conflict(self):
        token, code = self.request_code()
        IdentityProof.objects.filter(pk=self.legacy.pk).update(verified_name="  ADA\t Eze  ")
        response = self.exchange(token, {"number": code}, vas_flow.CODE)
        self.assertEqual(response["screen"], vas_flow.REENTRY)
        self.assertEqual(IdentityProof.objects.get(
            source=IdentityProof.IDENTITY_PROVIDER_OTP).verified_name, "Ada Eze")
        self.assert_identity_unchanged()
