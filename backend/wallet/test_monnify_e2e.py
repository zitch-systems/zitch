"""End-to-end LIVE-mode tests for the two Monnify-backed flows the app leans on:

  1. KYC + dedicated virtual account from a BVN  (POST /api/wallet/account/create/)
  2. Bank account-name enquiry, "get account details" (POST /api/transfers/resolve/)

Unlike the mock-mode suites, these run with Monnify *configured* — ``payments_live``
and ``_monnify_token`` are patched and ``requests.*`` is intercepted with a
URL-dispatching fake — so each endpoint builds the real Monnify request and parses
Monnify's documented ``{requestSuccessful, responseBody}`` envelope. That exercises
the whole wiring (HTTP shape + parse + DB side-effects + step ordering) without the
network, which the mock paths can't prove.
"""
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from accounts.models import AccessToken
from transfers.models import Bank
from wallet.services import get_or_create_wallet

User = get_user_model()


def _resp(body, ok=True):
    m = MagicMock()
    m.ok = ok
    m.json.return_value = body
    return m


# --- Monnify envelopes, by the path fragment each call targets -----------------
def _reserve_ok(reference="ZITCH-WALLET-X"):
    # V2 getAllAvailableBanks shape: issued accounts under `accounts`.
    return _resp({"requestSuccessful": True, "responseMessage": "success",
                  "responseBody": {"accountReference": reference,
                                   "accountName": "ADA EZE",
                                   "reservationReference": "RSV-1",
                                   "accounts": [
                                       {"bankName": "Wema Bank", "accountNumber": "7011223344", "bankCode": "035"},
                                       {"bankName": "Sterling Bank", "accountNumber": "8022334455", "bankCode": "232"}]}})


def _reserve_fail():
    return _resp({"requestSuccessful": False, "responseMessage": "BVN/name mismatch",
                  "responseBody": {}})


@patch("utility.providers._monnify_token", return_value="tok")
@patch("utility.providers.payments_live", return_value=True)
class MonnifyAccountCreateE2E(TestCase):
    """POST /api/wallet/account/create/ — BVN -> reserve -> tier-up.

    Account creation is gated on the reserved-account onboarding alone (which
    validates the BVN itself), NOT the separate VAS identity-match product — a
    contract may not have VAS enabled, and gating on it would block the flow."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create(username="08010000001", phone="08010000001",
                                        email="ada@zitch.test", first_name="Ada", last_name="Eze")
        self.user.set_transaction_pin("1234")
        self.user.save()
        get_or_create_wallet(self.user)
        self.token = AccessToken.issue(self.user).key

    def _create(self, **body):
        r = self.client.post("/api/wallet/account/create/",
                             data=json.dumps({"access_token": self.token, **body}),
                             content_type="application/json")
        return r, r.json()

    def test_bvn_provisions_account_and_tiers_up_via_reserve_only(self, *_):
        # No VAS call: the reserve alone provisions and (by validating the BVN)
        # grants verification. The router rejects any VAS hit to prove it.
        def router(url, *a, **kw):
            if "/api/v2/bank-transfer/reserved-accounts" in url:
                return _reserve_ok(reference=f"ZITCH-WALLET-{self.user.id}")
            raise AssertionError(f"VAS/other Monnify call must not happen: {url}")

        with patch("utility.providers.requests.post", side_effect=router) as mp:
            res, body = self._create(bvn="22212345678")

        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        # Primary account comes from accounts[0]; the full list is surfaced too.
        self.assertEqual(body["account_number"], "7011223344")
        self.assertEqual(body["bank_name"], "Wema Bank")
        self.assertEqual(body["account_name"], "ADA EZE")
        self.assertEqual(len(body["bank_accounts"]), 2)
        # One BVN both provisions the account and lifts the tier.
        self.assertTrue(body["bvn_verified"])
        self.assertEqual(body["tier"], 2)
        # Exactly one Monnify POST — the reserve — carrying the BVN + our reference.
        self.assertEqual(len(mp.call_args_list), 1)
        reserve_body = mp.call_args_list[0].kwargs["json"]
        self.assertEqual(reserve_body["bvn"], "22212345678")
        self.assertEqual(reserve_body["accountReference"], f"ZITCH-WALLET-{self.user.id}")
        # Persisted: hashed BVN (never raw), tier, and the NUBAN on the wallet.
        u = User.objects.get(pk=self.user.pk)
        self.assertTrue(u.bvn_verified)
        self.assertEqual(u.tier, 2)
        self.assertEqual(u.bvn_last4, "5678")
        self.assertEqual(get_or_create_wallet(u).account_number, "7011223344")

    def test_reserve_rejection_blocks_account_and_keeps_user_unverified(self, *_):
        """Monnify rejects the reserve (bad BVN / name mismatch / product off):
        502, no account, and the tier is NOT lifted — provisioning, not an
        un-minted attempt, is what grants verification."""
        def router(url, *a, **kw):
            if "/api/v2/bank-transfer/reserved-accounts" in url:
                return _reserve_fail()
            raise AssertionError(f"unexpected POST {url}")

        # ensure_reserved_account falls back to a GET (recover an orphaned reserve);
        # make that miss too so the wallet stays numberless.
        with patch("utility.providers.requests.post", side_effect=router), \
                patch("utility.providers.requests.get",
                      return_value=_resp({"requestSuccessful": False, "responseBody": {}})):
            res, body = self._create(bvn="22212345678")

        self.assertEqual(res.status_code, 502)
        u = User.objects.get(pk=self.user.pk)
        self.assertFalse(u.bvn_verified)
        self.assertEqual(u.tier, 1)
        self.assertFalse(get_or_create_wallet(u).account_number)


@patch("utility.providers._monnify_token", return_value="tok")
@patch("utility.providers.payments_live", return_value=True)
class MonnifyResolveE2E(TestCase):
    """POST /api/transfers/resolve/ — the "get account details" name enquiry."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create(username="08033300001", phone="08033300001", email="r@zitch.test")
        self.user.set_transaction_pin("1234")
        self.user.save()
        get_or_create_wallet(self.user)
        self.token = AccessToken.issue(self.user).key
        self.bank = Bank.objects.create(code="gtb", name="GTBank", bank_code="058", active=True)

    def _resolve(self, **body):
        r = self.client.post("/api/transfers/resolve/",
                             data=json.dumps({"access_token": self.token, **body}),
                             content_type="application/json")
        return r, r.json()

    def test_resolves_holder_name_with_correct_request(self, *_):
        with patch("utility.providers.requests.get",
                   return_value=_resp({"requestSuccessful": True,
                                       "responseBody": {"accountName": "ADA EZE"}})) as mg:
            res, body = self._resolve(account_number="0123456789", bank="gtb")

        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(body["name"], "ADA EZE")
        # Built the documented enquiry with the NIBSS bank_code (not our slug).
        self.assertTrue(mg.call_args.args[0].endswith("/api/v1/disbursements/account/validate"))
        self.assertEqual(mg.call_args.kwargs["params"], {"accountNumber": "0123456789", "bankCode": "058"})

    def test_auto_detect_returns_bank_and_name(self, *_):
        # No bank supplied -> the server sweeps active banks and returns the match,
        # so the app fills the bank in automatically.
        from django.core.cache import cache
        cache.clear()
        with patch("utility.providers.requests.get",
                   return_value=_resp({"requestSuccessful": True,
                                       "responseBody": {"accountName": "ADA EZE"}})):
            res, body = self._resolve(account_number="0123456789")  # no bank
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(body["name"], "ADA EZE")
        self.assertEqual(body["bank"], "gtb")
        self.assertEqual(len(body["matches"]), 1)

    def test_unresolvable_account_is_rejected_clearly(self, *_):
        with patch("utility.providers.requests.get",
                   return_value=_resp({"requestSuccessful": True, "responseBody": {}})):
            res, body = self._resolve(account_number="0123456789", bank="gtb")
        self.assertEqual(res.status_code, 400)
        self.assertIn("verify", body["message"].lower())

    def test_auth_failure_is_distinct_from_bad_account(self, *_):
        """A token/auth failure must read as "try again", not "invalid account",
        so a key misconfig isn't mistaken for a user error."""
        with patch("utility.providers._monnify_token", return_value=""):
            res, body = self._resolve(account_number="0123456789", bank="gtb")
        self.assertEqual(res.status_code, 400)
        self.assertIn("try again", body["message"].lower())

    def test_short_account_number_never_calls_provider(self, *_):
        with patch("utility.providers.requests.get") as mg:
            res, _ = self._resolve(account_number="123", bank="gtb")
        self.assertEqual(res.status_code, 400)
        mg.assert_not_called()


class MonnifyReservedFundingLoopE2E(TestCase):
    """The whole point of the virtual account: prove money transferred INTO it
    actually lands in the wallet. BVN -> dedicated NUBAN -> Monnify
    SUCCESSFUL_TRANSACTION/RESERVED_ACCOUNT webhook -> wallet credited, once.

    Note: the account-create step runs in LIVE mode (mocked Monnify HTTP); the
    webhook itself runs in MOCK mode so its signature is accepted offline — the
    crediting under test is pure ledger logic, no Monnify call."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create(username="08077700001", phone="08077700001",
                                        email="fund@zitch.test", first_name="Ada", last_name="Eze")
        self.user.set_transaction_pin("1234")
        self.user.save()
        get_or_create_wallet(self.user)
        self.token = AccessToken.issue(self.user).key

    def _provision_account(self):
        ref = f"ZITCH-WALLET-{self.user.id}"

        def router(url, *a, **kw):
            if "/api/v2/bank-transfer/reserved-accounts" in url:
                return _reserve_ok(reference=ref)
            raise AssertionError(f"unexpected POST {url}")

        with patch("utility.providers.payments_live", return_value=True), \
                patch("utility.providers._monnify_token", return_value="tok"), \
                patch("utility.providers.requests.post", side_effect=router):
            r = self.client.post("/api/wallet/account/create/",
                                 data=json.dumps({"access_token": self.token, "bvn": "22212345678"}),
                                 content_type="application/json")
        self.assertEqual(r.status_code, 200)
        return get_or_create_wallet(self.user)

    def _webhook(self, *, txref, amount, reference, dest_account):
        event = {"eventType": "SUCCESSFUL_TRANSACTION",
                 "eventData": {"transactionReference": txref, "amountPaid": amount,
                               "product": {"type": "RESERVED_ACCOUNT", "reference": reference},
                               "destinationAccountInformation": {"accountNumber": dest_account}}}
        return self.client.post("/api/fund/webhook/", data=json.dumps(event),
                                content_type="application/json")

    def _balance(self):
        return get_or_create_wallet(self.user).balance

    def test_inbound_transfer_credits_wallet_exactly_once(self):
        wallet = self._provision_account()
        r1 = self._webhook(txref="MNFY|TXN|001", amount=5000,
                           reference=wallet.account_reference, dest_account=wallet.account_number)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(self._balance(), Decimal("5000"))
        # Monnify redelivers webhooks — must never double-credit.
        self._webhook(txref="MNFY|TXN|001", amount=5000,
                      reference=wallet.account_reference, dest_account=wallet.account_number)
        self.assertEqual(self._balance(), Decimal("5000"))

    def test_funding_via_secondary_linked_bank_still_credits(self):
        """getAllAvailableBanks issues several NUBANs; only the primary is on the
        wallet row. A transfer into a SECONDARY linked account must still credit —
        it maps by product.reference, not the destination number."""
        wallet = self._provision_account()
        self.assertEqual(wallet.account_number, "7011223344")  # primary
        r = self._webhook(txref="MNFY|TXN|SEC", amount=2500,
                          reference=wallet.account_reference,
                          dest_account="8022334455")  # the secondary Sterling NUBAN
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._balance(), Decimal("2500"))
