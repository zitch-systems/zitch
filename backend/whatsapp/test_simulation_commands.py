"""No-CLI WhatsApp commands for a deliberately simulated deployment.

The linked WhatsApp number identifies the only account the commands may change.
Both simulation switches must be on; turning WEMA_SIMULATION off makes every
command inert. No real identity number is accepted or stored.
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
