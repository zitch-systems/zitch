"""Tests for virtual cards: create, details reveal, fund, freeze."""
import json
from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings

from wallet.models import Transaction
from wallet.services import get_or_create_wallet
from wallet.tests import make_user

from .issuance import claim_card_issuance
from .models import CardIssuance, VirtualCard


class CardTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08020000001", "kola@zitch.test", balance="50000")
        self._key_seq = 0

    def post(self, path, payload):
        payload = dict(payload)
        if path in ("/api/cards/create/", "/api/cards/fund/") \
                and "idempotency_key" not in payload:
            self._key_seq += 1
            payload["idempotency_key"] = f"card-test-{self._key_seq}"
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def balance(self):
        return get_or_create_wallet(self.user).balance

    def _create(self):
        return self.post("/api/cards/create/", {"access_token": self.token})

    def _provider_card(self, provider: str) -> VirtualCard:
        card = VirtualCard.objects.create(
            user=self.user,
            card_token=("0155500011" if provider == "wema" else "issuer-card-1"),
            brand="Verve",
            last4="1234",
            expiry="12/29",
            holder="KOLA TEST",
        )
        CardIssuance.objects.create(
            user=self.user,
            idempotency_key_hash=(provider[0] * 64),
            reference=f"CI-{provider.upper()}-TEST",
            provider=provider,
            state=CardIssuance.SUCCEEDED,
            card=card,
        )
        return card

    def test_create_mints_one_card(self):
        res, body = self._create()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(len(body["card"]["last4"]), 4)
        self.assertEqual(VirtualCard.objects.filter(user=self.user).count(), 1)

    def test_generic_card_response_exposes_reversible_capabilities(self):
        _, body = self._create()

        self.assertEqual(body["card"]["capabilities"], {
            "can_fund": True,
            "can_unfreeze": True,
            "permanent_block": False,
        })

        _, listed = self.post("/api/cards/list/", {"access_token": self.token})
        self.assertEqual(listed["cards"][0]["capabilities"],
                         body["card"]["capabilities"])

    @override_settings(CARD_PROVIDER="issuer")
    def test_wema_card_response_keeps_issuance_capabilities_after_config_change(self):
        self._provider_card("wema")

        _, body = self.post("/api/cards/list/", {"access_token": self.token})

        self.assertEqual(body["cards"][0]["capabilities"], {
            "can_fund": False,
            "can_unfreeze": False,
            "permanent_block": True,
        })

    def test_create_is_idempotent(self):
        self._create()
        with patch("cards.views.card_issue",
                   side_effect=AssertionError("existing card called issuer twice")) as issue:
            response, body = self._create()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["duplicate"])
        issue.assert_not_called()
        self.assertEqual(VirtualCard.objects.filter(user=self.user).count(), 1)

    def test_create_requires_a_client_idempotency_key(self):
        with patch("cards.views.card_issue") as issue:
            response, body = self.post("/api/cards/create/", {
                "access_token": self.token,
                "idempotency_key": None,
            })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(body["code"], "idempotency_key_required")
        issue.assert_not_called()
        self.assertFalse(CardIssuance.objects.exists())

    def test_create_commits_hashed_intent_before_provider_call(self):
        observed = {}

        def issue(*args, **kwargs):
            intent = CardIssuance.objects.get(user=self.user)
            observed["state"] = intent.state
            observed["reference"] = intent.reference
            observed["hash"] = intent.idempotency_key_hash
            observed["customer_ref"] = kwargs["customer_ref"]
            return {
                "success": True,
                "card_token": "card_durable_1",
                "brand": "Verve",
                "last4": "4321",
                "expiry": "12/29",
            }

        with patch("cards.views.card_issue", side_effect=issue):
            response, body = self.post("/api/cards/create/", {
                "access_token": self.token,
                "idempotency_key": "client-card-key-1",
            })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(observed["state"], CardIssuance.STARTING)
        self.assertEqual(observed["customer_ref"], observed["reference"])
        self.assertEqual(len(observed["hash"]), 64)
        self.assertNotIn("client-card-key-1", observed["hash"])
        intent = CardIssuance.objects.get(user=self.user)
        self.assertEqual(intent.state, CardIssuance.SUCCEEDED)
        self.assertEqual(intent.card_id, body["card"]["id"])

    def test_ambiguous_create_is_durable_and_never_calls_issuer_twice(self):
        payload = {
            "access_token": self.token,
            "idempotency_key": "card-issue-ambiguous-1",
        }
        with patch("cards.views.card_issue", return_value={
                "success": False,
                "pending": True,
                "status": "processing",
                "provider_reference": "issuer-attempt-1",
                "message": "response lost",
             }) as issue:
            first, first_body = self.post("/api/cards/create/", payload)
        with patch("cards.views.card_issue",
                   side_effect=AssertionError("pending retry called issuer twice")) as retry_issue:
            retry, retry_body = self.post("/api/cards/create/", payload)

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first_body["pending"])
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry_body["pending"])
        self.assertEqual(retry_body["reference"], first_body["reference"])
        self.assertFalse(VirtualCard.objects.exists())
        intent = CardIssuance.objects.get(reference=first_body["reference"])
        self.assertEqual(intent.state, CardIssuance.PENDING)
        self.assertEqual(intent.provider_reference, "issuer-attempt-1")
        issue.assert_called_once()
        retry_issue.assert_not_called()

        listed, listed_body = self.post("/api/cards/list/", {"access_token": self.token})
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed_body["issuance"]["reference"], first_body["reference"])

    def test_new_key_cannot_bypass_an_unresolved_issuance(self):
        with patch("cards.views.card_issue", return_value={
                "success": False, "pending": True, "message": "timeout"}):
            first, first_body = self.post("/api/cards/create/", {
                "access_token": self.token, "idempotency_key": "issue-stuck-1"})
        with patch("cards.views.card_issue") as second_issue:
            second, second_body = self.post("/api/cards/create/", {
                "access_token": self.token, "idempotency_key": "issue-stuck-2"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second_body["pending"])
        self.assertEqual(second_body["reference"], first_body["reference"])
        second_issue.assert_not_called()
        self.assertEqual(CardIssuance.objects.count(), 1)

    def test_provider_exception_is_pending_and_replayed_without_a_second_call(self):
        payload = {"access_token": self.token, "idempotency_key": "issue-exception-1"}
        with patch("cards.views.card_issue", side_effect=RuntimeError("after POST")) as issue:
            response, body = self.post("/api/cards/create/", payload)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["pending"])
        self.assertEqual(CardIssuance.objects.get().state, CardIssuance.PENDING)
        issue.assert_called_once()
        with patch("cards.views.card_issue") as retry_issue:
            replay, replay_body = self.post("/api/cards/create/", payload)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay_body["pending"])
        retry_issue.assert_not_called()

    def test_incomplete_success_evidence_is_quarantined(self):
        with patch("cards.views.card_issue", return_value={
                "success": True,
                "card_token": "issuer-created-card",
                "last4": "",
                "expiry": "12/29",
             }):
            response, body = self.post("/api/cards/create/", {
                "access_token": self.token,
                "idempotency_key": "issue-missing-evidence-1",
            })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["pending"])
        self.assertFalse(VirtualCard.objects.exists())
        self.assertEqual(CardIssuance.objects.get().state, CardIssuance.PENDING)

    def test_definitive_failure_replays_but_a_new_key_can_start_fresh(self):
        rejected = {"success": False, "status": "rejected", "message": "Request rejected"}
        with patch("cards.views.card_issue", return_value=rejected) as first_issue:
            first, first_body = self.post("/api/cards/create/", {
                "access_token": self.token, "idempotency_key": "issue-failed-1"})
        self.assertEqual(first.status_code, 422)
        self.assertEqual(first_body["code"], "card_issuance_failed")
        first_issue.assert_called_once()

        with patch("cards.views.card_issue") as retry_issue:
            replay, replay_body = self.post("/api/cards/create/", {
                "access_token": self.token, "idempotency_key": "issue-failed-1"})
        self.assertEqual(replay.status_code, 409)
        self.assertTrue(replay_body["duplicate"])
        retry_issue.assert_not_called()

        with patch("cards.views.card_issue", return_value={
                "success": True, "card_token": "card_second_attempt",
                "last4": "2468", "expiry": "11/30", "brand": "Verve"}) as second_issue:
            success, success_body = self.post("/api/cards/create/", {
                "access_token": self.token, "idempotency_key": "issue-failed-2"})
        self.assertEqual(success.status_code, 200)
        self.assertTrue(success_body["success"])
        second_issue.assert_called_once()
        self.assertEqual(CardIssuance.objects.filter(user=self.user).count(), 2)

    def test_starting_intent_after_a_crash_is_never_resubmitted(self):
        claim = claim_card_issuance(self.user, "issue-crash-1", "issuer")
        self.assertTrue(claim.call_provider)
        with patch("cards.views.card_issue") as issue:
            response, body = self.post("/api/cards/create/", {
                "access_token": self.token, "idempotency_key": "issue-crash-1"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["pending"])
        self.assertEqual(body["reference"], claim.intent.reference)
        issue.assert_not_called()

    def test_database_enforces_one_card_per_user(self):
        self._create()
        with self.assertRaises(IntegrityError), transaction.atomic():
            VirtualCard.objects.create(
                user=self.user,
                card_token="duplicate",
                last4="9999",
                expiry="12/30",
            )

    def test_details_require_correct_pin(self):
        self._create()
        res, _ = self.post("/api/cards/details/", {"access_token": self.token, "transaction_pin": "0000"})
        self.assertEqual(res.status_code, 403)

    def test_details_reveal_pan_and_cvv(self):
        self._create()
        res, body = self.post("/api/cards/details/", {"access_token": self.token, "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["pan"])
        self.assertTrue(body["cvv"])

    def test_fund_debits_wallet_and_loads_card(self):
        self._create()
        res, body = self.post("/api/cards/fund/", {"access_token": self.token, "amount": "10000", "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["card"]["balance"], "10000.00")
        self.assertEqual(self.balance(), Decimal("40000"))

    @override_settings(CARD_PROVIDER="issuer")
    def test_wema_funding_is_rejected_before_pin_debit_or_provider_call(self):
        self._provider_card("wema")

        with patch("cards.views.verify_transaction_pin") as verify_pin, \
             patch("cards.views.claim_card_funding") as claim, \
             patch("cards.views.issuer_fund_card") as provider_call:
            response, body = self.post("/api/cards/fund/", {
                "access_token": self.token,
                "amount": "10000",
                "transaction_pin": "1234",
                "idempotency_key": "wema-card-fund-unsupported",
            })

        self.assertEqual(response.status_code, 422)
        self.assertEqual(body["code"], "card_funding_unsupported")
        self.assertEqual(self.balance(), Decimal("50000"))
        self.assertFalse(Transaction.objects.filter(meta__card_funding=True).exists())
        verify_pin.assert_not_called()
        claim.assert_not_called()
        provider_call.assert_not_called()

    def test_ambiguous_card_funding_holds_the_debit_and_replays_pending(self):
        self._create()
        payload = {
            "access_token": self.token, "amount": "10000", "transaction_pin": "1234",
            "idempotency_key": "card-ambiguous-1",
        }
        with patch("cards.views.issuer_fund_card", return_value={
                "success": False, "pending": True, "message": "timeout",
             }) as fund:
            first, first_body = self.post("/api/cards/fund/", payload)
        with patch("cards.views.issuer_fund_card",
                   side_effect=AssertionError("ambiguous retry called issuer twice")) as retry_fund:
            retry, retry_body = self.post("/api/cards/fund/", payload)

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first_body["pending"])
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry_body["pending"])
        self.assertTrue(retry_body["duplicate"])
        self.assertEqual(self.balance(), Decimal("40000"))
        self.assertEqual(VirtualCard.objects.get(user=self.user).balance, Decimal("0"))
        txn = Transaction.objects.get(reference=first_body["reference"])
        self.assertEqual(txn.transaction_status, Transaction.PENDING)
        self.assertTrue(txn.meta["card_funding"])
        self.assertTrue(txn.meta["reconcile"])
        fund.assert_called_once()
        retry_fund.assert_not_called()

    def test_terminal_card_funding_rejection_refunds_and_returns_terminal_4xx(self):
        self._create()
        payload = {
            "access_token": self.token,
            "amount": "10000",
            "transaction_pin": "1234",
            "idempotency_key": "card-terminal-failure-1",
        }
        with patch("cards.views.issuer_fund_card", return_value={
                "success": False,
                "status": "declined",
                "message": "Card load declined",
             }) as fund:
            response, body = self.post("/api/cards/fund/", payload)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(body["code"], "card_funding_failed")
        self.assertEqual(self.balance(), Decimal("50000"))
        txn = Transaction.objects.get(reference=body["reference"])
        self.assertEqual(txn.transaction_status, Transaction.FAILED)
        fund.assert_called_once()

        with patch("cards.views.issuer_fund_card") as retry_fund:
            replay, replay_body = self.post("/api/cards/fund/", payload)
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(replay_body["code"], "duplicate")
        retry_fund.assert_not_called()
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_unhandled_card_funding_provider_exception_stays_pending(self):
        self._create()
        payload = {
            "access_token": self.token,
            "amount": "10000",
            "transaction_pin": "1234",
            "idempotency_key": "card-provider-exception-1",
        }
        with patch("cards.views.issuer_fund_card",
                   side_effect=RuntimeError("response lost")):
            response, body = self.post("/api/cards/fund/", payload)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["pending"])
        self.assertEqual(self.balance(), Decimal("40000"))
        txn = Transaction.objects.get(reference=body["reference"])
        self.assertEqual(txn.transaction_status, Transaction.PENDING)
        self.assertTrue(txn.meta["reconcile"])

    def test_fund_requires_a_client_idempotency_key(self):
        self._create()
        with patch("cards.views.issuer_fund_card") as fund:
            response, body = self.post("/api/cards/fund/", {
                "access_token": self.token,
                "amount": "10000",
                "transaction_pin": "1234",
                "idempotency_key": None,
            })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(body.get("code"), "idempotency_key_required")
        fund.assert_not_called()
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_new_key_is_blocked_while_an_ambiguous_load_is_unresolved(self):
        self._create()
        base = {"access_token": self.token, "amount": "10000",
                "transaction_pin": "1234"}
        with patch("cards.views.issuer_fund_card", return_value={
                "success": False, "pending": True, "message": "timeout"}):
            first, first_body = self.post("/api/cards/fund/", {
                **base, "idempotency_key": "card-stuck-1"})
        with patch("cards.views.issuer_fund_card") as second_call:
            second, second_body = self.post("/api/cards/fund/", {
                **base, "idempotency_key": "card-stuck-2"})

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first_body["pending"])
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second_body["code"], "card_funding_pending")
        self.assertEqual(second_body["reference"], first_body["reference"])
        second_call.assert_not_called()
        self.assertEqual(self.balance(), Decimal("40000"))

    def test_card_balance_projection_is_applied_exactly_once(self):
        self._create()
        _, body = self.post("/api/cards/fund/", {
            "access_token": self.token, "amount": "10000",
            "transaction_pin": "1234", "idempotency_key": "card-once-1"})
        txn = Transaction.objects.get(reference=body["reference"])

        from cards.services import finalize_card_funding

        self.assertEqual(finalize_card_funding(txn, {"success": True}), "success")
        self.assertEqual(finalize_card_funding(txn, {"success": True}), "success")
        self.assertEqual(VirtualCard.objects.get(user=self.user).balance,
                         Decimal("10000.00"))
        txn.refresh_from_db()
        self.assertTrue(txn.meta["card_balance_applied"])

    def test_fund_rejects_insufficient_balance(self):
        self._create()
        # Tier 2 (₦200k limit) so the amount clears the KYC tier/face gate and is
        # below the ₦100k face-verification threshold — it still exceeds the wallet
        # balance (₦50k), so funding is rejected at the balance check (402) with the
        # wallet untouched. (Card funding now also enforces send limits.)
        self.user.tier = 2
        self.user.save(update_fields=["tier"])
        res, _ = self.post("/api/cards/fund/", {"access_token": self.token, "amount": "60000", "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 402)
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_fund_rejects_above_tier_limit(self):
        # New guard: loading more than the KYC tier allows is blocked up front, so a
        # low-tier user can't use card funding to bypass the transfer limits.
        self._create()
        res, _ = self.post("/api/cards/fund/", {"access_token": self.token, "amount": "999999", "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_fund_blocked_over_daily_spend_cap(self):
        # Card funding now counts toward (and is bounded by) the daily bill/spend
        # aggregate, so it can't be used to move unlimited cash off-platform.
        from wallet.models import Transaction
        self.user.tier = 2  # bill cap = ₦100k/day
        self.user.save(update_fields=["tier"])
        self._create()
        Transaction.objects.create(
            user=self.user, service="Airtime — MTN", amount=Decimal("95000"),
            direction=Transaction.OUT, transaction_status=Transaction.SUCCESS,
            reference="SEED-CARD-DAILY")
        res, body = self.post("/api/cards/fund/", {"access_token": self.token, "amount": "10000", "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 403)  # 95k + 10k > 100k
        self.assertEqual(body.get("code"), "daily_limit_exceeded")
        self.assertEqual(self.balance(), Decimal("50000"))  # not debited

    def test_freeze_then_fund_blocked(self):
        self._create()
        self.post("/api/cards/freeze/", {"access_token": self.token})
        res, _ = self.post("/api/cards/fund/", {"access_token": self.token, "amount": "5000", "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_freeze_toggles_status(self):
        self._create()
        _, b1 = self.post("/api/cards/freeze/", {"access_token": self.token})
        self.assertTrue(b1["card"]["frozen"])
        _, b2 = self.post("/api/cards/freeze/", {"access_token": self.token})
        self.assertFalse(b2["card"]["frozen"])

    @override_settings(CARD_PROVIDER="issuer")
    def test_wema_hotlist_is_permanent_and_never_offers_backend_unfreeze(self):
        card = self._provider_card("wema")
        with patch("cards.views.card_set_status",
                   return_value={"success": True}) as block:
            response, body = self.post("/api/cards/freeze/", {
                "access_token": self.token,
                "card_id": card.id,
            })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["card"]["frozen"])
        self.assertTrue(body["card"]["capabilities"]["permanent_block"])
        self.assertIn("permanently", body["message"].lower())
        self.assertEqual(block.call_args.kwargs["provider"], "wema")
        self.assertFalse(block.call_args.kwargs["active"])

        with patch("cards.views.card_set_status") as unfreeze:
            retry, retry_body = self.post("/api/cards/freeze/", {
                "access_token": self.token,
                "card_id": card.id,
            })
        self.assertEqual(retry.status_code, 422)
        self.assertEqual(retry_body["code"], "card_unfreeze_unsupported")
        unfreeze.assert_not_called()
        card.refresh_from_db()
        self.assertTrue(card.frozen)

    @override_settings(CARD_PROVIDER="issuer")
    def test_ambiguous_wema_hotlist_keeps_local_card_active(self):
        card = self._provider_card("wema")
        with patch("cards.views.card_set_status", return_value={
                "success": False,
                "pending": True,
                "message": "Bank gateway is temporarily unavailable",
             }):
            response, body = self.post("/api/cards/freeze/", {
                "access_token": self.token,
                "card_id": card.id,
            })

        self.assertEqual(response.status_code, 409)
        self.assertTrue(body["pending"])
        self.assertEqual(body["code"], "card_status_pending")
        card.refresh_from_db()
        self.assertFalse(card.frozen)

    @override_settings(CARD_PROVIDER="issuer")
    def test_hotlist_provider_exception_is_pending_and_keeps_local_state(self):
        card = self._provider_card("wema")
        with patch("cards.views.card_set_status",
                   side_effect=RuntimeError("response lost")):
            response, body = self.post("/api/cards/freeze/", {
                "access_token": self.token,
                "card_id": card.id,
            })

        self.assertEqual(response.status_code, 409)
        self.assertTrue(body["pending"])
        card.refresh_from_db()
        self.assertFalse(card.frozen)
