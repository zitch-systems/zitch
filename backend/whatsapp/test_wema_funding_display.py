"""Funding instructions may only advertise a currently usable Partnership NUBAN.

Historical 9PSB-era numbers may remain on Wallet for audit/reconciliation. The
WhatsApp Add Money flow must never send those numbers or suggest empty deposits.
"""
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from whatsapp import router


class WemaWhatsAppFundingSafetyTests(SimpleTestCase):
    def _message(self, funding):
        wallet = Mock()
        wallet.user.pk = 101
        with patch.object(router, "customer_funding_account", return_value=funding), \
             patch.object(router, "reply") as reply:
            router._send_account_details("recipient-test", wallet)
        reply.assert_called_once()
        return reply.call_args.args[1]

    def test_unavailable_cutover_does_not_advertise_stale_account(self):
        msg = self._message({
            "account_setup_state": "partnership_unavailable", "can_fund": False,
            "account_number": "", "bank_name": "", "bank_accounts": [],
        })
        self.assertIn("temporarily unavailable", msg)
        self.assertNotIn("Transfer to your dedicated Zitch account", msg)
        self.assertNotIn("credited automatically", msg)

    def test_legacy_9psb_account_is_not_exposed(self):
        msg = self._message({
            "account_setup_state": "legacy_funding_review", "can_fund": False,
            "migration_message": "Historical funding is under review.",
            "account_number": "", "bank_name": "", "bank_accounts": [],
        })
        self.assertEqual(msg, "Historical funding is under review.")
        self.assertNotIn("Transfer to your dedicated Zitch account", msg)

    def test_no_available_account_cannot_produce_empty_deposit_instructions(self):
        msg = self._message({
            "account_setup_state": "identity_required", "can_fund": False,
            "account_number": "", "bank_name": "", "bank_accounts": [],
        })
        self.assertIn("not ready yet", msg)
        self.assertNotIn("Transfer to your dedicated Zitch account", msg)

    def test_verified_wema_account_still_shows_bank_instructions(self):
        msg = self._message({
            "account_setup_state": "ready", "can_fund": True,
            "account_number": "0123456789", "bank_name": "Wema Bank",
            "account_name": "TEST CUSTOMER", "bank_accounts": [{
                "account_number": "0123456789", "bank_name": "Wema Bank",
                "account_name": "TEST CUSTOMER",
            }],
        })
        self.assertIn("0123456789", msg)
        self.assertIn("Wema Bank", msg)
        self.assertIn("Transfer to your dedicated Zitch account", msg)
