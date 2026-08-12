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
    def test_interactive_verification_uses_real_sms_and_email_codes(self):
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

        with patch("whatsapp.router.send_sms", return_value={"success": True}) as sms, \
             patch("whatsapp.router.send_email", return_value={"success": True}) as email, \
             patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.send_flow", return_value={"success": True}):
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

            sms_code = sms.call_args.args[1].split("Zitch: ", 1)[1][:6]
            self.command(sms_code)

        self.user.refresh_from_db()
        self.assertTrue(self.user.phone_verified)
        self.assertFalse(self.user.email_verified)
        email.assert_called_once()
        self.assertEqual(email.call_args.args[0], self.user.email)
        pa.refresh_from_db()
        self.assertEqual(pa.state, FLOW_ID_STATE)
        self.assertEqual(pa.payload.get("id_kind"), "email")
        self.assertEqual(pa.payload.get("id_step"), "code")
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


@override_settings(WEMA=_SIM_ON, ALLOW_PRODUCTION_SIMULATION=True,
                   TESTING=False, DEBUG=False, TERMII=_REAL_TERMII)
class SimulatedIdentityLadderTests(TestCase):
    """The walkthrough as specified: BVN keeps its OTP round on the NEXT PAGE of
    the same flow, NIN verifies directly, both speak plainly ("Your BVN has been
    verified" — no simulation banner), and a completed ladder MINTS the personal
    account number."""

    def setUp(self):
        self.user = User.objects.create(
            username=PHONE, phone=PHONE, email="ladder@zitch.test",
            first_name="Ada", last_name="Test",
            phone_verified=True, email_verified=True)
        get_or_create_wallet(self.user)
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN,
                                    status=WhatsAppLink.ACTIVE)

    def _pa(self, kind):
        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="kyc", state=FLOW_ID_STATE,
            payload={"id_kind": kind, "attempted": ["phone", "email"]},
            expires_at=timezone.now() + timedelta(minutes=10))

    def _submit(self, pa, value):
        from .flows import handle_flow_request, sign_identity_token

        with patch("whatsapp.router.send_text", return_value={"success": True}), \
             patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.send_flow", return_value={"success": True}):
            return handle_flow_request({"action": "data_exchange",
                                        "flow_token": sign_identity_token(pa),
                                        "data": {"number": value}})

    def test_bvn_runs_the_otp_on_the_next_page_and_speaks_plainly(self):
        from .flows import IDENTITY_CHAIN, SUCCESS_SCREEN

        pa = self._pa("bvn")
        with patch("whatsapp.router.send_sms", return_value={"success": True}) as sms:
            resp = self._submit(pa, "22222222222")
        self.assertEqual(resp["screen"], IDENTITY_CHAIN)     # next page, same flow
        self.assertEqual(sms.call_args[0][0], PHONE)          # code to their line
        code = sms.call_args[0][1].split("Zitch: ")[1][:6]
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_verified)              # the code decides

        pa.refresh_from_db()
        done = self._submit(pa, code)
        self.assertEqual(done["screen"], SUCCESS_SCREEN)
        self.assertIn("Your BVN has been verified", done["data"]["message"])
        self.assertNotIn("simulation", done["data"]["message"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)

    def test_nin_verifies_directly_with_the_plain_message(self):
        pa = self._pa("nin")
        with patch("whatsapp.router.send_sms") as sms:
            self._submit(pa, "33333333333")
        sms.assert_not_called()                               # no OTP for NIN
        self.user.refresh_from_db()
        self.assertTrue(self.user.nin_verified)
        replies = "\n".join(WaMessageLog.objects.filter(
            msisdn=MSISDN, direction=WaMessageLog.OUT).values_list("text", flat=True))
        self.assertIn("Your NIN has been verified", replies)
        self.assertNotIn("in simulation", replies)

    def test_a_completed_ladder_mints_the_account_number(self):
        self.user.bvn_verified = True
        self.user.save(update_fields=["bvn_verified"])
        pa = self._pa("nin")
        with patch("whatsapp.router.send_sms"):
            self._submit(pa, "33333333333")
        wallet = get_or_create_wallet(self.user)
        wallet.refresh_from_db()
        self.assertTrue(wallet.account_number)                # minted
        replies = "\n".join(WaMessageLog.objects.filter(
            msisdn=MSISDN, direction=WaMessageLog.OUT).values_list("text", flat=True))
        self.assertIn("account number is ready", replies)
        self.assertIn(wallet.account_number, replies)
