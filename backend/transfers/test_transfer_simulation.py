"""Transfer simulation: the conditions the other suites do not already prove.

Every test here drives the real HTTP views and replaces ONLY the bank's HTTP
boundary (``utility.wema.requests``) with an in-memory gateway. The request the
bank receives, the envelope parsing, the authorisation callback, the settlement
state machine and the fee row are therefore all production code.

Each test ends on the ledger invariant (``assert_ledger``, registered as a
cleanup so it also runs when a test fails early): the stored balance is never
negative, equals what the ledger implies, and every transfer has at most one
fee row whose state follows the transfer's.

The bank's fee schedule is patched out so the published tariff applies
(N10.75 up to N5,000, N26.88 up to N50,000).

``RegressionSimulation`` holds three defects this simulation found and the
fixes that closed them.
"""
import json
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import requests
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from transfers.models import Bank, Beneficiary
from utility import wema
from wallet.models import BankHistoryCheckpoint, Transaction, Wallet
from wallet.services import wallet_expected_balance, wema_account_reference
from wallet.tests import make_user
from wallet.transfer_fees import FEE_SERVICE, fee_reference

TOKEN = "sim-callback-token"
LIVE = {
    "BASE_URL": "https://bank.invalid", "CHANNEL_ID": "sim-channel",
    "KEYS": {"wallet": "sim-wallet-key"},
    "SECURITY_INFO": "sim-security-seed-0123456789abcdef0123456789",
    "SIMULATION": False,
    "CALLBACK_TOKEN": TOKEN, "CALLBACK_TOKEN_PREV": "",
    "CALLBACK_ENFORCE_IPS": False, "CALLBACK_IPS": ["135.236.18.76"],
    "AUTH_MAX_AGE": 900, "AUTH_REQUIRE_SECURITY_INFO": True,
}
SENDER_ACCOUNT = "0451110001"
FEE = Decimal("10.75")                 # published tariff on N1,000
DEBIT = Decimal("1000") + FEE          # what one default transfer takes
PENDING, SUCCESS, FAILED = Transaction.PENDING, Transaction.SUCCESS, Transaction.FAILED


class _Reply:
    """One gateway response: a real int status and a body, or a body that will not parse."""

    def __init__(self, body=None, status=200, unparseable=False):
        self.status_code = status
        self._body = body
        self._unparseable = unparseable
        self.headers = {}
        self.content = b"{}"
        self.text = ""

    def json(self):
        if self._unparseable:
            raise ValueError("gateway answered with a page, not JSON")
        return self._body


class FakeBank:
    """The ALAT gateway, as far as the transfer path can see it."""

    def __init__(self):
        self.holder = "JOHN DOE"
        self.on_enquiry = None       # callable(bank_code, account) -> _Reply | Exception
        self.on_transfer = None      # callable(body) -> _Reply | Exception
        self.statuses = {}           # our reference -> the bank's status string
        self.history = {}            # account number -> statement rows
        self.transfers = []          # every ProcessClientTransfer body received
        self.enquiries = []
        self.status_lookups = []
        self.history_reads = []

    @staticmethod
    def _play(answer):
        if isinstance(answer, Exception):
            raise answer
        return answer

    def answer(self, body, status="SUCCESS"):
        reference = body["transactionReference"]
        return _Reply({"result": {"status": status, "transactionReference": reference,
                                  "platformTransactionReference": f"WEMA-{reference}"},
                       "hasError": False})

    def get(self, url, params=None, headers=None, timeout=None):
        if "/debit-wallet/api/Shared/AccountNameEnquiry/" in url:
            bank_code, account = url.rsplit("/", 2)[-2:]
            self.enquiries.append((bank_code, account))
            if self.on_enquiry is not None:
                return self._play(self.on_enquiry(bank_code, account))
            return _Reply({"result": {"accountName": self.holder, "bankCode": bank_code,
                                      "accountNumber": account}, "hasError": False})
        if "/debit-wallet/api/IntraBankTransfer/ConfirmClientTransferStatus/" in url:
            asked = url.rsplit("/", 1)[-1]
            self.status_lookups.append(asked)
            reference = asked[5:] if asked.startswith("WEMA-") else asked
            status = self.statuses.get(reference)
            if status is None:
                # What ALAT answers for a reference its status store has no verdict on.
                return _Reply({"result": None, "hasError": True,
                               "errorMessage": "Transaction not found"})
            return _Reply({"result": {"status": status, "transactionReference": reference,
                                      "platformTransactionReference": f"WEMA-{reference}"},
                           "hasError": False})
        raise AssertionError(f"unexpected bank GET {url}")

    def post(self, url, json=None, params=None, headers=None, timeout=None):
        if url.endswith("/debit-wallet/api/Shared/ProcessClientTransfer"):
            self.transfers.append(dict(json))
            return self._play((self.on_transfer or self.answer)(json))
        if url.endswith("/transhistoryV2"):
            self.history_reads.append(json["accountNumber"])
            rows = [dict(row) for row in self.history.get(json["accountNumber"], [])]
            return _Reply({"successful": True, "message": "ok", "result": rows})
        raise AssertionError(f"unexpected bank POST {url}")


@override_settings(WEMA=LIVE)
class LiveSimulationCase(TestCase):
    opening = "50000"

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user, self.token = make_user("08031110001", "sim-sender@zitch.test",
                                          balance=self.opening)
        self.attach_account(self.user, SENDER_ACCOUNT)
        Bank.objects.update_or_create(code="gtb", defaults={
            "name": "GTBank", "bank_code": "058", "active": True})
        Bank.objects.update_or_create(code="wema", defaults={
            "name": "Wema Bank", "bank_code": "035", "active": True})
        self.bank = FakeBank()
        self.authorisations = []
        for verb in ("get", "post"):
            patcher = patch(f"utility.wema.requests.{verb}", getattr(self.bank, verb))
            patcher.start()
            self.addCleanup(patcher.stop)
        tariff = patch("utility.providers.payout_charge", return_value=None)
        tariff.start()
        self.addCleanup(tariff.stop)
        self.ledger_users = [self.user]
        self._keys = 0
        self.addCleanup(self.assert_ledger)

    # -- fixtures ----------------------------------------------------------
    def attach_account(self, user, number, holder="ADA EZE"):
        wallet = Wallet.objects.get(user=user)
        Wallet.objects.filter(pk=wallet.pk).update(
            account_number=number, account_name=holder, bank_name="Wema Bank",
            account_reference=wema_account_reference(user), pnd_lifted=True)
        BankHistoryCheckpoint.objects.create(
            wallet=wallet, account_number=number, opening_review_required=False)

    def recipient(self):
        user, _ = make_user("08031110002", "sim-recipient@zitch.test")
        self.attach_account(user, "0451110002")
        self.ledger_users.append(user)
        return user

    # -- the customer ------------------------------------------------------
    def api(self, path, payload, client=None):
        return (client or self.client).post(
            path, json.dumps(payload), content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def send_bank(self, client=None, **changes):
        self._keys += 1
        payload = {"account_number": "0123456789", "bank": "gtb", "name": "John Doe",
                   "amount": "1000", "transaction_pin": "1234",
                   "idempotency_key": f"sim-bank-{self._keys}"}
        payload.update(changes)
        return self.api("/api/transfers/send/", payload, client=client)

    def last_key(self):
        return f"sim-bank-{self._keys}"

    # -- the bank ----------------------------------------------------------
    def bank_says(self, kind, payload):
        return Client().post(f"/webhooks/wema/{kind}/{TOKEN}", json.dumps(payload),
                             content_type="application/json")

    def transaction_callback(self, reference, status):
        return self.bank_says("transaction", {"requestType": 3, "data": {
            "status": status, "transactionReference": reference,
            "platformTransactionReference": f"WEMA-{reference}"}})

    def ask_authorisation(self, reference, security_info, pascal_case=False):
        keys = (("TransactionReference", "SecurityInfo") if pascal_case
                else ("transactionReference", "securityInfo"))
        answer = self.bank_says(
            "authorize", {keys[0]: reference, keys[1]: security_info}).json()
        self.authorisations.append(answer)
        return answer

    def authorising(self, status="SUCCESS", security_info=None, pascal_case=False):
        """A bank that asks our authentication callback before it moves money."""
        def handler(body):
            reference = body["transactionReference"]
            presented = body["securityInfo"] if security_info is None else security_info
            if not self.ask_authorisation(reference, presented, pascal_case)["authorized"]:
                return _Reply({"result": None, "hasError": True,
                               "errorMessage": "Authentication Failed"})
            return self.bank.answer(body, status)
        return handler

    def poll(self):
        try:
            call_command("reconcile_wema", "--payout-older-than-minutes=0",
                         stdout=StringIO(), stderr=StringIO())
        except SystemExit:
            pass

    # -- the ledger --------------------------------------------------------
    def balance(self, user=None):
        return Wallet.objects.get(user=user or self.user).balance

    def row(self, reference):
        return Transaction.objects.get(reference=reference)

    def fee(self, reference):
        return Transaction.objects.get(reference=fee_reference(reference))

    def payouts(self):
        return Transaction.objects.filter(
            user=self.user, direction=Transaction.OUT, service__startswith="Transfer to")

    def assert_ledger(self):
        for user in self.ledger_users:
            wallet = Wallet.objects.get(user=user)
            self.assertGreaterEqual(wallet.balance, Decimal("0"))
            self.assertEqual(wallet.balance, wallet_expected_balance(user.pk),
                             "the stored balance has drifted from the ledger")
            transfers = Transaction.objects.filter(
                user=user, direction=Transaction.OUT, service__startswith="Transfer to")
            for transfer in transfers:
                fees = list(Transaction.objects.filter(
                    reference=fee_reference(transfer.reference)))
                self.assertLessEqual(len(fees), 1)
                for fee in fees:
                    self.assertEqual(fee.service, FEE_SERVICE)
                    self.assertEqual(fee.transaction_status, transfer.transaction_status,
                                     "a fee must follow its transfer")


class BankOutcomeSimulation(LiveSimulationCase):
    def test_ambiguous_bank_outcomes_hold_the_debit_and_are_never_refunded_or_resent(self):
        answer = self.bank.answer

        def keyed(result, **envelope):
            return lambda body: _Reply({
                "result": {**result, "transactionReference": body["transactionReference"]},
                **envelope})

        cases = [
            ("read timeout", lambda body: requests.Timeout("read timed out")),
            ("connection reset", lambda body: requests.ConnectionError("Connection reset by peer")),
            ("http 500", lambda body: _Reply({"statusCode": 500, "message": "Internal server error"}, 500)),
            ("http 502 page", lambda body: _Reply(status=502, unparseable=True)),
            ("http 503", lambda body: _Reply({"statusCode": 503, "message": "Service unavailable"}, 503)),
            ("http 504", lambda body: _Reply(status=504, unparseable=True)),
            ("http 429", lambda body: _Reply({"statusCode": 429, "message": "Rate limit is exceeded"}, 429)),
            ("http 409", lambda body: _Reply({"hasError": True, "errorMessage": "Duplicate reference"}, 409)),
            ("http 408", lambda body: _Reply({"hasError": True, "errorMessage": "Timeout"}, 408)),
            ("http 200 page", lambda body: _Reply(unparseable=True)),
            ("http 200 null", lambda body: _Reply(None)),
            ("http 200 empty object", lambda body: _Reply({})),
            ("http 200 empty list", lambda body: _Reply([])),
            ("http 200 unknown status", lambda body: answer(body, "QUEUED_AT_SWITCH")),
            ("http 200 blank status", lambda body: answer(body, "")),
            ("http 200 processor timeout",
             keyed({"status": "TIMEOUT"}, hasError=True, errorMessage="Request timed out")),
            ("http 200 success for another reference", lambda body: _Reply({
                "result": {"status": "SUCCESS", "transactionReference": "ZTCHSOMEONEELSE"},
                "hasError": False})),
            ("http 200 conflicting statuses",
             keyed({"status": "SUCCESS", "transactionStatus": "FAILED"}, hasError=False)),
            ("http 200 string success flag", keyed({"status": "SUCCESS"}, hasError="false")),
        ]
        for label, outcome in cases:
            with self.subTest(label):
                self.bank.on_transfer = outcome
                before, sent = self.balance(), len(self.bank.transfers)
                response = self.send_bank()
                body = response.json()
                self.assertEqual(response.status_code, 200, body)
                self.assertTrue(body.get("pending"), body)
                self.assertNotIn("success", body)
                payout = self.row(body["reference"])
                self.assertEqual(payout.transaction_status, PENDING)
                self.assertTrue(payout.meta.get("reconcile"))
                self.assertEqual(self.fee(payout.reference).transaction_status, PENDING)
                self.assertEqual(self.balance(), before - DEBIT)

                # The customer retries the same attempt (even mistyping the PIN):
                # it is replayed from the ledger, never sent to the bank again.
                replay = self.send_bank(idempotency_key=self.last_key(), transaction_pin="0000")
                self.assertEqual(replay.status_code, 200, replay.json())
                self.assertTrue(replay.json().get("pending"))
                self.assertTrue(replay.json().get("duplicate"))
                self.assertEqual(replay.json()["reference"], payout.reference)
                self.assertEqual(len(self.bank.transfers), sent + 1)

                # A callback CLAIMING failure only makes us ask the bank, which has
                # no verdict: nothing is refunded on the callback's word.
                self.transaction_callback(payout.reference, "Failed")
                self.assertEqual(self.row(payout.reference).transaction_status, PENDING)
                self.assertEqual(self.balance(), before - DEBIT)

        self.poll()
        self.assertFalse(Transaction.objects.filter(
            user=self.user, transaction_status=FAILED).exists())
        self.assertEqual(self.payouts().filter(transaction_status=PENDING).count(), len(cases))
        self.assertEqual(len(self.bank.transfers), len(cases))
        self.assertEqual(self.balance(), Decimal(self.opening) - DEBIT * len(cases))

    def test_a_queued_transfer_that_fails_refunds_amount_and_fee_exactly_once(self):
        for first in ("callback", "poller"):
            with self.subTest(first=first):
                self.bank.on_transfer = lambda body: self.bank.answer(body, "PENDING")
                before = self.balance()
                body = self.send_bank().json()
                key, reference = self.last_key(), body["reference"]
                self.assertTrue(body.get("pending"), body)
                self.assertEqual(self.balance(), before - DEBIT)

                self.bank.statuses[reference] = "FAILED"
                if first == "callback":
                    self.transaction_callback(reference, "Failed")
                    self.poll()
                else:
                    self.poll()
                    self.transaction_callback(reference, "Failed")
                self.assertEqual(self.balance(), before)

                # Replays, a second sweep, and the bank changing its mind afterwards.
                asked = len(self.bank.status_lookups)
                self.transaction_callback(reference, "Failed")
                self.bank.statuses[reference] = "SUCCESS"
                self.transaction_callback(reference, "Successful")
                self.poll()
                self.assertEqual(len(self.bank.status_lookups), asked)
                self.assertEqual(self.balance(), before)
                self.assertEqual(self.row(reference).transaction_status, FAILED)
                self.assertEqual(self.fee(reference).transaction_status, FAILED)

                # The spent key cannot start a second transfer.
                retry = self.send_bank(idempotency_key=key)
                self.assertEqual(retry.status_code, 409, retry.json())
        self.assertEqual(len(self.bank.transfers), 2)
        self.assertEqual(self.balance(), Decimal(self.opening))

    def test_a_queued_transfer_settles_on_requery_and_confirms_its_fee(self):
        self.bank.on_transfer = lambda body: self.bank.answer(body, "PENDING")
        body = self.send_bank().json()
        key, reference = self.last_key(), body["reference"]
        held = self.balance()
        self.assertEqual(held, Decimal(self.opening) - DEBIT)

        # Forged or premature "Successful": the bank's own status store disagrees.
        self.transaction_callback(reference, "Successful")
        self.assertEqual(self.row(reference).transaction_status, PENDING)
        self.assertEqual(self.fee(reference).transaction_status, PENDING)

        self.bank.statuses[reference] = "SUCCESS"
        self.poll()
        payout = self.row(reference)
        self.assertEqual(payout.transaction_status, SUCCESS)
        self.assertNotIn("reconcile", payout.meta)
        self.assertEqual(self.fee(reference).transaction_status, SUCCESS)
        self.assertEqual(self.balance(), held)

        # A late failure for a settled transfer asks the bank nothing and moves nothing.
        asked = len(self.bank.status_lookups)
        self.bank.statuses[reference] = "FAILED"
        self.transaction_callback(reference, "Failed")
        self.poll()
        self.assertEqual(len(self.bank.status_lookups), asked)
        self.assertEqual(self.row(reference).transaction_status, SUCCESS)
        self.assertEqual(self.balance(), held)

        replay = self.send_bank(idempotency_key=key)
        self.assertTrue(replay.json().get("success"), replay.json())
        self.assertTrue(replay.json().get("duplicate"))
        self.assertEqual(len(self.bank.transfers), 1)

    def test_the_bank_is_authorised_only_for_our_own_fresh_instruction(self):
        self.bank.on_transfer = self.authorising()
        response = self.send_bank()
        body = response.json()
        self.assertEqual(response.status_code, 200, body)
        self.assertTrue(body.get("success"), body)
        self.assertEqual(body["fee"], str(FEE))
        self.assertEqual(Decimal(body["wallet"]), Decimal(self.opening) - DEBIT)
        payout = self.row(body["reference"])
        self.assertEqual((payout.transaction_status, payout.amount), (SUCCESS, Decimal("1000")))
        self.assertEqual(self.fee(payout.reference).transaction_status, SUCCESS)

        sent = self.bank.transfers[0]
        self.assertEqual(sent["transactionReference"], payout.reference)
        self.assertEqual(sent["sourceAccountNumber"], SENDER_ACCOUNT)
        self.assertEqual((sent["destinationBankCode"], sent["destinationAccountNumber"]),
                         ("058", "0123456789"))
        self.assertEqual(sent["amount"], 1000.0)
        self.assertEqual(sent["securityInfo"], wema.security_info_for_reference(payout.reference))
        self.assertNotIn(LIVE["SECURITY_INFO"], json.dumps(sent))
        self.assertEqual(self.authorisations,
                         [{"transactionReference": payout.reference, "authorized": True}])

        # Replaying the very same authorisation after settlement is refused.
        self.assertFalse(self.ask_authorisation(payout.reference, sent["securityInfo"])["authorized"])

        # A forged, missing or borrowed securityInfo is refused; the bank's
        # resulting decline returns the amount AND the fee.
        for label, presented in (("forged", "0" * 64), ("missing", ""),
                                 ("borrowed", sent["securityInfo"])):
            with self.subTest(label):
                self.bank.on_transfer = self.authorising(security_info=presented)
                before = self.balance()
                refused = self.send_bank()
                self.assertEqual(refused.status_code, 422, refused.json())
                self.assertTrue(refused.json().get("refunded"))
                self.assertFalse(self.authorisations[-1]["authorized"])
                self.assertEqual(self.balance(), before)
                reference = refused.json()["reference"]
                self.assertEqual(self.row(reference).transaction_status, FAILED)
                self.assertEqual(self.fee(reference).transaction_status, FAILED)
        self.assertEqual(self.balance(), Decimal(self.opening) - DEBIT)

    def test_a_double_tap_while_the_first_request_is_in_flight_debits_and_sends_once(self):
        second = {}

        def in_flight(body):
            if not second:
                second["response"] = self.send_bank(client=Client(), idempotency_key="double-tap")
            return self.bank.answer(body, "SUCCESS")

        self.bank.on_transfer = in_flight
        first = self.send_bank(idempotency_key="double-tap")
        self.assertTrue(first.json().get("success"), first.json())
        inner = second["response"].json()
        self.assertEqual(second["response"].status_code, 200, inner)
        self.assertTrue(inner.get("pending"))
        self.assertTrue(inner.get("duplicate"))
        self.assertEqual(inner["reference"], first.json()["reference"])

        third = self.send_bank(idempotency_key="double-tap")
        self.assertTrue(third.json().get("success"), third.json())
        self.assertTrue(third.json().get("duplicate"))
        changed = self.send_bank(idempotency_key="double-tap", amount="2000")
        self.assertEqual(changed.status_code, 409, changed.json())
        self.assertEqual(changed.json().get("code"), "idempotency_conflict")

        self.assertEqual(len(self.bank.transfers), 1)
        self.assertEqual(self.payouts().count(), 1)
        self.assertEqual(Transaction.objects.filter(service=FEE_SERVICE).count(), 1)
        self.assertEqual(self.balance(), Decimal(self.opening) - DEBIT)

    def test_two_requests_racing_for_one_balance_cannot_both_be_paid(self):
        # SQLite cannot run two writers, so the race is interleaved by hand: the
        # second request runs to completion while the first is still inside its
        # name enquiry, i.e. after every pre-check the first will ever make. What
        # stops the overdraw on PostgreSQL is the same code this exercises:
        # wallet.services.debit re-reads the balance under
        # Wallet.objects.select_for_update(), backed by the
        # wallet_balance_non_negative constraint.
        racer = {}

        def enquiry(bank_code, account):
            if not racer:
                racer["started"] = True
                racer["response"] = self.send_bank(client=Client(), amount="26000")
            return _Reply({"result": {"accountName": "JOHN DOE", "bankCode": bank_code,
                                      "accountNumber": account}, "hasError": False})

        self.bank.on_enquiry = enquiry
        loser = self.send_bank(amount="26000")
        winner = racer["response"]
        self.assertTrue(winner.json().get("success"), winner.json())
        self.assertEqual(loser.status_code, 402, loser.json())
        self.assertEqual(len(self.bank.transfers), 1)
        self.assertEqual(self.payouts().count(), 1)
        self.assertEqual(self.balance(), Decimal(self.opening) - Decimal("26026.88"))


class RegressionSimulation(LiveSimulationCase):
    """Defects the simulation found. Each test failed before its fix."""

    # A service with no bank keys in a real-money deploy (the reconcile cron ran
    # without them on 2026-11-12) has asked the bank nothing. Its status lookup
    # used to answer {"success": False} with no "pending", which reads as a
    # declined transfer, so a bank callback refunded the amount and fee of a
    # payout the bank may have paid. It must stay pending.
    def test_a_callback_on_a_deploy_without_bank_keys_leaves_the_transfer_pending(self):
        self.bank.on_transfer = lambda body: requests.Timeout("read timed out")
        reference = self.send_bank().json()["reference"]
        held = self.balance()
        without_keys = {**LIVE, "CHANNEL_ID": "", "KEYS": {"wallet": ""}}
        with override_settings(DEBUG=False, TESTING=False, WEMA_CUTOVER_APPROVED=True,
                               WEMA=without_keys):
            self.transaction_callback(reference, "Successful")
        self.assertEqual(self.row(reference).transaction_status, PENDING)
        self.assertEqual(self.balance(), held)

    # Transaction.service and Beneficiary.name are varchar(80). A holder name over
    # 68 characters overflowed "Transfer to <name>": PostgreSQL raised DataError and
    # every send to that recipient was a 500. Both are cut to the column.
    def test_a_long_holder_name_fits_the_ledger_label(self):
        holder = ("THE REGISTERED TRUSTEES OF THE INCORPORATED ASSOCIATION OF "
                  "LAGOS MARKET TRADERS AND ARTISANS")
        self.bank.holder = holder
        response = self.send_bank(name=holder)
        self.assertEqual(response.status_code, 200, response.json())
        payout = self.row(response.json()["reference"])
        self.assertLessEqual(len(payout.service),
                             Transaction._meta.get_field("service").max_length)
        self.assertLessEqual(len(Beneficiary.objects.get(user=self.user).name),
                             Beneficiary._meta.get_field("name").max_length)

    # The authorisation callback matched the reference key case-insensitively but
    # securityInfo exactly, so a "SecurityInfo" payload was denied and every payout
    # failed "Authentication Failed". Whether the bank sends that casing is
    # unverified; both keys are now matched the same way.
    def test_a_pascal_case_authorisation_callback_keeps_its_security_info(self):
        self.bank.on_transfer = self.authorising(pascal_case=True)
        response = self.send_bank()
        self.assertEqual(response.status_code, 200, response.json())
        self.assertTrue(response.json().get("success"))


class InboundDepositSimulation(LiveSimulationCase):
    def credit_row(self, reference, amount, status="Successfull", **changes):
        row = {"referenceId": reference, "tranId": f"TRN-{reference}", "rrn": reference,
               "amount": amount, "balance": "99999.00", "creditType": "Credit",
               "status": status, "narration": "NIP:GTB-JOHN DOE",
               "date": timezone.localdate().isoformat()}
        row.update(changes)
        return row

    def notify(self):
        # The notification's own amount is never trusted.
        return self.bank_says("notification", {
            "accountNumber": SENDER_ACCOUNT, "transactionType": "Credit",
            "amount": "999999.00"})

    def deposits(self):
        return Transaction.objects.filter(
            user=self.user, direction=Transaction.IN, reference__startswith="WEMA-CR-")

    def test_a_deposit_is_credited_exactly_once_whoever_reads_the_statement(self):
        opening = Decimal(self.opening)
        incomplete = {"referenceId": "DEP-PARTIAL", "amount": "900.00", "creditType": "Credit",
                      "status": "Default", "date": timezone.localdate().isoformat()}
        self.bank.history[SENDER_ACCOUNT] = [
            self.credit_row("DEP-1", "2,500.00"),
            self.credit_row("DEP-1", "2,500.00"),              # the same row listed twice
            self.credit_row("DEP-PENDING", "700.00", status="Pending"),
            self.credit_row("DEP-FAILED", "800.00", status="Failed"),
            incomplete,                                        # no posted-record evidence
            self.credit_row("OUT-1", "100.00", creditType="Debit"),
        ]
        # Notification replay, two cron sweeps and a balance request all read it.
        self.assertEqual(self.notify().status_code, 200)
        self.notify()
        self.poll()
        self.poll()
        self.assertEqual(self.api("/api/wallet_balance/", {}).status_code, 200)
        self.assertGreaterEqual(len(self.bank.history_reads), 4)
        self.assertEqual(self.balance(), opening + Decimal("2500"))
        self.assertEqual([d.reference for d in self.deposits()], ["WEMA-CR-DEP-1"])

        # The pending row lands and the incomplete one gains its posted record.
        self.bank.history[SENDER_ACCOUNT][2] = self.credit_row("DEP-PENDING", "700.00")
        self.bank.history[SENDER_ACCOUNT][4] = self.credit_row(
            "DEP-PARTIAL", "900.00", status="Default")
        self.notify()
        self.notify()
        self.poll()
        self.assertEqual(self.balance(), opening + Decimal("4100"))
        self.assertEqual(self.deposits().count(), 3)
        self.assertFalse(Transaction.objects.filter(reference="WEMA-CR-DEP-FAILED").exists())


@override_settings(DEBUG=False, TESTING=False, WEMA_CUTOVER_APPROVED=False)
class ArchivedPartnershipSimulation(LiveSimulationCase):
    def test_an_unapproved_production_deploy_refuses_transfers_before_any_debit(self):
        recipient = self.recipient()
        bank = self.send_bank()
        self.assertGreaterEqual(bank.status_code, 400, bank.json())
        self.assertFalse(bank.json().get("success"))
        zitch = self.api("/api/transfer/send/", {
            "identifier": recipient.phone, "amount": "1000", "transaction_pin": "1234",
            "idempotency_key": "sim-archived-zitch"})
        self.assertGreaterEqual(zitch.status_code, 400, zitch.json())
        self.assertFalse(zitch.json().get("success"))
        self.assertEqual(self.bank.transfers, [])
        self.assertFalse(Transaction.objects.filter(
            user=self.user, direction=Transaction.OUT).exists())
        self.assertEqual(self.balance(), Decimal(self.opening))
        self.assertEqual(self.balance(recipient), Decimal("0"))


class AmountAndPinEdgeSimulation(LiveSimulationCase):
    def both_endpoints(self, recipient, **fields):
        self._keys += 1
        common = {"transaction_pin": "1234", **fields}
        yield self.api("/api/transfers/send/", {
            "account_number": "0123456789", "bank": "gtb", "name": "John Doe",
            "idempotency_key": f"sim-edge-bank-{self._keys}", **common})
        yield self.api("/api/transfer/send/", {
            "identifier": recipient.phone,
            "idempotency_key": f"sim-edge-zitch-{self._keys}", **common})

    def assert_nothing_moved(self, recipient):
        self.assertEqual(self.bank.transfers, [])
        self.assertEqual(self.bank.enquiries, [])
        self.assertFalse(Transaction.objects.filter(
            user=self.user, direction=Transaction.OUT).exists())
        self.assertEqual(self.balance(), Decimal(self.opening))
        self.assertEqual(self.balance(recipient), Decimal("0"))

    def test_invalid_and_absurd_amounts_never_reach_the_ledger_or_the_bank(self):
        recipient = self.recipient()
        invalid = [0, "0", "0.00", -100, "-100", "abc", "", None, True, [], {"gt": 0},
                   "NaN", "Infinity", "-Infinity", "1e500", "12,000", "0.004", "49.99"]
        absurd = ["99999999999999999999", 1e15]
        for amount in invalid + absurd:
            for response in self.both_endpoints(recipient, amount=amount):
                with self.subTest(amount=amount, path=response.request["PATH_INFO"]):
                    expected = 403 if amount in absurd else 400
                    self.assertEqual(response.status_code, expected, response.json())
        self.assert_nothing_moved(recipient)

    def test_a_sub_kobo_amount_is_truncated_once_and_the_same_everywhere(self):
        response = self.send_bank(amount="1000.999")
        self.assertTrue(response.json().get("success"), response.json())
        payout = self.row(response.json()["reference"])
        self.assertEqual(payout.amount, Decimal("1000.99"))
        self.assertEqual(self.bank.transfers[0]["amount"], 1000.99)
        self.assertEqual(self.balance(), Decimal(self.opening) - Decimal("1000.99") - FEE)

    def test_an_account_with_no_transaction_pin_cannot_move_money(self):
        recipient = self.recipient()
        type(self.user).objects.filter(pk=self.user.pk).update(transaction_pin="")
        for response in self.both_endpoints(recipient, amount="1000"):
            with self.subTest(path=response.request["PATH_INFO"]):
                self.assertEqual(response.status_code, 403, response.json())
        self.assert_nothing_moved(recipient)
