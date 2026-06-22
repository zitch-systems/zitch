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
def _bvn_ok():
    return _resp({"requestSuccessful": True, "responseMessage": "success",
                  "responseBody": {"bvn": "22212345678",
                                   "bvnInformationMatch": {"name": "FULL_MATCH"}}})


def _bvn_invalid():
    return _resp({"requestSuccessful": False, "responseMessage": "Invalid BVN",
                  "responseBody": {}})


def _reserve_ok():
    # V2 getAllAvailableBanks shape: issued accounts under `accounts`.
    return _resp({"requestSuccessful": True, "responseMessage": "success",
                  "responseBody": {"accountReference": "ZITCH-WALLET-X",
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
    """POST /api/wallet/account/create/ — BVN -> verify -> reserve -> tier-up."""

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

    def test_bvn_verifies_provisions_account_and_tiers_up(self, *_):
        def router(url, *a, **kw):
            if "/api/v1/vas/bvn-details-match" in url:
                return _bvn_ok()
            if "/api/v2/bank-transfer/reserved-accounts" in url:
                return _reserve_ok()
            raise AssertionError(f"unexpected POST {url}")

        with patch("utility.providers.requests.post", side_effect=router) as mp:
            res, body = self._create(bvn="22212345678")

        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        # Primary account comes from accounts[0]; the full list is surfaced too.
        self.assertEqual(body["account_number"], "7011223344")
        self.assertEqual(body["bank_name"], "Wema Bank")
        self.assertEqual(body["account_name"], "ADA EZE")
        self.assertEqual(len(body["bank_accounts"]), 2)
        # One BVN both verifies and lifts the tier.
        self.assertTrue(body["bvn_verified"])
        self.assertEqual(body["tier"], 2)
        # Ordering matters: verify the BVN BEFORE minting an account from it.
        urls = [c.args[0] for c in mp.call_args_list]
        self.assertTrue(urls[0].endswith("/api/v1/vas/bvn-details-match"))
        self.assertTrue(urls[1].endswith("/api/v2/bank-transfer/reserved-accounts"))
        # The reserve request carries the BVN and our stable per-user reference.
        reserve_body = mp.call_args_list[1].kwargs["json"]
        self.assertEqual(reserve_body["bvn"], "22212345678")
        self.assertEqual(reserve_body["accountReference"], f"ZITCH-WALLET-{self.user.id}")
        # Persisted: hashed BVN (never raw), tier, and the NUBAN on the wallet.
        u = User.objects.get(pk=self.user.pk)
        self.assertTrue(u.bvn_verified)
        self.assertEqual(u.tier, 2)
        self.assertEqual(u.bvn_last4, "5678")
        self.assertEqual(get_or_create_wallet(u).account_number, "7011223344")

    def test_kyc_failure_blocks_account_and_never_reserves(self, *_):
        """A BVN Monnify can't verify must be rejected up front — no reserve call,
        no account, user stays tier 1 (regression for verify-before-mint ordering)."""
        def router(url, *a, **kw):
            if "/api/v1/vas/bvn-details-match" in url:
                return _bvn_invalid()
            raise AssertionError("reserve must NOT run when KYC fails")

        with patch("utility.providers.requests.post", side_effect=router):
            res, body = self._create(bvn="22212345678")

        self.assertEqual(res.status_code, 400)
        u = User.objects.get(pk=self.user.pk)
        self.assertFalse(u.bvn_verified)
        self.assertEqual(u.tier, 1)
        self.assertFalse(get_or_create_wallet(u).account_number)

    def test_reserve_failure_after_kyc_leaves_user_unverified(self, *_):
        """BVN verifies but Monnify rejects the reserve (name mismatch / product
        not enabled): 502, no account, and the tier is NOT lifted — provisioning,
        not the KYC call alone, is what grants verification."""
        def router(url, *a, **kw):
            if "/api/v1/vas/bvn-details-match" in url:
                return _bvn_ok()
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
