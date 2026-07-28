"""Tests for the Wema/ALAT bank-called callbacks (wallet/wema_callbacks.py).

The bank signs nothing, so these endpoints are authenticated by a URL path secret
plus a source-IP allowlist, and neither money-moving handler trusts its payload:
`authorize` decides from our own ledger, `transaction` re-queries the bank.
"""
import json
import os
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from wallet.models import Transaction, Wallet
from wallet.services import debit, get_or_create_wallet
from wallet.tests import make_user

TOKEN = "s3cr3t-callback-token"
WEMA_CB = {"BASE_URL": "https://apiplayground.alat.ng", "CHANNEL_ID": "chan-1",
           "KEYS": {"wallet": "subkey"}, "SECURITY_INFO": "", "SIMULATION": False,
           "CALLBACK_TOKEN": TOKEN, "CALLBACK_TOKEN_PREV": "",
           "CALLBACK_ENFORCE_IPS": False, "CALLBACK_IPS": ["135.236.18.76"],
           "AUTH_MAX_AGE": 900, "AUTH_REQUIRE_SECURITY_INFO": False}


@override_settings(WEMA=WEMA_CB)
class WemaCallbackAuthTests(TestCase):
    """Transport authentication — applied before the body is even parsed."""

    def setUp(self):
        self.client = Client()

    def _post(self, path, payload=None, **extra):
        return self.client.post(path, data=json.dumps(payload or {}),
                                content_type="application/json", **extra)

    def test_wrong_token_is_forbidden(self):
        r = self._post("/webhooks/wema/account/wrong-token", {"data": {"nuban": "0123456789"}})
        self.assertEqual(r.status_code, 403)

    def test_get_is_rejected(self):
        r = self.client.get(f"/webhooks/wema/account/{TOKEN}")
        self.assertEqual(r.status_code, 405)

    def test_slashed_and_slashless_both_route(self):
        # ALAT is profiled with the slashless form; APPEND_SLASH would otherwise turn a
        # slashed POST into a 301 that clients re-issue as a bodyless GET.
        for path in (f"/webhooks/wema/account/{TOKEN}", f"/webhooks/wema/account/{TOKEN}/"):
            self.assertEqual(self._post(path, {"data": {}}).status_code, 200)

    def test_malformed_json_does_not_500(self):
        r = self.client.post(f"/webhooks/wema/account/{TOKEN}", data=b"not json",
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)

    @override_settings(WEMA={**WEMA_CB, "CALLBACK_TOKEN": "new-token",
                             "CALLBACK_TOKEN_PREV": TOKEN})
    def test_previous_token_still_accepted_during_rotation(self):
        self.assertEqual(self._post(f"/webhooks/wema/account/{TOKEN}", {"data": {}}).status_code, 200)
        self.assertEqual(self._post("/webhooks/wema/account/new-token", {"data": {}}).status_code, 200)


@override_settings(WEMA=WEMA_CB, PAYMENT_PROVIDER="wema")
class WemaAccountCallbackTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, _ = make_user("08030000777", "cb@zitch.app")

    def _post(self, payload):
        return self.client.post(f"/webhooks/wema/account/{TOKEN}",
                                data=json.dumps(payload), content_type="application/json")

    def _payload(self, nuban="0155500099", phone="08030000777", email="cb@zitch.app"):
        return {"title": "t", "message": "m", "requestType": 2,
                "data": {"email": email, "nuban": nuban, "nubanName": "ADA EZE",
                         "type": 1, "nubanStatus": "Active", "phoneNumber": phone}}

    @patch("utility.wema.lift_debit_restriction", return_value={"success": True})
    def test_provisions_wallet_from_callback(self, _pnd):
        r = self._post(self._payload())
        self.assertEqual(r.status_code, 200)
        w = Wallet.objects.get(user=self.user)
        self.assertEqual(w.account_number, "0155500099")
        self.assertEqual(w.account_name, "ADA EZE")
        # account_reference is what makes the reconcile poller sweep it for deposits
        self.assertTrue(w.account_reference)

    @patch("utility.wema.lift_debit_restriction", return_value={"success": True})
    def test_replay_is_idempotent(self, _pnd):
        self._post(self._payload())
        self._post(self._payload())
        self.assertEqual(Wallet.objects.filter(account_number="0155500099").count(), 1)

    @patch("utility.wema.lift_debit_restriction", return_value={"success": True})
    def test_bank_local_phone_format_resolves_user(self, _pnd):
        # The bank sends 07011223344-style local numbers; ours may be stored otherwise.
        r = self._post(self._payload(phone="2348030000777"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Wallet.objects.get(user=self.user).account_number, "0155500099")

    def test_unknown_customer_is_recorded_not_guessed(self):
        r = self._post(self._payload(nuban="0999999999", phone="08099999999",
                                     email="nobody@example.com"))
        self.assertEqual(r.status_code, 200)          # never 4xx — the bank would retry
        self.assertFalse(Wallet.objects.filter(account_number="0999999999").exists())

    @patch("utility.wema.lift_debit_restriction", return_value={"success": True})
    def test_email_fallback_resolves_when_phone_is_unrecognised(self, _pnd):
        # The bank's phone spelling may not match ours; a UNIQUE email still identifies
        # the customer.
        r = self._post(self._payload(phone="0000000000"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Wallet.objects.get(user=self.user).account_number, "0155500099")

    def test_ambiguous_email_never_provisions(self):
        # email is NOT unique on the user model — two customers can share one. Attaching
        # a bank account to a guess would be worse than not provisioning at all.
        make_user("08055550001", "shared@zitch.app")
        make_user("08055550002", "shared@zitch.app")
        r = self._post(self._payload(nuban="0888888888", phone="08077777777",
                                     email="shared@zitch.app"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Wallet.objects.filter(account_number="0888888888").exists())

    @patch("utility.wema.lift_debit_restriction", return_value={"success": True})
    def test_never_overwrites_an_existing_different_nuban(self, _pnd):
        self._post(self._payload(nuban="0155500099"))
        self._post(self._payload(nuban="0155500011"))   # a second, different account
        self.assertEqual(Wallet.objects.get(user=self.user).account_number, "0155500099")


@override_settings(WEMA=WEMA_CB)
class WemaAuthenticateCallbackTests(TestCase):
    """The payout authorisation gate. Answering true lets money leave, so every
    condition is checked against our own ledger."""

    def setUp(self):
        self.client = Client()
        self.user, _ = make_user("08030000888", "auth@zitch.app")
        w = get_or_create_wallet(self.user)
        w.balance = Decimal("50000.00")
        w.save(update_fields=["balance"])

    def _post(self, payload):
        return self.client.post(f"/webhooks/wema/authorize/{TOKEN}",
                                data=json.dumps(payload), content_type="application/json")

    def _pending_payout(self, ref="ZTRF-abc123"):
        return debit(self.user, Decimal("1000.00"), "transfer",
                     meta={"reconcile": True, "bank": {"code": "035"}}, reference=ref)

    def test_authorizes_a_fresh_pending_bank_payout(self):
        txn = self._pending_payout()
        r = self._post({"transactionReference": txn.reference, "securityInfo": "opaque"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["authorized"])
        self.assertEqual(body["transactionReference"], txn.reference)

    def test_unknown_reference_is_denied(self):
        r = self._post({"transactionReference": "ZTRF-doesnotexist", "securityInfo": "x"})
        self.assertFalse(r.json()["authorized"])

    def test_settled_payout_is_not_reauthorized(self):
        txn = self._pending_payout()
        txn.transaction_status = Transaction.SUCCESS
        txn.save(update_fields=["transaction_status"])
        self.assertFalse(self._post({"transactionReference": txn.reference}).json()["authorized"])

    def test_non_bank_reference_cannot_authorize_a_payout(self):
        # A VTU purchase row carries no meta["bank"] — it must never authorise a
        # bank payout even though the reference exists.
        txn = debit(self.user, Decimal("500.00"), "airtime", meta={"reconcile": True})
        self.assertFalse(self._post({"transactionReference": txn.reference}).json()["authorized"])

    def test_stale_payout_is_denied(self):
        txn = self._pending_payout()
        Transaction.objects.filter(pk=txn.pk).update(
            created=timezone.now() - timedelta(hours=2))
        self.assertFalse(self._post({"transactionReference": txn.reference}).json()["authorized"])

    def test_missing_reference_is_denied(self):
        self.assertFalse(self._post({"securityInfo": "x"}).json()["authorized"])

    def test_denials_are_indistinguishable(self):
        # No message/detail: the endpoint must not be an oracle for "is a payout in
        # flight right now".
        a = self._post({"transactionReference": "ZTRF-nope1"}).json()
        self.assertEqual(sorted(a), ["authorized", "transactionReference"])

    @override_settings(WEMA={**WEMA_CB, "AUTH_REQUIRE_SECURITY_INFO": True,
                             "SECURITY_INFO": "expected-value"})
    def test_security_info_enforced_when_configured(self):
        txn = self._pending_payout()
        self.assertFalse(self._post({"transactionReference": txn.reference,
                                     "securityInfo": "wrong"}).json()["authorized"])
        self.assertTrue(self._post({"transactionReference": txn.reference,
                                    "securityInfo": "expected-value"}).json()["authorized"])

    @override_settings(WEMA={**WEMA_CB, "AUTH_REQUIRE_SECURITY_INFO": True,
                             "SECURITY_INFO": ""})
    def test_required_but_unconfigured_security_info_denies(self):
        # Misconfiguration must fail closed, never open.
        txn = self._pending_payout()
        self.assertFalse(self._post({"transactionReference": txn.reference,
                                     "securityInfo": "anything"}).json()["authorized"])


@override_settings(WEMA=WEMA_CB)
class WemaTransactionCallbackTests(TestCase):
    """The callback is a trigger, not an oracle — settlement comes from a re-query."""

    def setUp(self):
        self.client = Client()
        self.user, _ = make_user("08030000999", "txn@zitch.app")
        w = get_or_create_wallet(self.user)
        w.balance = Decimal("50000.00")
        w.save(update_fields=["balance"])

    def _post(self, payload):
        return self.client.post(f"/webhooks/wema/transaction/{TOKEN}",
                                data=json.dumps(payload), content_type="application/json")

    def _payload(self, ref, status="Successful"):
        return {"title": "t", "message": "m", "request": 3, "requestType": 3,
                "data": {"status": status, "message": "ok", "narration": "n",
                         "transactionReference": ref,
                         "platformTransactionReference": "WEMA-9",
                         "transactionStan": "000123",
                         "orinalTxnTransactionDate": "0001-01-01T00:00:00"}}

    def _pending_payout(self):
        return debit(self.user, Decimal("1000.00"), "transfer",
                     meta={"reconcile": True, "bank": {"code": "035"}})

    @patch("utility.wema.confirm_transfer_status")
    def test_settles_from_the_requery_not_the_payload(self, mock_status):
        # Payload claims success; the authenticated re-query says pending. The
        # re-query must win — a forged callback cannot settle a payout.
        mock_status.return_value = {"success": False, "pending": True}
        txn = self._pending_payout()
        self._post(self._payload(txn.reference, status="Successful"))
        mock_status.assert_called_once_with(txn.reference)
        txn.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.PENDING)

    @patch("utility.wema.confirm_transfer_status",
           return_value={"success": True, "pending": False})
    def test_stamps_bank_identifiers_under_a_namespaced_key(self, _s):
        txn = self._pending_payout()
        self._post(self._payload(txn.reference))
        txn.refresh_from_db()
        self.assertEqual(txn.meta["wema_callback"]["stan"], "000123")
        self.assertEqual(txn.meta["wema_callback"]["platform_reference"], "WEMA-9")

    @patch("utility.wema.confirm_transfer_status")
    def test_already_settled_row_is_not_requeried(self, mock_status):
        txn = self._pending_payout()
        txn.transaction_status = Transaction.SUCCESS
        txn.save(update_fields=["transaction_status"])
        self._post(self._payload(txn.reference, status="Failed"))
        mock_status.assert_not_called()          # never re-open a settled payout
        txn.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.SUCCESS)

    def test_unknown_reference_is_accepted_and_ignored(self):
        r = self._post(self._payload("ZTRF-unknown"))
        self.assertEqual(r.status_code, 200)     # a 4xx would make the bank retry forever

    def test_callback_never_credits_a_wallet(self):
        # The requestType-3 payload has no amount and no account number; a credit path
        # here would be a money-printing primitive under an IP-only trust model.
        before = Wallet.objects.get(user=self.user).balance
        self._post(self._payload("ZTRF-unknown"))
        self.assertEqual(Wallet.objects.get(user=self.user).balance, before)


@override_settings(WEMA=WEMA_CB)
class WemaCallbacksDiagnoseTests(TestCase):
    """/wema-callbacks-diagnose — the browser check for a host with no shell.

    The four URLs must be PROFILED with ALAT before any rail works, so the cost of
    handing over a wrong one is a silent dead end at the bank. This view is what
    makes them verifiable from an address bar.
    """

    def setUp(self):
        self.client = Client()
        self._env = patch.dict(os.environ, {"DIAG_TOKEN": "diag-secret"})
        self._env.start()
        self.addCleanup(self._env.stop)

    def _get(self, token="diag-secret"):
        return self.client.get("/wema-callbacks-diagnose", {"token": token})

    def _body(self, **kw):
        return json.loads(self._get(**kw).content)["callbacks"]

    def test_requires_the_diagnose_token(self):
        self.assertEqual(self._get(token="wrong").status_code, 403)

    def test_lists_all_four_urls_and_they_all_resolve(self):
        body = self._body()
        self.assertEqual(len(body["routes"]), 4)
        for route in body["routes"]:
            self.assertTrue(route["resolves"], route)
            self.assertIn(TOKEN, route["url"])          # the string to give the bank
        handlers = {r["handler"] for r in body["routes"]}
        self.assertEqual(handlers, {"wema_account_callback", "wema_authenticate_callback",
                                    "wema_transaction_callback", "wema_notification_callback"})

    def test_reports_ready_when_configured(self):
        body = self._body()
        self.assertTrue(body["ready_to_send_to_the_bank"])
        self.assertEqual(body["blockers"], [])
        self.assertTrue(body["wrong_secret_is_refused"])

    def test_never_returns_the_callback_secret_itself(self):
        # The URLs necessarily contain it — nothing else may, and the fingerprint
        # must not be reversible to it.
        body = self._body()
        self.assertNotIn(TOKEN, body["secret_fingerprint"])
        for key, value in body.items():
            if key != "routes":
                self.assertNotIn(TOKEN, str(value), key)

    @override_settings(WEMA={**WEMA_CB, "CALLBACK_TOKEN": ""})
    def test_unset_secret_blocks_the_handover(self):
        body = self._body()
        self.assertFalse(body["ready_to_send_to_the_bank"])
        self.assertTrue(any("WEMA_CALLBACK_TOKEN is unset" in b for b in body["blockers"]))

    @override_settings(WEMA={**WEMA_CB, "CALLBACK_TOKEN": ""})
    def test_warns_when_the_endpoints_would_accept_any_secret(self):
        # An open callback must never be profiled with the bank: from then on the
        # URL is public and anyone can drive it.
        body = self._body()
        self.assertFalse(body["wrong_secret_is_refused"])
        self.assertTrue(any("accept ANY secret" in b for b in body["blockers"]))

    def test_trailing_slash_also_resolves(self):
        # These get pasted into an address bar by hand. APPEND_SLASH only ever ADDS
        # a slash, so a slashless-only route answers a bare HTML 404 for the slashed
        # spelling — which reads as "not deployed", the exact question this endpoint
        # exists to answer.
        r = self.client.get("/wema-callbacks-diagnose/", {"token": "diag-secret"})
        self.assertEqual(r.status_code, 200)

    @patch.dict(os.environ, {"DIAG_TOKEN": "", "WEMA_DIAG_TOKEN": "wema-only-secret"})
    def test_wema_diag_token_also_opens_it(self):
        r = self.client.get("/wema-callbacks-diagnose", {"token": "wema-only-secret"})
        self.assertEqual(r.status_code, 200)

    def test_reports_the_proxy_hop_chain_for_ip_enforcement(self):
        # On a platform that fronts the app with its own proxies, client_ip() can
        # resolve to an INTERNAL hop. An allowlist compared against that refuses every
        # bank callback while looking correctly configured, so the raw chain is shown.
        r = self.client.get("/wema-callbacks-diagnose", {"token": "diag-secret"},
                            HTTP_X_FORWARDED_FOR="41.58.1.9, 10.30.28.8")
        det = json.loads(r.content)["callbacks"]["source_ip_detection"]
        self.assertEqual(det["x_forwarded_for"], ["41.58.1.9", "10.30.28.8"])
        self.assertEqual(det["suggested_trusted_proxy_hops"], 2)   # 41.58.1.9 is 2nd from right

    @override_settings(WEMA={**WEMA_CB, "CALLBACK_ENFORCE_IPS": True},
                       RATELIMIT_TRUSTED_PROXY_HOPS=1)
    def test_enforcing_ips_on_a_private_hop_is_a_blocker(self):
        # hops=1 selects the right-most entry — a private platform address. Turning the
        # allowlist on here would 403 the bank on every call, so it must not read ready.
        r = self.client.get("/wema-callbacks-diagnose", {"token": "diag-secret"},
                            HTTP_X_FORWARDED_FOR="41.58.1.9, 10.30.28.8")
        body = json.loads(r.content)["callbacks"]
        self.assertFalse(body["ready_to_send_to_the_bank"])
        self.assertFalse(body["source_ip_detection"]["safe_to_enable_ip_enforcement"])
        self.assertTrue(any("not a public address" in b for b in body["blockers"]))
        self.assertTrue(any("RATELIMIT_TRUSTED_PROXY_HOPS=2" in b for b in body["blockers"]))

    @override_settings(WEMA={**WEMA_CB, "CALLBACK_ENFORCE_IPS": True},
                       RATELIMIT_TRUSTED_PROXY_HOPS=2)
    def test_correct_hop_count_clears_the_blocker(self):
        r = self.client.get("/wema-callbacks-diagnose", {"token": "diag-secret"},
                            HTTP_X_FORWARDED_FOR="41.58.1.9, 10.30.28.8")
        body = json.loads(r.content)["callbacks"]
        self.assertEqual(body["this_request_came_from"], "41.58.1.9")
        self.assertTrue(body["source_ip_detection"]["safe_to_enable_ip_enforcement"])
        self.assertTrue(body["ready_to_send_to_the_bank"])
