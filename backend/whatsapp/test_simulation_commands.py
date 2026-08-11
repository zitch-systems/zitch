"""No-CLI WhatsApp commands for a deliberately simulated deployment.

The linked WhatsApp number identifies the only account the commands may change.
Both simulation switches must be on; turning WEMA_SIMULATION off makes every
command inert. Interactive test digits never leave Zitch and are not used to
derive the identity hashes stored on the account.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from wallet.models import Transaction
from wallet.services import get_or_create_wallet

from .flows import FLOW_ID_STATE
from .models import PendingAction, WaMessageLog, WhatsAppLink
from .router import handle_inbound


User = get_user_model()
MSISDN = "2348012345678"
PHONE = "08012345678"
_SIM_ON = {"SIMULATION": True}
_SIM_OFF = {"SIMULATION": False}
_REAL_TERMII = {
    "BASE_URL": "https://v3.api.termii.com",
    "API_KEY": "termii-live",
    "SENDER_ID": "Zitch",
    "CHANNEL": "dnd",
}
_REAL_RESEND = {
    "BASE_URL": "https://api.resend.com",
    "API_KEY": "resend-live",
    "FROM_EMAIL": "Zitch <no-reply@send.zitch.ng>",
}
_NO_TEST_OTP = {"PHONE": "", "CODE": ""}


class WhatsAppSimulationCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username=PHONE,
            phone=PHONE,
            email="simulation@zitch.test",
            first_name="Ada",
            last_name="Test",
        )
        get_or_create_wallet(self.user)
        WhatsAppLink.objects.create(
            user=self.user,
            wa_msisdn=MSISDN,
            status=WhatsAppLink.ACTIVE,
        )

    def command(self, text):
        with patch("whatsapp.router.send_text", return_value={"success": True}):
            handle_inbound(MSISDN, text)

    def last_reply(self):
        row = WaMessageLog.objects.filter(
            msisdn=MSISDN, direction=WaMessageLog.OUT
        ).order_by("-created").first()
        return row.text if row else ""

    @override_settings(WEMA=_SIM_ON, ALLOW_PRODUCTION_SIMULATION=True)
    def test_setup_persists_full_kyc_account_and_mock_balance(self):
        self.command("simulate setup")

        self.user.refresh_from_db()
        wallet = get_or_create_wallet(self.user)
        self.assertEqual(self.user.tier, 3)
        self.assertTrue(
            self.user.phone_verified
            and self.user.email_verified
            and self.user.bvn_verified
            and self.user.nin_verified
            and self.user.face_verified
            and self.user.address_verified
            and self.user.id_document_verified
        )
        self.assertTrue(wallet.account_number)
        self.assertEqual(wallet.balance, Decimal("50000"))
        self.assertTrue(
            Transaction.objects.filter(
                user=self.user, reference__startswith="WEMA-CR-SIM-"
            ).exists()
        )
        self.assertIn("Simulation ready", self.last_reply())

    @override_settings(WEMA=_SIM_ON, ALLOW_PRODUCTION_SIMULATION=True)
    def test_setup_is_idempotent_and_escapes_open_bvn_flow(self):
        PendingAction.objects.create(
            user=self.user,
            msisdn=MSISDN,
            action_type="kyc",
            state=FLOW_ID_STATE,
            payload={"id_kind": "bvn"},
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        self.command("simulate setup")
        first_account = get_or_create_wallet(self.user).account_number
        self.command("simulate setup")

        wallet = get_or_create_wallet(self.user)
        self.assertFalse(PendingAction.objects.filter(msisdn=MSISDN).exists())
        self.assertEqual(wallet.balance, Decimal("50000"))
        self.assertEqual(wallet.account_number, first_account)
        self.assertEqual(
            Transaction.objects.filter(
                user=self.user, reference__startswith="WEMA-CR-SIM-"
            ).count(),
            1,
        )

    @override_settings(
        WEMA=_SIM_ON,
        ALLOW_PRODUCTION_SIMULATION=True,
        TERMII=_REAL_TERMII,
        RESEND=_REAL_RESEND,
        TEST_OTP=_NO_TEST_OTP,
    )
    def test_interactive_verification_resets_flags_and_sends_a_real_sms_code(self):
        self.user.phone_verified = True
        self.user.email_verified = True
        self.user.bvn_verified = True
        self.user.nin_verified = True
        self.user.tier = 1
        self.user.set_bvn("22222222222")
        self.user.set_nin("33333333333")
        self.user.save(update_fields=[
            "phone_verified", "email_verified", "bvn_verified", "nin_verified",
            "tier", "bvn_hash", "bvn_last4", "nin_hash", "nin_last4",
        ])

        with patch("whatsapp.router.send_sms", return_value={"success": True}) as sms:
            self.command("simulate verification")

        self.user.refresh_from_db()
        self.assertFalse(self.user.phone_verified)
        self.assertFalse(self.user.email_verified)
        self.assertFalse(self.user.bvn_verified)
        self.assertFalse(self.user.nin_verified)
        self.assertFalse(self.user.bvn_hash)
        self.assertFalse(self.user.nin_hash)
        self.assertEqual(self.user.tier, 0)
        sms.assert_called_once()
        self.assertEqual(sms.call_args.args[0], PHONE)
        pa = PendingAction.objects.get(msisdn=MSISDN)
        self.assertEqual(pa.state, "phone")
        self.assertTrue(pa.payload.get("code_hash"))
        replies = "\n".join(WaMessageLog.objects.filter(
            msisdn=MSISDN, direction=WaMessageLog.OUT
        ).values_list("text", flat=True))
        self.assertIn("Interactive verification ready", replies)
        self.assertIn("real delivery through Termii", replies)
        self.assertIn("real delivery through Resend", replies)

    @override_settings(WEMA=_SIM_ON, ALLOW_PRODUCTION_SIMULATION=True)
    def test_explicit_simulated_deposit_accepts_compact_amount(self):
        self.command("simulate deposit 5k")
        self.assertEqual(
            get_or_create_wallet(self.user).balance,
            Decimal("5000"),
        )
        self.assertIn("Simulated deposit credited", self.last_reply())

    @override_settings(
        WEMA=_SIM_ON,
        ALLOW_PRODUCTION_SIMULATION=False,
        DEBUG=False,
        TESTING=False,
    )
    def test_second_switch_is_required_on_production_host(self):
        self.command("simulate setup")
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 0)
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("0"))
        self.assertIn("disabled", self.last_reply().lower())

    @override_settings(WEMA=_SIM_OFF, ALLOW_PRODUCTION_SIMULATION=True)
    def test_live_money_mode_makes_commands_inert(self):
        self.command("simulate deposit 50000")
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("0"))
        self.assertIn("disabled", self.last_reply().lower())
