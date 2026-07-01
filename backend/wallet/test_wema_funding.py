"""Tests for Wema/ALAT wallet funding — the OTP provisioning endpoints and the
credit-reconciliation poller (ALAT has no inbound-credit webhook).

- Provisioning: create -> verify-otp mints + persists a NUBAN with a WEMA
  account_reference (mock flow, PAYMENT_PROVIDER=wema).
- Reconcile: reconcile_wema credits an inbound (creditType=='Credit') deposit
  once, is idempotent across re-polls, and ignores debits / zero rows.
"""
import json
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from wallet.models import Transaction, Wallet
from wallet.services import apply_wema_credit, wema_account_reference
from wallet.tests import make_user


def _tx(ref, amount, credit=True, **extra):
    return {"referenceId": ref, "amount": amount,
            "creditType": "Credit" if credit else "Debit",
            "narration": extra.get("narration", "Transfer in"),
            "sender": extra.get("sender", "GTBANK / JOHN")}


@override_settings(PAYMENT_PROVIDER="wema")
class WemaWalletProvisioningTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08030000123", "w@zitch.app")

    def _post(self, path, payload):
        return self.client.post(path, data=json.dumps({**payload, "access_token": self.token}),
                                content_type="application/json")

    def test_otp_flow_provisions_wema_account(self):
        r1 = self._post("/api/wallet/wema/create/", {"bvn": "22222222222"})
        self.assertEqual(r1.status_code, 200)
        b1 = r1.json()
        self.assertTrue(b1["success"])
        self.assertTrue(b1["tracking_id"])
        self.assertTrue(b1["using_bvn"])

        r2 = self._post("/api/wallet/wema/verify-otp/",
                        {"otp": "123456", "tracking_id": b1["tracking_id"],
                         "using_bvn": True, "bvn": "22222222222"})
        self.assertEqual(r2.status_code, 200)
        b2 = r2.json()
        self.assertTrue(b2["success"])
        self.assertTrue(b2["account_number"])

        w = Wallet.objects.get(user=self.user)
        self.assertEqual(w.account_number, b2["account_number"])
        self.assertEqual(w.account_reference, wema_account_reference(self.user))
        # Echoing the BVN lifts KYC / tier, mirroring the Kora account flow.
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)

    def test_verify_requires_otp_and_tracking(self):
        r = self._post("/api/wallet/wema/verify-otp/", {"otp": "", "tracking_id": ""})
        self.assertEqual(r.status_code, 400)
        self.assertIn("OTP", r.json()["message"])

    def test_resend_otp_ok(self):
        r = self._post("/api/wallet/wema/resend-otp/", {"tracking_id": "WEMA-SIM-abc", "using_bvn": True})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["success"])


class WemaReconcileTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08030000999", "r@zitch.app")
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.account_number = "0155500011"
        self.wallet.account_reference = wema_account_reference(self.user)
        self.wallet.bank_name = "Wema Bank"
        self.wallet.save(update_fields=["account_number", "account_reference", "bank_name"])

    def _run(self, txns):
        with patch("utility.wema.get_transactions",
                   return_value={"success": True, "transactions": txns}):
            call_command("reconcile_wema")

    def test_credits_inbound_deposit_once(self):
        self._run([_tx("WEMA-DEP-1", 2500)])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("2500.00"))
        self.assertTrue(Transaction.objects.filter(reference="WEMA-DEP-1",
                                                   direction=Transaction.IN).exists())
        # Re-poll the same window: idempotent on referenceId, no double-credit.
        self._run([_tx("WEMA-DEP-1", 2500)])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("2500.00"))

    def test_ignores_debits_and_zero_rows(self):
        self._run([_tx("WEMA-OUT-1", 1000, credit=False), _tx("WEMA-ZERO-1", 0)])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("0.00"))

    def test_apply_wema_credit_skips_non_credit(self):
        self.assertIsNone(apply_wema_credit(self.wallet, _tx("X", 500, credit=False)))
        self.assertIsNone(apply_wema_credit(self.wallet, {"referenceId": "", "amount": 500,
                                                          "creditType": "Credit"}))
