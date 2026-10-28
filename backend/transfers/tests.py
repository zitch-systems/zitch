"""Tests for bank transfer (payout) + saved beneficiaries."""
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase, override_settings

from wallet.models import Transaction
from wallet.services import get_or_create_wallet
from wallet.tests import make_user as make_unbanked_user
from banking.test_fixtures import make_bank_user as make_user
from banking.test_client import CONFIG
from banking.client import Reply, ProviderError
from banking.models import BankAccount, Instruction

from .models import Bank, Beneficiary


@override_settings(NINEPSB={**CONFIG, "TRANSFER_FEE_TOTAL": "0.00"})
class BankIOCase(TestCase):
    """Exercise application controls with only the bank HTTP boundary replaced."""
    def setUp(self):
        super().setUp()
        def wallet(number):
            bank = BankAccount.objects.get(number=number)
            return Reply({"status": "SUCCESS", "data": {
                "number": "INTERNAL-WALLET", "nuban": bank.number, "name": bank.name,
                "status": "Active", "tier": str(bank.tier), "isSuccessful": True,
                "pndstatus": "Active" if bank.pnd else "InActive",
                "freezeStatus": "Active" if bank.frozen else "InActive",
                "availableBalance": str(bank.available_balance),
                "ledgerBalance": str(bank.ledger_balance)}}, 200)
        def resolve(number, code):
            return Reply({"code": "00", "customer": {"account": {
                "number": number, "bank": code,
                "name": "MUSA ADAMU" if number == "0777777777" else "JOHN DOE"}}}, 200)
        self.wallet_read = self.enterContext(patch("banking.client.Client.wallet", side_effect=wallet))
        self.enquiry = self.enterContext(patch("banking.client.Client.resolve", side_effect=resolve))
        self.bank_send = self.enterContext(patch("banking.client.Client.transfer",
            return_value=Reply({"status": "SUCCESS", "responseCode": "00"}, 200)))


class BankTransferTests(BankIOCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user, self.token = make_user("08010000001", "ada@zitch.test", balance="50000")
        self.bank = Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#E32119")
        self._key_seq = 0

    def post(self, path, payload):
        payload = dict(payload)
        if path == "/api/transfers/send/":
            payload.setdefault("expected_fee", "0.00")
        if path == "/api/transfers/send/" and "idempotency_key" not in payload:
            self._key_seq += 1
            payload["idempotency_key"] = f"bank-test-{self._key_seq}"
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def test_send_requires_a_client_idempotency_key(self):
        with patch("transfers.views.payout_resolve_account") as resolve:
            res, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789",
                "bank": "gtb", "name": "John Doe", "amount": "1000",
                "transaction_pin": "1234", "idempotency_key": None,
            })
        self.assertEqual(res.status_code, 400)
        self.assertEqual(body.get("code"), "idempotency_key_required")
        resolve.assert_not_called()
        self.assertEqual(self.balance(), Decimal("50000"))

    def balance(self):
        return get_or_create_wallet(self.user).balance

    def test_malformed_account_numbers_are_rejected_before_name_enquiry(self):
        for account in (1234567890, [], {}, "abcdefghij", "٠١٢٣٤٥٦٧٨٩"):
            for path in ("/api/transfers/resolve/", "/api/transfers/send/"):
                with self.subTest(account=account, path=path), patch(
                        "transfers.views.payout_resolve_account") as resolve:
                    res, _body = self.post(path, {
                        "access_token": self.token, "account_number": account,
                        "bank": "gtb", "name": "John Doe", "amount": "1000",
                        "transaction_pin": "1234", "expected_fee": "0.00",
                    })
                    self.assertEqual(res.status_code, 400)
                    resolve.assert_not_called()
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_inactive_bank_cannot_be_used_for_new_enquiry_or_transfer(self):
        self.bank.active = False
        self.bank.save(update_fields=["active"])
        for path in ("/api/transfers/resolve/", "/api/transfers/send/"):
            with self.subTest(path=path), patch("transfers.views.payout_resolve_account") as resolve:
                res, _body = self.post(path, {
                    "access_token": self.token, "account_number": "0123456789",
                    "bank": "gtb", "name": "John Doe", "amount": "1000",
                    "transaction_pin": "1234", "expected_fee": "0.00",
                })
                self.assertEqual(res.status_code, 404)
                resolve.assert_not_called()
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_transfer_charge_returns_nip_fee(self):
        from decimal import Decimal
        with patch("transfers.views.payout_charge", return_value=Decimal("25.00")):
            res, body = self.post("/api/transfers/charge/", {"access_token": self.token, "amount": "20000"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["fee"], "25.00")

    def test_transfer_charge_is_retryable_when_schedule_unavailable(self):
        with patch("transfers.views.payout_charge", return_value=None):
            res, body = self.post("/api/transfers/charge/", {"access_token": self.token, "amount": "20000"})
        self.assertEqual(res.status_code, 503)
        self.assertFalse(body["success"])
        self.assertEqual(body["code"], "transfer_fee_unavailable")
        self.assertTrue(body["retryable"])
        self.assertNotIn("fee", body)
        self.assertEqual(self.balance(), Decimal("50000"))
        self.assertFalse(Transaction.objects.filter(user=self.user, direction=Transaction.OUT).exists())

    def test_transfer_charge_preserves_confirmed_zero_fee(self):
        with patch("transfers.views.payout_charge", return_value=Decimal("0.00")):
            res, body = self.post("/api/transfers/charge/", {"access_token": self.token, "amount": "20000"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(body["fee"], "0.00")

    def test_transfer_below_50_rejected(self):
        res, body = self.post("/api/transfers/send/", {
            "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
            "name": "ADEYEMI WILLIAM", "amount": "40", "transaction_pin": "1234", "expected_fee": "0.00",
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("50", body.get("message", ""))
        self.assertEqual(self.balance(), Decimal("50000"))  # nothing moved

    def test_live_resolution_blocks_name_mismatch(self):
        # With a LIVE name enquiry, an account whose real holder differs from the
        # name the user confirmed must be BLOCKED â€” no debit. (Guards the reported
        # "account mismatch but the transfer went through".)
        with patch("transfers.views.payout_resolve_account",
                   return_value={"success": True, "name": "JANE SMITH"}):
            res, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234", "expected_fee": "0.00",
            })
        self.assertEqual(res.status_code, 409)
        self.assertEqual(body.get("code"), "account_mismatch")
        self.assertEqual(body.get("resolved_name"), "JANE SMITH")
        self.assertEqual(self.balance(), Decimal("50000"))  # untouched

    def test_live_resolution_allows_matching_name(self):
        # The same holder (tolerant of word order) goes through and debits.
        with patch("transfers.views.payout_resolve_account",
                   return_value={"success": True, "name": "DOE JOHN"}):
            res, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234", "expected_fee": "0.00",
            })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body.get("success") or body.get("pending"))
        self.assertEqual(self.balance(), Decimal("40000"))

    def test_banks_listed(self):
        res, body = self.post("/api/transfers/banks/", {})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["banks"][0]["code"], "gtb")
        # The picker renders a hosted logo (with a monogram fallback), so the
        # payload must always carry the field â€” blank when unset.
        self.assertIn("logo", body["banks"][0])

    def test_detection_never_sweeps_paid_name_enquiries(self):
        from transfers.services import detect_account_banks
        Bank.objects.create(code="acc", name="Access Bank", bank_code="044", popular=True)
        self.assertEqual(detect_account_banks("0000000001"), [])
        self.enquiry.assert_not_called()

    def test_resolve_requires_10_digits(self):
        res, _ = self.post("/api/transfers/resolve/", {"access_token": self.token, "account_number": "123", "bank": "gtb"})
        self.assertEqual(res.status_code, 400)

    def test_resolve_returns_name(self):
        res, body = self.post("/api/transfers/resolve/", {"access_token": self.token, "account_number": "0123456789", "bank": "gtb"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["name"])
        self.assertEqual(body["bank"], "gtb")  # echoes the bank back

    def test_resolve_requires_a_selected_bank_without_paid_detection(self):
        res, body = self.post("/api/transfers/resolve/", {
            "access_token": self.token, "account_number": "0123456789"})
        self.assertEqual(res.status_code, 404)
        self.assertIn("pick the bank", body["message"])
        self.enquiry.assert_not_called()

    def test_failed_name_enquiry_never_returns_a_placeholder_holder(self):
        with patch("banking.client.Client.resolve", side_effect=ProviderError("network_unavailable")):
            res, body = self.post("/api/transfers/resolve/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb"})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(body.get("success"))
        self.assertNotIn("name", body)

    def test_send_debits_and_saves_beneficiary(self):
        res, body = self.post("/api/transfers/send/", {
            "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
            "name": "John Doe", "amount": "10000", "transaction_pin": "1234", "expected_fee": "0.00",
        })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(self.balance(), Decimal("40000"))
        # Ledger row settled, beneficiary auto-saved & deduped.
        self.assertEqual(Transaction.objects.get(reference=body["reference"]).transaction_status, Transaction.SUCCESS)
        self.assertEqual(Beneficiary.objects.filter(user=self.user, account_number="0123456789").count(), 1)

    def test_send_dedupes_beneficiary_on_repeat(self):
        payload = {"access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                   "name": "John Doe", "amount": "5000", "transaction_pin": "1234"}
        self.post("/api/transfers/send/", payload)
        self.post("/api/transfers/send/", payload)
        self.assertEqual(Beneficiary.objects.filter(user=self.user, account_number="0123456789").count(), 1)

    def test_send_rejects_wrong_pin(self):
        res, _ = self.post("/api/transfers/send/", {
            "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
            "name": "John Doe", "amount": "10000", "transaction_pin": "0000",
        })
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_send_rejects_insufficient(self):
        # Fresh user with 30k balance: 40k is within the tier-1 limit (50k) but
        # over balance, so this hits the insufficient-funds path, not the limit.
        poor, token = make_user("08090000009", "poor@zitch.test", balance="30000")
        res, _ = self.post("/api/transfers/send/", {
            "access_token": token, "account_number": "0123456789", "bank": "gtb",
            "name": "John Doe", "amount": "40000", "transaction_pin": "1234", "expected_fee": "0.00",
        })
        self.assertEqual(res.status_code, 402)
        self.assertEqual(get_or_create_wallet(poor).balance, Decimal("30000"))

    def test_send_enforces_tier_limit(self):
        # Tier 1 ceiling is 50,000; a 60,000 payout is blocked before any debit.
        res, body = self.post("/api/transfers/send/", {
            "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
            "name": "John Doe", "amount": "60000", "transaction_pin": "1234", "expected_fee": "0.00",
        })
        self.assertEqual(res.status_code, 403)
        self.assertEqual(body["code"], "limit_exceeded")
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_pending_payout_is_not_settled(self):
        # A rail that returns PENDING (queued / awaiting auth) must NOT be settled
        # as Successful â€” the row stays Pending (money debited, flagged for the
        # webhook) and the response says "processing", not "sent".
        with patch("banking.client.Client.transfer",
                   return_value=Reply({"status": "PENDING", "responseCode": "09"}, 200)):
            res, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234", "expected_fee": "0.00",
            })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body.get("pending"))
        self.assertFalse(body.get("success"))
        txn = Transaction.objects.get(reference=body["reference"])
        self.assertEqual(txn.transaction_status, Transaction.PENDING)
        self.assertTrue(txn.meta.get("reconcile"))
        self.assertEqual(self.balance(), Decimal("40000"))  # debited, not refunded

    def test_pending_payout_excluded_from_vtu_reconcile_sweep(self):
        """Regression: a PENDING bank payout shares the reconcile+OUT shape with a
        VAS purchase, but must not enter a VAS sweep. The durable bank instruction
        records the product and is requeried through the WaaS endpoint."""
        from datetime import timedelta

        from django.utils import timezone

        from wallet.services import credit, debit, pending_vas_purchases

        # A PENDING bank payout (what execute_payout leaves on a rail 'PENDING').
        with patch("banking.client.Client.transfer",
                   return_value=Reply({"status": "PENDING", "responseCode": "09"}, 200)):
            _, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234", "expected_fee": "0.00",
            })
        payout = Transaction.objects.get(reference=body["reference"])

        # A PENDING VAS purchase (reconcile, no bank meta) for contrast.
        credit(self.user, Decimal("1000"), "Seed")
        vtu = debit(self.user, Decimal("500"), "Airtime",
                    meta={"phone": "08010000001", "reconcile": True})

        cutoff = timezone.now() + timedelta(minutes=1)  # both are "old enough"
        swept = set(pending_vas_purchases(cutoff).values_list("reference", flat=True))
        self.assertIn(vtu.reference, swept)         # VAS purchase is reconciled
        self.assertNotIn(payout.reference, swept)   # bank payout is not

        self.assertEqual(self.balance(), Decimal("40500"))  # 50000 - 10000 payout + 1000 seed - 500 vtu

    def test_send_refunds_when_payout_fails(self):
        """If the payout provider declines, the wallet debit must be reversed."""
        with patch("banking.client.Client.transfer",
                   return_value=Reply({"status": "FAILED", "responseCode": "51"}, 200)):
            res, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234", "expected_fee": "0.00",
            })
        self.assertEqual(res.status_code, 422)
        self.assertEqual(body.get("code"), "transfer_failed")
        self.assertTrue(body.get("refunded"))
        self.assertTrue(body.get("reference"))
        self.assertEqual(self.balance(), Decimal("50000"))  # fully refunded
        self.assertTrue(Transaction.objects.filter(user=self.user, transaction_status=Transaction.FAILED).exists())
        # A failed payout must not save the beneficiary.
        self.assertEqual(Beneficiary.objects.filter(user=self.user).count(), 0)

    def test_ambiguous_send_timeout_holds_debit_never_refunds(self):
        """CRITICAL: a network timeout on the non-idempotent disburse POST is
        AMBIGUOUS — the bank may already have paid the recipient. The debit MUST be
        HELD (PENDING + reconcile), never refunded. Refunding here drains the float
        (recipient keeps the money, sender made whole) and a retry double-pays. The
        reconcile flag then lets reconcile_9psb settle it on the true
        outcome."""
        with patch("banking.client.Client.transfer",
                   side_effect=ProviderError("network_unavailable", uncertain=True)):
            res, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234", "expected_fee": "0.00",
            })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body.get("pending"))
        self.assertFalse(body.get("success"))
        txn = Transaction.objects.get(reference=body["reference"])
        self.assertEqual(txn.transaction_status, Transaction.PENDING)  # held, not settled
        self.assertTrue(txn.meta.get("reconcile"))                     # sweepable
        self.assertEqual(self.balance(), Decimal("40000"))             # debited, NOT refunded
        self.assertFalse(
            Transaction.objects.filter(user=self.user, transaction_status=Transaction.FAILED).exists())

    def test_accepted_unknown_status_holds_debit_and_reports_pending(self):
        """CRITICAL: a payout the bank ACCEPTED but reported with a status this code
        doesn't recognise must be treated exactly like PENDING — debit held,
        response pending — not as a failure. Misclassifying it refunded an
        in-flight transfer (double-spend) and showed the user "Error / success"."""
        with patch("banking.client.Client.transfer",
                   return_value=Reply({"status": "SUCCESS", "responseCode": "00",
                       "data": {"transactionStatus": "QUEUED_AT_SWITCH"}}, 200)):
            res, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234", "expected_fee": "0.00",
            })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body.get("pending"))
        txn = Transaction.objects.get(reference=body["reference"])
        self.assertEqual(txn.transaction_status, Transaction.PENDING)
        self.assertTrue(txn.meta.get("reconcile"))
        self.assertEqual(self.balance(), Decimal("40000"))  # held, not refunded

    def test_conflicting_failure_and_success_keeps_the_debit_pending(self):
        with patch("banking.client.Client.transfer", return_value=Reply({
                "status": "SUCCESS", "responseCode": "00", "data": {
                    "status": "FAILED", "responseCode": "51"}}, 200)):
            res, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body.get("pending"))
        self.assertFalse(body.get("refunded"))
        self.assertEqual(Transaction.objects.get(reference=body["reference"]).transaction_status, Transaction.PENDING)
        self.assertEqual(self.balance(), Decimal("40000"))

    def test_provider_success_echo_is_never_shown_as_the_error(self):
        """A definitive provider rejection whose message is the request-level
        "success" echo must not reach the app as the error text (the
        "Error / success" dialog) — the view substitutes a real sentence."""
        with patch("banking.client.Client.transfer",
                   return_value=Reply({"status": "FAILED", "responseCode": "51", "message": "success"}, 200)):
            res, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234", "expected_fee": "0.00",
            })
        self.assertEqual(res.status_code, 422)
        self.assertEqual(body.get("code"), "transfer_failed")
        self.assertTrue(body.get("refunded"))
        self.assertTrue(body.get("reference"))
        self.assertNotEqual(body["message"].strip().lower(), "success")
        self.assertEqual(self.balance(), Decimal("50000"))  # definitive -> refunded

    def test_reconcile_flag_committed_before_provider_call(self):
        """A crash between the debit commit and the settle write must leave a row the
        payout sweep (meta__reconcile=True) can still find. Assert the committed
        PENDING row already carries the flag at the moment the bank is invoked —
        i.e. it is pre-flagged with the debit, not after the call."""
        seen = {}

        def spy(*args, **kwargs):
            row = Instruction.objects.get(reference=args[0]).transaction
            seen["reconcile"] = (row.meta or {}).get("reconcile")
            seen["status"] = row.transaction_status
            return Reply({"status": "SUCCESS", "responseCode": "00"}, 200)

        with patch("banking.client.Client.transfer", side_effect=spy):
            self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234", "expected_fee": "0.00",
            })
        self.assertTrue(seen["reconcile"])                     # flagged BEFORE the send
        self.assertEqual(seen["status"], Transaction.PENDING)

    def test_delivered_payout_clears_reconcile_flag(self):
        """A confirmed-delivered payout is settled AND its reconcile flag cleared, so
        the reconciler never needlessly requeries a completed transfer."""
        with patch("banking.client.Client.transfer",
                   return_value=Reply({"status": "SUCCESS", "responseCode": "00"}, 200)):
            _, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234", "expected_fee": "0.00",
            })
        txn = Transaction.objects.get(reference=body["reference"])
        self.assertEqual(txn.transaction_status, Transaction.SUCCESS)
        self.assertNotIn("reconcile", txn.meta or {})


class DemoSourceAccountTests(BankIOCase):
    """A historical demo account is never ownership evidence for new banking."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user, self.token = make_unbanked_user("08010000009", "demo@zitch.test", balance="50000")
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#E32119")
        wallet = get_or_create_wallet(self.user)
        wallet.account_number = "0112345678"
        wallet.bank_name = "Wema Bank (demo)"      # the mock rail's stamp
        wallet.save(update_fields=["account_number", "bank_name"])

    def send(self):
        res = self.client.post("/api/transfers/send/", data=json.dumps({
            "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
            "name": "John Doe", "amount": "10000", "transaction_pin": "1234", "expected_fee": "0.00",
            "idempotency_key": "demo-source-send-1",
        }), content_type="application/json")
        return res, res.json()

    def test_historical_demo_source_is_refused_before_any_debit(self):
        before = get_or_create_wallet(self.user).balance
        res, body = self.send()
        self.assertEqual(res.status_code, 403)
        self.assertFalse(body.get("success"))
        self.bank_send.assert_not_called()
        self.assertEqual(get_or_create_wallet(self.user).balance, before)
        self.assertFalse(Transaction.objects.filter(user=self.user, direction=Transaction.OUT).exists())

    @override_settings(NINEPSB={**CONFIG, "ENABLED": False})
    def test_disabled_banking_never_uses_a_mock_money_fallback(self):
        _, body = self.send()
        self.assertFalse(body.get("success"))
        self.bank_send.assert_not_called()
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("50000"))


class ClearDemoAccountAdminTests(TestCase):
    """provision_wema_account refuses to replace an existing NUBAN, so a wallet
    stamped with a test-mode one can never be issued a real one without this."""

    def setUp(self):
        from django.contrib.admin.sites import AdminSite

        from wallet.admin import WalletAdmin
        from wallet.models import Wallet

        self.admin = WalletAdmin(Wallet, AdminSite())
        self.messages = []
        self.admin.message_user = lambda req, msg, level=None: self.messages.append(msg)

    def _wallet(self, phone, bank_name):
        user, _ = make_unbanked_user(phone, f"{phone}@zitch.test", balance="100")
        wallet = get_or_create_wallet(user)
        wallet.account_number = f"01{phone[-8:]}"
        wallet.bank_name = bank_name
        wallet.account_reference = f"ref-{phone}"
        wallet.save(update_fields=["account_number", "bank_name", "account_reference"])
        return wallet

    def test_clears_a_demo_account_but_keeps_the_balance(self):
        from wallet.models import Wallet

        wallet = self._wallet("08010000021", "Wema Bank (demo)")
        self.admin.clear_demo_account(None, Wallet.objects.filter(pk=wallet.pk))
        wallet.refresh_from_db()
        self.assertEqual(wallet.account_number, "")
        self.assertEqual(wallet.account_reference, "")
        self.assertEqual(wallet.balance, Decimal("100.00"))

    def test_never_clears_a_real_account(self):
        from wallet.models import Wallet

        wallet = self._wallet("08010000022", "Wema Bank")
        self.admin.clear_demo_account(None, Wallet.objects.filter(pk=wallet.pk))
        wallet.refresh_from_db()
        self.assertEqual(wallet.account_number, "0110000022")
        self.assertIn("issued by the real bank", " ".join(self.messages))


class PayoutFailureIsRecordedTests(BankIOCase):
    """A rejected payout used to leave nothing behind but a Failed row: the rail's
    reason went to the user and the log and was then lost. On a deploy with no
    shell and no log access, every failed transfer looked identical."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user, self.token = make_user("08010000031", "why@zitch.test", balance="50000")
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#E32119")

    def test_rejection_code_is_retained_without_raw_provider_text(self):
        with patch("banking.client.Client.transfer", return_value=Reply({
                "status": "FAILED", "responseCode": "51", "message": "Private bank response"}, 200)):
            res = self.client.post("/api/transfers/send/", data=json.dumps({
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
                "expected_fee": "0.00", "idempotency_key": "failure-reason-send-1",
            }), content_type="application/json")
        self.assertEqual(res.status_code, 422)
        txn = Transaction.objects.get(user=self.user, direction=Transaction.OUT)
        self.assertEqual(txn.transaction_status, Transaction.FAILED)
        self.assertEqual(txn.meta.get("failure_status"), "FAILED")
        self.assertEqual(Instruction.objects.get(transaction=txn).last_error, "waas_response_51")
        self.assertNotIn("Private bank response", json.dumps(txn.meta))
        self.assertNotIn("Private bank response", res.content.decode())
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("50000.00"))

    def test_the_admin_column_reads_it_back(self):
        from django.contrib.admin.sites import AdminSite

        from wallet.admin import TransactionAdmin
        from wallet.models import Transaction as Txn

        row = Txn(meta={"failure": "Debit account is restricted"})
        self.assertEqual(TransactionAdmin(Txn, AdminSite()).failure_reason(row),
                         "Debit account is restricted")
        self.assertEqual(TransactionAdmin(Txn, AdminSite()).failure_reason(Txn(meta={})), "—")


class NoSourceAccountTests(BankIOCase):
    """No customer-owned 9PSB account means no debit or shared-pool fallback."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user, self.token = make_unbanked_user("08010000041", "nosrc@zitch.test", balance="50000")
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#E32119")
        # Wallet exists (make_user credits it) but was never provisioned a NUBAN.

    def send(self):
        res = self.client.post("/api/transfers/send/", data=json.dumps({
            "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
            "name": "John Doe", "amount": "10000", "transaction_pin": "1234", "expected_fee": "0.00",
            "idempotency_key": "no-source-send-1",
        }), content_type="application/json")
        return res, res.json()

    def test_unprovisioned_user_is_refused_before_debit(self):
        before = get_or_create_wallet(self.user).balance
        _, body = self.send()
        self.assertFalse(body.get("success"))
        self.bank_send.assert_not_called()
        self.assertEqual(get_or_create_wallet(self.user).balance, before)
        self.assertFalse(Transaction.objects.filter(user=self.user, direction=Transaction.OUT).exists())

    @override_settings(WEMA={"SOURCE_ACCOUNT": "0100000001"})
    def test_historical_shared_pool_cannot_substitute_for_an_owned_account(self):
        _, body = self.send()
        self.assertFalse(body.get("success"))
        self.bank_send.assert_not_called()
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("50000"))


class PayoutSourceActivationTests(BankIOCase):
    def setUp(self):
        super().setUp()
        self.user, _ = make_user("08010000042", "activate@zitch.test", balance="50000")
        self.bank = Bank.objects.create(code="gtb-activate", name="GTBank", bank_code="058")
        self.source = BankAccount.objects.get(wallet__user=self.user)

    def test_restricted_source_never_debits_or_calls_transfer(self):
        from .services import PayoutError, execute_payout
        self.source.pnd = True
        self.source.save(update_fields=["pnd"])
        with self.assertRaises(PayoutError) as caught:
            execute_payout(self.user, Decimal("1000"), "0123456789", self.bank,
                "JOHN DOE", channel="app", expected_fee="0.00")
        self.assertEqual(caught.exception.kind, "limit_exceeded")
        self.assertIn("restricted", caught.exception.message)
        self.bank_send.assert_not_called()
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("50000"))
        self.assertFalse(Transaction.objects.filter(user=self.user, direction=Transaction.OUT).exists())

    def test_send_uses_owned_bank_binding_even_when_display_mirror_is_stale(self):
        from wallet.models import Wallet
        from .services import execute_payout
        Wallet.objects.filter(user=self.user).update(account_number="0452491369", pnd_lifted=True)
        execute_payout(self.user, Decimal("1000"), "0123456789", self.bank,
            "JOHN DOE", channel="app", expected_fee="0.00")
        self.assertEqual(self.bank_send.call_args.args[2], self.source.number)
        self.assertNotEqual(self.bank_send.call_args.args[2], "0452491369")


class SavedBeneficiaryTests(BankIOCase):
    """Keeping a recipient, naming them, and removing them.

    The distinction under test throughout: every payout records a RECENT, and
    only the customer turns a recent into a saved beneficiary. The two are kept
    apart because the send screen treats a row as proof that money once reached
    that account, and paying by name is only offered against names a customer
    chose for themselves.
    """

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user, self.token = make_user("08010000001", "ada@zitch.test", balance="50000")
        self.other, self.other_token = make_user("08010000002", "bola@zitch.test")
        self.row = Beneficiary.objects.create(
            user=self.user, name="JOHN DOE", account_number="0123456789",
            bank_name="GTBank", bank_code="058")

    def post(self, path, payload):
        payload = dict(payload)
        if path == "/api/transfers/send/":
            payload.setdefault("expected_fee", "0.00")
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def test_a_payout_records_a_recent_not_a_saved_beneficiary(self):
        bank = Bank.objects.create(code="gtb2", name="Zenith", bank_code="057")
        from .services import execute_payout
        with patch("banking.client.Client.transfer",
                   return_value=Reply({"status": "SUCCESS", "responseCode": "00"}, 200)):
            execute_payout(self.user, Decimal("1000"), "0999999999", bank,
                           "MUSA ADAMU", channel="app", expected_fee="0.00")
        row = Beneficiary.objects.get(user=self.user, account_number="0999999999")
        self.assertFalse(row.saved)
        self.assertEqual(row.nickname, "")

    def test_saving_keeps_the_recipient(self):
        res, body = self.post("/api/transfers/beneficiaries/save/", {
            "access_token": self.token, "beneficiary_id": self.row.id})
        self.assertEqual(res.status_code, 200)
        self.row.refresh_from_db()
        self.assertTrue(self.row.saved)
        self.assertTrue(body["beneficiary"]["saved"])

    def test_saving_with_a_nickname_sets_the_display_name(self):
        _, body = self.post("/api/transfers/beneficiaries/save/", {
            "access_token": self.token, "beneficiary_id": self.row.id, "nickname": "Mum"})
        self.row.refresh_from_db()
        self.assertEqual(self.row.nickname, "Mum")
        self.assertEqual(body["beneficiary"]["display_name"], "Mum")
        # The holder name is untouched: bank_transfer re-confirms against it.
        self.assertEqual(self.row.name, "JOHN DOE")

    def test_naming_a_recent_also_keeps_it(self):
        # Otherwise a customer names someone "Mum" in the app and then cannot pay
        # "Mum" in the chat, because paying by name only reads saved rows.
        self.post("/api/transfers/beneficiaries/rename/", {
            "access_token": self.token, "beneficiary_id": self.row.id, "nickname": "Mum"})
        self.row.refresh_from_db()
        self.assertTrue(self.row.saved)

    def test_a_duplicate_nickname_is_refused(self):
        second = Beneficiary.objects.create(
            user=self.user, name="MUSA ADAMU", account_number="0999999999",
            bank_name="Zenith", saved=True, nickname="Mum")
        res, body = self.post("/api/transfers/beneficiaries/rename/", {
            "access_token": self.token, "beneficiary_id": self.row.id, "nickname": "mum"})
        self.assertEqual(res.status_code, 409)
        self.row.refresh_from_db()
        self.assertEqual(self.row.nickname, "")
        self.assertEqual(second.nickname, "Mum")

    def test_a_nickname_of_digits_is_refused(self):
        # In a chat message a numeric nickname is indistinguishable from an
        # account number, and both arrive as bare text.
        res, _ = self.post("/api/transfers/beneficiaries/rename/", {
            "access_token": self.token, "beneficiary_id": self.row.id, "nickname": "0123456789"})
        self.assertEqual(res.status_code, 400)

    def test_clearing_the_nickname_leaves_the_recipient_saved(self):
        self.row.saved, self.row.nickname = True, "Mum"
        self.row.save()
        _, body = self.post("/api/transfers/beneficiaries/rename/", {
            "access_token": self.token, "beneficiary_id": self.row.id, "nickname": ""})
        self.row.refresh_from_db()
        self.assertEqual(self.row.nickname, "")
        self.assertTrue(self.row.saved)
        self.assertEqual(body["beneficiary"]["display_name"], "JOHN DOE")

    def test_deleting_removes_the_row_entirely(self):
        # Not merely unsaved: a row left behind still answers the send screen's
        # typed-account fast path, which is not what "remove" means.
        res, _ = self.post("/api/transfers/beneficiaries/delete/", {
            "access_token": self.token, "beneficiary_id": self.row.id})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Beneficiary.objects.filter(pk=self.row.pk).exists())

    def test_another_customers_recipient_is_untouchable(self):
        for path in ("save", "rename", "delete"):
            res, _ = self.post(f"/api/transfers/beneficiaries/{path}/", {
                "access_token": self.other_token, "beneficiary_id": self.row.id,
                "nickname": "Mine"})
            self.assertEqual(res.status_code, 404, path)
        self.row.refresh_from_db()
        self.assertFalse(self.row.saved)
        self.assertEqual(self.row.nickname, "")

    def test_the_list_still_carries_what_shipped_apps_read(self):
        # A phone that has not updated must keep rendering exactly what it does
        # today, so `name` and `initials` stay off the holder name.
        self.row.nickname, self.row.saved = "Mum", True
        self.row.save()
        _, body = self.post("/api/transfers/beneficiaries/", {"access_token": self.token})
        item = body["beneficiaries"][0]
        self.assertEqual(item["name"], "JOHN DOE")
        self.assertEqual(item["initials"], "JD")
        self.assertEqual(item["display_name"], "Mum")
        self.assertTrue(item["saved"])

    def test_the_list_is_not_filtered_to_saved(self):
        # The send screen's fast path reads this list; filtering would empty it.
        _, body = self.post("/api/transfers/beneficiaries/", {"access_token": self.token})
        self.assertEqual(len(body["beneficiaries"]), 1)

    def test_a_repeat_payout_never_unsaves_or_unnames(self):
        self.row.saved, self.row.nickname = True, "Mum"
        self.row.save()
        bank = Bank.objects.create(code="gtb2", name="GTBank", bank_code="058")
        from .services import execute_payout
        with patch("banking.client.Client.transfer",
                   return_value=Reply({"status": "SUCCESS", "responseCode": "00"}, 200)):
            execute_payout(self.user, Decimal("1000"), "0123456789", bank,
                           "JOHN DOE", channel="app", expected_fee="0.00")
        self.row.refresh_from_db()
        self.assertTrue(self.row.saved)
        self.assertEqual(self.row.nickname, "Mum")

    def test_a_send_names_the_row_it_just_wrote(self):
        bank = Bank.objects.create(code="gtb3", name="Access", bank_code="044")
        with patch("banking.client.Client.transfer",
                   return_value=Reply({"status": "SUCCESS", "responseCode": "00"}, 200)):
            _, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0777777777", "bank": "gtb3",
                "name": "MUSA ADAMU", "amount": "1000", "transaction_pin": "1234", "expected_fee": "0.00",
                "idempotency_key": "saved-beneficiary-send-1"})
        self.assertTrue(body.get("success"), body)
        row = Beneficiary.objects.get(user=self.user, account_number="0777777777")
        self.assertEqual(body["beneficiary_id"], row.id)


class PayoutFrequencyTests(BankIOCase):
    """A recipient earns their place in the address book.

    Every payout is remembered — that is what fills the send screen in from a
    typed account number — but being remembered is not the same as being kept.
    Keeping is the customer's own decision, or the conclusion of having paid
    somebody so many times that asking would be pedantic.
    """

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user, self.token = make_user("08010000001", "ada@zitch.test", balance="500000")
        self.bank = Bank.objects.create(code="gtb", name="GTBank", bank_code="058")

    def pay(self):
        from .services import execute_payout
        with patch("banking.client.Client.transfer",
                   return_value=Reply({"status": "SUCCESS", "responseCode": "00"}, 200)):
            return execute_payout(self.user, Decimal("1000"), "0123456789", self.bank,
                                  "JOHN DOE", channel="app", expected_fee="0.00")

    def row(self):
        return Beneficiary.objects.get(user=self.user, account_number="0123456789")

    def test_every_payout_is_counted(self):
        for _ in range(3):
            self.pay()
        self.assertEqual(self.row().transfer_count, 3)

    def test_the_first_two_payouts_leave_the_recipient_unsaved(self):
        # Below the threshold nothing is offered and nothing is kept — the row
        # exists only to fill the send screen in when that account is typed again.
        self.pay()
        self.assertEqual(self.row().transfer_count, 1)
        self.pay()
        self.assertEqual(self.row().transfer_count, 2)
        self.assertFalse(self.row().saved)
        self.assertFalse(self.row().save_offer_sent)

    def test_the_third_payout_reaches_the_offer_threshold(self):
        self.pay(); self.pay(); self.pay()
        self.assertEqual(self.row().transfer_count, 3)
        # Reaching it is not itself the offer: the chat sends that, once, and
        # records it on the row (covered in whatsapp.tests.SavedPeopleTests).
        self.assertFalse(self.row().saved)

    def test_a_payout_never_unsaves_a_kept_recipient(self):
        self.pay()
        r = self.row(); r.saved = True; r.nickname = "Mum"; r.save()
        self.pay()
        self.assertTrue(self.row().saved)
        self.assertEqual(self.row().nickname, "Mum")

    def test_fifty_payouts_keep_the_recipient_without_asking(self):
        Beneficiary.objects.create(
            user=self.user, name="JOHN DOE", account_number="0123456789",
            bank_name="GTBank", transfer_count=50)
        self.pay()
        self.assertTrue(self.row().saved)

    def test_the_count_survives_two_payouts_settling_at_once(self):
        # Read-modify-write would lose one, and the count is what decides whether
        # somebody is offered and when they are kept outright.
        self.pay()
        Beneficiary.objects.filter(pk=self.row().pk).update(transfer_count=7)
        self.pay()
        self.assertEqual(self.row().transfer_count, 8)


class BankShortFormTests(TestCase):
    """What people actually call the banks.

    We store one name each, usually the short trading name, so the legal name off
    a statement matched nothing at all — which reads as the bank not existing
    rather than as us knowing it by another name.
    """

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_unbanked_user("08010000001", "ada@zitch.test")
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", active=True)
        Bank.objects.create(code="uba", name="UBA", bank_code="033", active=True)
        Bank.objects.create(code="scb", name="Standard Chartered", bank_code="068", active=True)

    def test_the_legal_name_finds_the_bank(self):
        from whatsapp.router import _match_banks
        for phrase in ("guaranty trust bank", "guaranty trust", "gtco", "gt"):
            hits = _match_banks(phrase)
            self.assertEqual([b.code for b in hits], ["gtb"], phrase)

    def test_an_initialism_finds_the_bank(self):
        from whatsapp.router import _match_banks
        self.assertEqual([b.code for b in _match_banks("scb")], ["scb"])
        self.assertEqual([b.code for b in _match_banks("united bank for africa")], ["uba"])

    def test_an_unknown_phrase_still_matches_nothing(self):
        from whatsapp.router import _match_banks
        self.assertEqual(_match_banks("not a bank at all"), [])

    def test_no_alias_is_claimed_by_two_banks(self):
        # An alias is matched exactly and wins outright, so one claimed twice
        # would silently route money to whichever sorted first.
        from .bank_aliases import BANK_ALIASES
        seen = {}
        for slug, (_short, names) in BANK_ALIASES.items():
            for n in names:
                self.assertNotIn(n, seen, f"{n!r} claimed by {seen.get(n)} and {slug}")
                seen[n] = slug

    def test_the_app_is_told_the_short_form(self):
        res = self.client.post("/api/transfers/banks/", data=json.dumps({}),
                               content_type="application/json")
        rows = {b["code"]: b for b in res.json()["banks"]}
        self.assertEqual(rows["gtb"]["short"], "GT")
        self.assertIn("guaranty trust bank", rows["gtb"]["aliases"])
        # Additive: everything a shipped build reads is still there.
        self.assertEqual(rows["gtb"]["name"], "GTBank")
