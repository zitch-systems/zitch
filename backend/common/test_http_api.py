import json
from types import SimpleNamespace

from django.test import Client, SimpleTestCase, TestCase, override_settings

from accounts.models import AccessToken
from common.http import idempotent_replay, spend_key
from wallet.models import ReversalEvidence, Transaction
from wallet.services import debit, existing_for_key, get_or_create_wallet
from wallet.tests import make_user


class IdempotentReplayTests(SimpleTestCase):
    """A duplicate describes request identity, never the rail outcome."""

    @staticmethod
    def _body(response):
        return json.loads(response.content)

    def test_pending_replay_stays_pending_and_never_claims_success(self):
        response = idempotent_replay(SimpleNamespace(
            transaction_status=Transaction.PENDING,
            reference="ZTC-PENDING-1",
        ))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._body(response), {
            "pending": True,
            "reference": "ZTC-PENDING-1",
            "message": ("This request is still processing. Its final status will be updated "
                        "after the provider confirms the outcome."),
            "duplicate": True,
        })

    def test_success_replay_preserves_completed_response(self):
        response = idempotent_replay(SimpleNamespace(
            transaction_status=Transaction.SUCCESS,
            reference="ZTC-SUCCESS-1",
        ))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._body(response), {
            "success": True,
            "reference": "ZTC-SUCCESS-1",
            "message": "Already processed",
            "duplicate": True,
        })

    def test_failed_replay_preserves_definitive_failure(self):
        response = idempotent_replay(SimpleNamespace(
            transaction_status=Transaction.FAILED,
            reference="ZTC-FAILED-1",
        ))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._body(response), {
            "message": "This request already failed — please start a new one",
            "code": "duplicate",
        })

    def test_failed_row_with_active_bank_conflict_replays_as_review_not_retry(self):
        response = idempotent_replay(SimpleNamespace(
            transaction_status=Transaction.FAILED,
            reference="ZTC-FAILED-REVIEW-1",
            meta={"wema_reversal_quarantine": {"active": True}},
        ))

        self.assertEqual(response.status_code, 200)
        body = self._body(response)
        self.assertTrue(body["pending"])
        self.assertTrue(body["under_review"])
        self.assertTrue(body["duplicate"])
        self.assertIn("Do not retry", body["message"])

    def test_unknown_replay_status_fails_closed(self):
        response = idempotent_replay(SimpleNamespace(
            transaction_status="LegacyUnknown",
            reference="ZTC-UNKNOWN-1",
        ))

        self.assertEqual(response.status_code, 200)
        body = self._body(response)
        self.assertNotIn("success", body)
        self.assertTrue(body["pending"])
        self.assertTrue(body["unknown"])
        self.assertTrue(body["duplicate"])
        self.assertEqual(body["reference"], "ZTC-UNKNOWN-1")


class IdempotencyBindingTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08010000444", "idem@zitch.test", balance="5000")

    def test_same_client_key_with_changed_material_request_is_a_conflict(self):
        first_key = spend_key("client-attempt-1", self.user, "airtime", "mtn", "0801", "500")
        debit(self.user, "500", "Airtime", idempotency_key=first_key)

        changed_key = spend_key("client-attempt-1", self.user, "airtime", "mtn", "0801", "1000")
        response = idempotent_replay(existing_for_key(self.user, changed_key))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.content)["code"], "idempotency_conflict")
        self.assertEqual(get_or_create_wallet(self.user).balance, 4500)

    def test_same_client_key_and_request_replays_the_original_attempt(self):
        key = spend_key("client-attempt-2", self.user, "airtime", "mtn", "0801", "500")
        txn = debit(self.user, "500", "Airtime", idempotency_key=key)

        retry_key = spend_key("client-attempt-2", self.user, "airtime", "mtn", "0801", "500")
        response = idempotent_replay(existing_for_key(self.user, retry_key))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.content)["pending"])
        self.assertEqual(json.loads(response.content)["reference"], txn.reference)

    def test_indexed_reversal_hold_overrides_failed_replay_without_json_summary(self):
        key = spend_key("client-attempt-review", self.user, "bank", "500")
        txn = debit(self.user, "500", "Bank transfer", idempotency_key=key)
        txn.transaction_status = Transaction.FAILED
        txn.save(update_fields=["transaction_status"])
        evidence = ReversalEvidence.objects.create(
            provider=ReversalEvidence.WEMA,
            provider_reference="ALAT-IDEMPOTENT-REVIEW",
            provider_reference_hash="d" * 64,
            user=self.user,
            payout=txn,
            amount="500.00",
            initial_reason="provider_status_conflict",
            reason="provider_status_conflict",
            state=ReversalEvidence.CONFLICT,
        )
        evidence.associated_payouts.add(txn)

        response = idempotent_replay(existing_for_key(self.user, key))

        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertTrue(body["pending"])
        self.assertTrue(body["under_review"])
        self.assertTrue(body["duplicate"])


class JsonApiBoundaryTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_rejects_non_json_and_non_object_bodies(self):
        wrong_type = self.client.post("/api/sigin/", data="{}", content_type="text/plain")
        self.assertEqual(wrong_type.status_code, 415)
        array = self.client.post("/api/sigin/", data="[]", content_type="application/json")
        self.assertEqual(array.status_code, 400)

    @override_settings(API_MAX_BODY_BYTES=32, DATA_UPLOAD_MAX_MEMORY_SIZE=64)
    def test_rejects_oversized_json_before_parsing(self):
        response = self.client.post(
            "/api/sigin/",
            data=json.dumps({"email_or_phone": "x" * 100, "password": "x"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 413)

    @override_settings(ALLOW_BODY_ACCESS_TOKEN=False)
    def test_production_auth_accepts_bearer_but_not_body_tokens(self):
        user, _ = make_user("08091910001", "bearer-only@zitch.test")
        token = AccessToken.issue(user).key
        body_only = self.client.post(
            "/api/wallet_balance/",
            data=json.dumps({"access_token": token}),
            content_type="application/json",
        )
        self.assertEqual(body_only.status_code, 401)
        bearer = self.client.post(
            "/api/wallet_balance/",
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(bearer.status_code, 200)
