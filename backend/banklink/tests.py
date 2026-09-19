"""Tests for the Mono open-banking provider (utility.mono) and the banklink app.

Two layers, neither needing real Mono credentials:
- MOCK mode (no key): provider functions return success stubs; endpoints work
  end-to-end offline.
- Simulated LIVE mode: utility.mono.requests is patched so functions build the
  real request and parse Mono's {status, data} envelope.
Webhook crediting reuses the wallet's idempotent settle_funding path.
"""
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import requests
from django.test import Client, SimpleTestCase, TestCase, override_settings

from utility import mono
from wallet.models import FundingIntent, Transaction, Wallet
from wallet.tests import make_user
from whatsapp.models import WebhookEvent

from .models import BankConnectSession, LinkedBankAccount

MONO_LIVE = {"BASE_URL": "https://api.withmono.com", "SECRET_KEY": "test_sk",
             "PUBLIC_KEY": "", "WEBHOOK_SECRET": "whsec"}


def _resp(body, status_code=200):
    m = MagicMock(status_code=status_code)
    m.json.return_value = body
    return m


class MonoMockTests(SimpleTestCase):
    def test_mock_mode_active(self):
        self.assertFalse(mono.mono_live())

    def test_exchange_and_account_mock(self):
        r = mono.exchange_token("code-123")
        self.assertTrue(r["success"])
        self.assertTrue(r["account_id"].startswith("mock_acct_"))
        self.assertTrue(mono.get_account(r["account_id"])["success"])

    def test_directpay_mock(self):
        r = mono.initiate_directpay(5000, "ZMONO-1", email="a@b.com")
        self.assertTrue(r["success"])
        self.assertTrue(r["authorization_url"].startswith("mock://mono/directpay/"))

    def test_webhook_accepts_in_dev_without_secret(self):
        with override_settings(MONO={**MONO_LIVE, "SECRET_KEY": "", "WEBHOOK_SECRET": ""}):
            self.assertTrue(mono.verify_webhook({"event": "x"}, ""))


@override_settings(MONO=MONO_LIVE)
class MonoLiveTests(SimpleTestCase):
    def test_mono_live_true(self):
        self.assertTrue(mono.mono_live())

    @patch("utility.mono.requests.post")
    def test_explicit_failed_status_wins_over_connect_and_account_data(self, mock_post):
        mock_post.side_effect = [
            _resp({
                "status": False,
                "data": {"mono_url": "https://connect.mono/should-not-open"},
            }),
            _resp({"status": "failed", "data": {"id": "should-not-link"}}),
        ]

        connect = mono.initiate_connect("zitch://linkbank")
        account = mono.exchange_token("rejected-code")

        self.assertFalse(connect["success"])
        self.assertFalse(account["success"])

    @patch("utility.mono.requests.get")
    def test_explicit_failed_status_wins_over_account_balance_and_history_data(self, mock_get):
        mock_get.side_effect = [
            _resp({
                "status": "failed",
                "data": {"account": {"accountNumber": "0123456789"}},
            }),
            _resp({"status": False, "data": {"balance": 8420010}}),
            _resp({"status": "failed", "data": [{"id": "txn-rejected"}]}),
        ]

        account = mono.get_account("rejected-account")
        balance = mono.get_balance("rejected-account")
        history = mono.get_transactions("rejected-account")

        self.assertFalse(account["success"])
        self.assertFalse(balance["success"])
        self.assertFalse(history["success"])

    @patch("utility.mono.requests.post")
    def test_exchange_live(self, mock_post):
        mock_post.return_value = _resp({"status": "successful", "data": {"id": "acc_99"}})
        r = mono.exchange_token("auth-code")
        self.assertTrue(r["success"])
        self.assertEqual(r["account_id"], "acc_99")
        self.assertTrue(mock_post.call_args[0][0].endswith("/v2/accounts/auth"))
        self.assertEqual(mock_post.call_args[1]["headers"]["mono-sec-key"], "test_sk")

    @patch("utility.mono.requests.get")
    def test_balance_live_converts_kobo(self, mock_get):
        mock_get.return_value = _resp({"status": "successful", "data": {"balance": 8420010}})
        r = mono.get_balance("acc_99")
        self.assertEqual(r["balance_naira"], Decimal("84200.10"))

    @patch("utility.mono.requests.get")
    def test_malformed_or_non_finite_balance_fails_closed_without_crashing(self, mock_get):
        for raw in ("not-a-number", "NaN", "Infinity", None):
            with self.subTest(raw=raw):
                mock_get.return_value = _resp({
                    "status": "successful", "data": {"balance": raw},
                })

                result = mono.get_balance("bad-balance")

                self.assertFalse(result["success"])
                self.assertIsNone(result["balance_naira"])

    @patch("utility.mono.requests.post")
    def test_directpay_live_sends_kobo(self, mock_post):
        mock_post.return_value = _resp({"status": "successful",
                                        "data": {"id": "pay_1",
                                                 "mono_url": "https://pay.mono/x",
                                                 "reference": "ZMONO-1"}})
        r = mono.initiate_directpay(5000, "ZMONO-1", email="a@b.com")
        self.assertTrue(r["success"])
        self.assertEqual(r["authorization_url"], "https://pay.mono/x")
        self.assertEqual(r["reference"], "pay_1")
        self.assertTrue(mock_post.call_args[0][0].endswith("/v2/payments/initiate"))
        self.assertEqual(mock_post.call_args[1]["json"]["amount"], 500000)  # kobo

    @patch("utility.mono.requests.post")
    def test_directpay_failed_envelope_with_data_never_counts_as_success(self, mock_post):
        mock_post.return_value = _resp({
            "status": "failed",
            "message": "Payment was not created",
            "data": {"mono_url": "https://pay.mono/should-not-open"},
        })

        result = mono.initiate_directpay(5000, "ZMONO-FAILED", email="a@b.com")

        self.assertFalse(result["success"])
        self.assertTrue(result["pending"])

    @patch("utility.mono.requests.post")
    def test_directpay_retryable_http_response_is_pending(self, mock_post):
        for status in (429, 500, 503):
            with self.subTest(status=status):
                mock_post.return_value = _resp(
                    {"status": "failed", "message": "temporarily unavailable"},
                    status_code=status,
                )

                result = mono.initiate_directpay(
                    5000, f"ZMONO-HTTP-{status}", email="a@b.com")

                self.assertFalse(result["success"])
                self.assertTrue(result["pending"])
                self.assertEqual(result["http_status"], status)

    @patch("utility.mono.requests.post")
    def test_directpay_incomplete_2xx_response_is_pending(self, mock_post):
        mock_post.return_value = _resp({
            "status": "successful", "data": {"reference": "MONO-ACCEPTED"},
        })

        result = mono.initiate_directpay(5000, "ZMONO-INCOMPLETE", email="a@b.com")

        self.assertFalse(result["success"])
        self.assertTrue(result["pending"])
        self.assertEqual(result["reference"], "MONO-ACCEPTED")

    @patch("utility.mono.requests.post")
    def test_directpay_definitive_client_rejection_is_failed(self, mock_post):
        mock_post.return_value = _resp(
            {"status": "failed", "message": "invalid amount"}, status_code=422)

        result = mono.initiate_directpay(5000, "ZMONO-REJECTED", email="a@b.com")

        self.assertFalse(result["success"])
        self.assertNotIn("pending", result)
        self.assertEqual(result["http_status"], 422)

    @patch("utility.mono.requests.post")
    def test_directpay_timeout_is_pending_not_failed(self, mock_post):
        mock_post.side_effect = requests.Timeout("response lost")

        result = mono.initiate_directpay(5000, "ZMONO-UNKNOWN", email="a@b.com")

        self.assertFalse(result["success"])
        self.assertTrue(result["pending"])
        self.assertEqual(result["reference"], "ZMONO-UNKNOWN")

    def test_webhook_signature(self):
        self.assertTrue(mono.verify_webhook({"event": "x"}, "whsec"))
        self.assertFalse(mono.verify_webhook({"event": "x"}, "wrong"))
        self.assertFalse(mono.verify_webhook({"event": "x"}, ""))


class BanklinkEndpointTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08044400001", "bank@zitch.app")

    def _post(self, path, body):
        return self._post_as(self.token, path, body)

    def _post_as(self, token, path, body):
        return self.client.post(
            path, data=json.dumps({**body, "access_token": token}),
            content_type="application/json",
        )

    def _connect(self, code):
        initiated = self._post(
            "/api/banklink/connect-init/", {"redirect_url": "Zitch://linkbank"},
        )
        state = parse_qs(urlsplit(initiated.json()["mono_url"]).query)["state"][0]
        return self._post("/api/banklink/connect/", {"code": code, "state": state})

    @staticmethod
    def _directpay_event(reference, **overrides):
        payment = {
            "id": "txd_test_payment",
            "reference": reference,
            "status": "successful",
            "verified": True,
            "amount": 500000,
            "currency": "NGN",
            **overrides,
        }
        return {
            "event": "direct_debit.payment_successful",
            "event_id": "evt_test_payment",
            "data": {"type": "onetime-debit", "object": payment},
        }

    def test_connect_init_returns_mono_url(self):
        r = self._post("/api/banklink/connect-init/", {"redirect_url": "Zitch://linkbank"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        # MOCK mode echoes the redirect back pre-filled with a code the app exchanges.
        self.assertIn("zitch://linkbank", body["mono_url"].lower())
        self.assertIn("code=", body["mono_url"])
        self.assertIn("state=", body["mono_url"])
        self.assertEqual(BankConnectSession.objects.filter(user=self.user).count(), 1)

    def test_connect_init_requires_redirect(self):
        self.assertFalse(self._post("/api/banklink/connect-init/", {}).json().get("success"))

    def test_connect_list_refresh_unlink(self):
        r = self._connect("mono-code")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["account"]["bank_name"])
        # list
        items = self._post("/api/banklink/list/", {}).json()["accounts"]
        self.assertEqual(len(items), 1)
        lid = items[0]["id"]
        self.assertTrue(items[0]["account_number"].startswith("****"))
        # refresh
        self.assertEqual(self._post("/api/banklink/refresh/", {"linked_id": lid}).status_code, 200)
        # unlink -> drops from list
        self._post("/api/banklink/unlink/", {"linked_id": lid})
        self.assertEqual(len(self._post("/api/banklink/list/", {}).json()["accounts"]), 0)

    @patch("banklink.views.mono.exchange_token")
    def test_connect_requires_one_time_user_bound_state_before_exchange(self, exchange):
        missing = self._post("/api/banklink/connect/", {"code": "injected-code"})

        self.assertEqual(missing.status_code, 409)
        self.assertEqual(missing.json()["code"], "invalid_connect_state")
        exchange.assert_not_called()

    def test_connect_state_cannot_be_replayed(self):
        initiated = self._post(
            "/api/banklink/connect-init/", {"redirect_url": "Zitch://linkbank"},
        )
        state = parse_qs(urlsplit(initiated.json()["mono_url"]).query)["state"][0]
        body = {"code": "one-time-code", "state": state}

        first = self._post("/api/banklink/connect/", body)
        replay = self._post("/api/banklink/connect/", body)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(replay.json()["code"], "invalid_connect_state")

    def test_existing_provider_account_can_never_be_reassigned_to_another_user(self):
        first = self._connect("shared-mono-code")
        self.assertEqual(first.status_code, 200)
        other, other_token = make_user(
            "08044400002", "other-bank-user@zitch.app",
        )
        initiated = self._post_as(
            other_token, "/api/banklink/connect-init/",
            {"redirect_url": "Zitch://linkbank"},
        )
        state = parse_qs(urlsplit(initiated.json()["mono_url"]).query)["state"][0]

        conflict = self._post_as(
            other_token, "/api/banklink/connect/",
            {"code": "shared-mono-code", "state": state},
        )

        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["code"], "bank_account_already_linked")
        linked = LinkedBankAccount.objects.get()
        self.assertEqual(linked.user_id, self.user.id)
        self.assertFalse(other.linked_banks.exists())

    def test_fund_creates_intent_and_returns_url(self):
        lid = self._connect("c").json()["account"]["id"]
        r = self._post("/api/banklink/fund/", {
            "linked_id": lid, "amount": "5000", "idempotency_key": "create-intent-1",
        })
        self.assertEqual(r.status_code, 200)
        ref = r.json()["reference"]
        intent = FundingIntent.objects.get(reference=ref)
        self.assertEqual(intent.meta["provider"], "mono")
        self.assertEqual(intent.amount, Decimal("5000"))

    @patch("banklink.views.mono.initiate_directpay")
    def test_definitive_directpay_failure_is_terminal_not_transport_unknown(self, initiate):
        initiate.return_value = {
            "success": False,
            "http_status": 422,
            "message": "Invalid funding request",
        }
        lid = self._connect("definitive-failure").json()["account"]["id"]

        response = self._post("/api/banklink/fund/", {
            "linked_id": lid,
            "amount": "5000",
            "idempotency_key": "definitive-failure-key",
        })

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "bank_funding_failed")
        intent = FundingIntent.objects.get()
        self.assertEqual(intent.status, FundingIntent.FAILED)
        self.assertEqual(intent.meta["directpay_state"], "failed")

    @patch("banklink.views.mono.initiate_directpay")
    def test_fund_retry_replays_stored_url_without_second_provider_call(self, initiate):
        initiate.return_value = {
            "success": True,
            "reference": "mono-provider-reference",
            "authorization_url": "https://pay.mono/once",
        }
        lid = self._connect("retry-account").json()["account"]["id"]
        body = {"linked_id": lid, "amount": "5000", "idempotency_key": "fund-attempt-1"}

        first = self._post("/api/banklink/fund/", body)
        second = self._post("/api/banklink/fund/", body)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["reference"], second.json()["reference"])
        self.assertEqual(second.json()["authorization_url"], "https://pay.mono/once")
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(initiate.call_count, 1)
        self.assertEqual(FundingIntent.objects.count(), 1)
        intent = FundingIntent.objects.get()
        self.assertEqual(intent.meta["provider_reference"], "mono-provider-reference")
        self.assertEqual(intent.meta["directpay_state"], "started")

    @patch("banklink.views.mono.initiate_directpay")
    def test_fund_retry_replays_after_linked_account_is_unlinked(self, initiate):
        initiate.return_value = {
            "success": True,
            "reference": "mono-provider-unlinked-replay",
            "authorization_url": "https://pay.mono/unlinked-replay",
        }
        lid = self._connect("unlinked-replay-account").json()["account"]["id"]
        body = {
            "linked_id": lid,
            "amount": "5000",
            "idempotency_key": "fund-unlinked-replay",
        }

        first = self._post("/api/banklink/fund/", body)
        self._post("/api/banklink/unlink/", {"linked_id": lid})
        replay = self._post("/api/banklink/fund/", body)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["duplicate"])
        self.assertEqual(replay.json()["reference"], first.json()["reference"])
        self.assertEqual(
            replay.json()["authorization_url"],
            "https://pay.mono/unlinked-replay",
        )
        self.assertEqual(initiate.call_count, 1)

    @patch("banklink.views.mono.initiate_directpay")
    def test_unlinked_account_cannot_start_a_new_funding_attempt(self, initiate):
        lid = self._connect("unlinked-new-account").json()["account"]["id"]
        self._post("/api/banklink/unlink/", {"linked_id": lid})

        response = self._post("/api/banklink/fund/", {
            "linked_id": lid,
            "amount": "5000",
            "idempotency_key": "fund-new-after-unlink",
        })

        self.assertEqual(response.status_code, 404)
        self.assertEqual(FundingIntent.objects.count(), 0)
        initiate.assert_not_called()

    @patch("banklink.views.mono.initiate_directpay")
    def test_fund_key_cannot_be_reused_for_different_amount(self, initiate):
        initiate.return_value = {
            "success": True,
            "reference": "mono-provider-reference",
            "authorization_url": "https://pay.mono/once",
        }
        lid = self._connect("bound-account").json()["account"]["id"]
        first = self._post("/api/banklink/fund/", {
            "linked_id": lid, "amount": "5000", "idempotency_key": "fund-bound-key",
        })
        conflict = self._post("/api/banklink/fund/", {
            "linked_id": lid, "amount": "6000", "idempotency_key": "fund-bound-key",
        })

        self.assertEqual(first.status_code, 200)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["code"], "idempotency_conflict")
        self.assertEqual(initiate.call_count, 1)
        self.assertEqual(FundingIntent.objects.count(), 1)

    def test_fund_rejects_non_string_idempotency_key(self):
        lid = self._connect("invalid-key-account").json()["account"]["id"]

        response = self._post("/api/banklink/fund/", {
            "linked_id": lid, "amount": "5000", "idempotency_key": {"not": "a string"},
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(FundingIntent.objects.count(), 0)

    def test_fund_requires_a_stable_idempotency_key(self):
        lid = self._connect("missing-key").json()["account"]["id"]

        response = self._post("/api/banklink/fund/", {
            "linked_id": lid, "amount": "5000",
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "idempotency_key_required")
        self.assertEqual(FundingIntent.objects.count(), 0)

    @patch("banklink.views.mono.initiate_directpay")
    def test_ambiguous_fund_is_held_pending_and_retry_does_not_repost(self, initiate):
        initiate.return_value = {
            "success": False,
            "pending": True,
            "reference": "merchant-ref",
            "message": "Bank funding request is processing.",
        }
        lid = self._connect("pending-account").json()["account"]["id"]
        body = {"linked_id": lid, "amount": "5000", "idempotency_key": "fund-pending-key"}

        first = self._post("/api/banklink/fund/", body)
        second = self._post("/api/banklink/fund/", body)

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["pending"])
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["pending"])
        self.assertTrue(second.json()["duplicate"])
        self.assertEqual(first.json()["reference"], second.json()["reference"])
        self.assertEqual(initiate.call_count, 1)
        intent = FundingIntent.objects.get()
        self.assertEqual(intent.status, FundingIntent.PENDING)
        self.assertEqual(intent.meta["directpay_state"], "pending")

    def test_webhook_payment_success_credits_wallet_once(self):
        lid = self._connect("c").json()["account"]["id"]
        ref = self._post("/api/banklink/fund/", {
            "linked_id": lid, "amount": "5000", "idempotency_key": "webhook-credit-1",
        }).json()["reference"]
        event = self._directpay_event(ref)
        r = self.client.post("/api/banklink/webhook/", data=json.dumps(event),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000"))
        # redelivered webhook does not double-credit
        self.client.post("/api/banklink/webhook/", data=json.dumps(event),
                         content_type="application/json")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000"))

    @patch("banklink.views.mono.initiate_directpay")
    def test_webhook_provider_reference_settles_claimed_intent(self, initiate):
        initiate.return_value = {
            "success": True,
            "reference": "mono-provider-123",
            "authorization_url": "https://pay.mono/provider-ref",
        }
        lid = self._connect("provider-reference").json()["account"]["id"]
        response = self._post("/api/banklink/fund/", {
            "linked_id": lid, "amount": "5000", "idempotency_key": "provider-ref-key",
        })
        self.assertNotEqual(response.json()["reference"], "mono-provider-123")
        event = self._directpay_event(
            "unavailable-merchant-reference", id="mono-provider-123",
        )

        result = self.client.post("/api/banklink/webhook/", data=json.dumps(event),
                                  content_type="application/json")

        self.assertEqual(result.status_code, 200)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000"))
        intent = FundingIntent.objects.get()
        self.assertTrue(intent.credited)

    def test_webhook_without_verified_amount_leaves_funding_pending(self):
        intent = FundingIntent.objects.create(
            user=self.user, reference="ZMONO-MISSING-AMOUNT", amount=Decimal("5000"),
            meta={"provider": "mono", "directpay_state": "started"},
        )
        event = self._directpay_event(intent.reference)
        event["data"]["object"].pop("amount")

        result = self.client.post("/api/banklink/webhook/", data=json.dumps(event),
                                  content_type="application/json")

        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["pending"])
        intent.refresh_from_db()
        self.assertFalse(intent.credited)
        self.assertTrue(intent.meta["funding_review"]["active"])
        self.assertEqual(intent.meta["funding_review"]["reason"], "unverified_amount")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("0"))
        self.assertTrue(WebhookEvent.objects.filter(
            source="mono", reference=intent.reference,
            action="funding_review_missing_amount", verified=True,
        ).exists())

    def test_webhook_amount_mismatch_is_held_without_losing_requested_amount(self):
        intent = FundingIntent.objects.create(
            user=self.user, reference="ZMONO-PARTIAL", amount=Decimal("5000"),
            meta={"provider": "mono", "directpay_state": "started"},
        )
        event = self._directpay_event(intent.reference, amount=250000)

        result = self.client.post("/api/banklink/webhook/", data=json.dumps(event),
                                  content_type="application/json")

        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["pending"])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("0"))
        intent.refresh_from_db()
        self.assertEqual(intent.amount, Decimal("5000"))
        self.assertFalse(intent.credited)
        self.assertEqual(intent.meta["funding_review"]["reason"], "amount_mismatch")
        self.assertEqual(intent.meta["funding_review"]["observed_amount"], "2500")

    def test_webhook_prefers_merchant_reference_when_provider_alias_was_lost(self):
        intent = FundingIntent.objects.create(
            user=self.user, reference="ZMONO-MERCHANT-TIMEOUT", amount=Decimal("5000"),
            meta={"provider": "mono", "directpay_state": "pending",
                  "provider_reference": "ZMONO-MERCHANT-TIMEOUT"},
        )
        event = self._directpay_event(
            "mono-provider-never-returned", merchant_ref=intent.reference,
        )

        result = self.client.post("/api/banklink/webhook/", data=json.dumps(event),
                                  content_type="application/json")

        self.assertEqual(result.status_code, 200)
        intent.refresh_from_db()
        self.assertTrue(intent.credited)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000"))

    def test_webhook_never_settles_a_different_funding_provider(self):
        intent = FundingIntent.objects.create(
            user=self.user, reference="ZPAY-NOT-MONO", amount=Decimal("5000"),
            meta={"provider": "wema", "initialize_state": "pending"},
        )
        event = self._directpay_event(intent.reference)

        result = self.client.post("/api/banklink/webhook/", data=json.dumps(event),
                                  content_type="application/json")

        self.assertEqual(result.status_code, 409)
        intent.refresh_from_db()
        self.assertFalse(intent.credited)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("0"))
        self.assertTrue(WebhookEvent.objects.filter(
            source="mono", reference=intent.reference,
            action="unresolved_reference", verified=True,
        ).exists())

    def test_early_provider_reference_callback_is_retried_after_alias_binding(self):
        intent = FundingIntent.objects.create(
            user=self.user, reference="ZMONO-MERCHANT-RACE", amount=Decimal("5000"),
            meta={"provider": "mono", "directpay_state": "starting"},
        )
        event = self._directpay_event(
            "unknown-merchant-reference", id="mono-provider-race",
        )

        early = self.client.post("/api/banklink/webhook/", data=json.dumps(event),
                                 content_type="application/json")
        self.assertEqual(early.status_code, 409)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("0"))

        intent.meta = {**intent.meta, "provider_reference": "mono-provider-race",
                       "directpay_state": "started"}
        intent.save(update_fields=["meta", "updated"])
        retry = self.client.post("/api/banklink/webhook/", data=json.dumps(event),
                                 content_type="application/json")

        self.assertEqual(retry.status_code, 200)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000"))

    def test_legacy_payment_received_name_never_credits(self):
        intent = FundingIntent.objects.create(
            user=self.user, reference="ZMONO-LEGACY-EVENT", amount=Decimal("5000"),
            meta={"provider": "mono", "directpay_state": "started"},
        )
        event = {"event": "mono.events.payment_received", "data": {
            "reference": intent.reference, "amount": 500000, "currency": "NGN",
        }}

        response = self.client.post(
            "/api/banklink/webhook/", data=json.dumps(event),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        intent.refresh_from_db()
        self.assertFalse(intent.credited)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("0"))

    def test_success_event_without_successful_verified_object_is_held(self):
        intent = FundingIntent.objects.create(
            user=self.user, reference="ZMONO-UNVERIFIED-EVENT", amount=Decimal("5000"),
            meta={"provider": "mono", "directpay_state": "started"},
        )
        event = self._directpay_event(
            intent.reference, status="processing", verified=False,
        )

        response = self.client.post(
            "/api/banklink/webhook/", data=json.dumps(event),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["pending"])
        intent.refresh_from_db()
        self.assertFalse(intent.credited)
        self.assertEqual(
            intent.meta["funding_review"]["reason"],
            "unverified_payment_status",
        )
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("0"))

    def test_review_hold_is_sticky_against_a_later_exact_callback(self):
        intent = FundingIntent.objects.create(
            user=self.user, reference="ZMONO-STICKY-REVIEW", amount=Decimal("5000"),
            meta={"provider": "mono", "directpay_state": "started"},
        )
        mismatch = self._directpay_event(intent.reference, amount=250000)
        exact = self._directpay_event(intent.reference, amount=500000)

        first = self.client.post(
            "/api/banklink/webhook/", data=json.dumps(mismatch),
            content_type="application/json",
        )
        second = self.client.post(
            "/api/banklink/webhook/", data=json.dumps(exact),
            content_type="application/json",
        )

        self.assertTrue(first.json()["pending"])
        self.assertTrue(second.json()["pending"])
        intent.refresh_from_db()
        self.assertTrue(intent.meta["funding_review"]["active"])
        self.assertFalse(intent.credited)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("0"))
        self.assertTrue(WebhookEvent.objects.filter(
            source="mono", reference=intent.reference,
            action="funding_review_held",
        ).exists())

    def test_active_review_replay_never_returns_stale_authorization_url(self):
        lid = self._connect("review-replay").json()["account"]["id"]
        payload = {
            "linked_id": lid,
            "amount": "5000",
            "idempotency_key": "review-replay-key",
        }
        started = self._post("/api/banklink/fund/", payload)
        event = self._directpay_event(started.json()["reference"])
        event["data"]["object"].pop("amount")
        self.client.post(
            "/api/banklink/webhook/", data=json.dumps(event),
            content_type="application/json",
        )

        replay = self._post("/api/banklink/fund/", payload)

        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["pending"])
        self.assertEqual(replay.json()["code"], "funding_review")
        self.assertNotIn("authorization_url", replay.json())

    def test_late_conflict_after_credit_replays_review_without_undoing_credit(self):
        lid = self._connect("paid-then-conflict").json()["account"]["id"]
        payload = {
            "linked_id": lid,
            "amount": "5000",
            "idempotency_key": "paid-then-conflict-key",
        }
        started = self._post("/api/banklink/fund/", payload)
        reference = started.json()["reference"]
        exact = self._directpay_event(reference)
        conflict = self._directpay_event(reference, amount=400000)

        self.client.post(
            "/api/banklink/webhook/", data=json.dumps(exact),
            content_type="application/json",
        )
        conflicting = self.client.post(
            "/api/banklink/webhook/", data=json.dumps(conflict),
            content_type="application/json",
        )
        replay = self._post("/api/banklink/fund/", payload)

        self.assertTrue(conflicting.json()["pending"])
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["pending"])
        self.assertEqual(replay.json()["code"], "funding_review")
        self.assertNotIn("success", replay.json())
        intent = FundingIntent.objects.get(reference=reference)
        self.assertTrue(intent.credited)
        self.assertTrue(intent.meta["funding_review"]["active"])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000"))
        self.assertEqual(Transaction.objects.filter(reference=reference).count(), 1)

    def test_credited_flag_without_ledger_is_not_replayed_as_funded(self):
        lid = self._connect("broken-credit").json()["account"]["id"]
        payload = {
            "linked_id": lid,
            "amount": "5000",
            "idempotency_key": "broken-credit-key",
        }
        started = self._post("/api/banklink/fund/", payload)
        intent = FundingIntent.objects.get(reference=started.json()["reference"])
        intent.credited = True
        intent.status = FundingIntent.PAID
        intent.save(update_fields=["credited", "status", "updated"])

        replay = self._post("/api/banklink/fund/", payload)

        self.assertTrue(replay.json()["pending"])
        self.assertNotIn("success", replay.json())
        intent.refresh_from_db()
        self.assertEqual(
            intent.meta["funding_review"]["reason"], "credited_without_ledger",
        )
