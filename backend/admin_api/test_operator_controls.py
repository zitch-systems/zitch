"""Operator second factor, and maker/checker on the one action that creates money.

Both were deferred in docs/hardening/GAP_ANALYSIS.md as things that matter once there
is a real ops team. That reasoning holds for most of the portal and fails for a manual
wallet credit: it mints a balance from nothing, and every control downstream — tier
caps, velocity, the ledger — treats that balance as legitimate, because it is.
"""
import base64
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken, OperatorTotp, User
from accounts.totp import DIGITS, code_for, current_step, new_secret, verify
from wallet.models import (
    FundingIntent,
    ReversalEvidence,
    ReversalEvidenceObservation,
    ReversalEvidenceResolution,
    Transaction,
    Wallet,
)
from wallet.services import debit
from wallet.tests import make_user
from whatsapp.models import ApprovalRequest


def _operator(username, role="finance"):
    # phone=None, not "": the column is unique, and seed_ops deliberately leaves it
    # NULL for operators (they authenticate by username or email).
    user = User.objects.create(username=username, email=f"{username}@zitch.ng",
                               phone=None, is_staff=True, is_active=True)
    user.set_password("Ops-passw0rd!")
    user.save()
    group, _ = Group.objects.get_or_create(name=role)
    user.groups.add(group)
    return user


def _admin_token(user):
    return AccessToken.issue(user, scope=AccessToken.ADMIN).key


class TotpAlgorithmTests(TestCase):
    def test_matches_the_rfc_6238_appendix_b_vector(self):
        # RFC 6238 Appendix B: the seed is the ASCII string "12345678901234567890",
        # and at T=59 (step 1) SHA-1/8-digit gives 94287082.
        #
        # Derived here rather than pasted as a base32 literal for two reasons: the
        # derivation shows WHERE the value comes from instead of asking a reader to
        # trust an opaque blob, and the literal is 32 chars of high-entropy base32 that
        # the CI secret scan flags as a generic API key (correctly — it cannot tell a
        # published test vector from a live TOTP seed).
        secret = base64.b32encode(b"12345678901234567890").decode("ascii")
        self.assertEqual(code_for(secret, 59 // 30), "94287082"[-DIGITS:])

    def test_accepts_one_step_of_clock_drift(self):
        secret = new_secret()
        now = current_step()
        for step in (now - 1, now, now + 1):
            self.assertIsNotNone(verify(secret, code_for(secret, step)))
        self.assertIsNone(verify(secret, code_for(secret, now + 5)))

    def test_a_used_step_is_refused(self):
        # Single-use is what makes the drift window safe: without it a shoulder-surfed
        # code is replayable for up to 90 seconds.
        secret = new_secret()
        step = current_step()
        code = code_for(secret, step)
        self.assertEqual(verify(secret, code), step)
        self.assertIsNone(verify(secret, code, after_step=step))

    def test_malformed_input_is_rejected_without_raising(self):
        secret = new_secret()
        for bad in ("", "abc", "12345", "1234567", None, "12 34 56"):
            self.assertIsNone(verify(secret, bad))

    def test_provisioning_uri_carries_the_parameters_apps_need(self):
        from accounts.totp import provisioning_uri
        uri = provisioning_uri("ABCDEFGH", account="ada@zitch.ng")
        self.assertTrue(uri.startswith("otpauth://totp/Zitch%3Aada%40zitch.ng?"))
        self.assertIn("secret=ABCDEFGH", uri)
        self.assertIn("digits=6", uri)
        self.assertIn("period=30", uri)


class MfaEnrolmentTests(TestCase):
    def setUp(self):
        self.op = _operator("ada")
        self.token = _admin_token(self.op)

    def _post(self, path, body=None):
        return self.client.post(f"/api/admin/{path}", data=json.dumps(body or {}),
                                content_type="application/json",
                                HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def test_enrol_then_confirm_turns_it_on(self):
        res = self._post("mfa/enroll")
        secret = res.json()["secret"]
        self.assertIn("otpauth://totp/", res.json()["otpauth_uri"])
        # Unconfirmed must NOT gate login — a half-finished enrolment locking an
        # operator out of the portal is the worst outcome here.
        stored = OperatorTotp.objects.get(user=self.op)
        self.assertFalse(stored.confirmed)
        self.assertTrue(stored.secret.startswith("enc:v1:"))
        self.assertNotIn(secret, stored.secret)
        self.assertEqual(stored.plaintext_secret(), secret)

        res = self._post("mfa/confirm", {"code": code_for(secret, current_step())})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(OperatorTotp.objects.get(user=self.op).confirmed)

    def test_confirm_refuses_a_wrong_code(self):
        self._post("mfa/enroll")
        res = self._post("mfa/confirm", {"code": "000000"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["code"], "mfa_invalid")
        self.assertFalse(OperatorTotp.objects.get(user=self.op).confirmed)

    def test_corrupt_encrypted_seed_fails_closed(self):
        self._post("mfa/enroll")
        OperatorTotp.objects.filter(user=self.op).update(secret="enc:v1:not-a-token")
        res = self._post("mfa/confirm", {"code": "123456"})
        self.assertEqual((res.status_code, res.json()["code"]), (403, "mfa_invalid"))

    def test_re_enrolling_a_confirmed_factor_needs_a_current_code(self):
        # Otherwise a hijacked operator session could swap the factor for one the
        # attacker controls, making it decorative.
        res = self._post("mfa/enroll")
        secret = res.json()["secret"]
        self._post("mfa/confirm", {"code": code_for(secret, current_step())})

        res = self._post("mfa/enroll")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["code"], "mfa_code_required")
        self.assertEqual(OperatorTotp.objects.get(user=self.op).plaintext_secret(), secret)

    def test_disabling_needs_a_current_code(self):
        secret = self._post("mfa/enroll").json()["secret"]
        self._post("mfa/confirm", {"code": code_for(secret, current_step())})

        self.assertEqual(self._post("mfa/disable", {"code": "000000"}).status_code, 403)
        self.assertTrue(OperatorTotp.objects.filter(user=self.op).exists())

        # A fresh step, since confirm burned the current one.
        code = code_for(secret, current_step() + 1)
        res = self._post("mfa/disable", {"code": code})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(OperatorTotp.objects.filter(user=self.op).exists())

    def test_the_secret_is_never_re_displayed(self):
        secret = self._post("mfa/enroll").json()["secret"]
        self._post("mfa/confirm", {"code": code_for(secret, current_step())})
        body = self._post("mfa/status").json()
        self.assertTrue(body["enrolled"])
        self.assertNotIn("secret", body)


class MfaLoginTests(TestCase):
    def setUp(self):
        self.op = _operator("bola")

    def _enrol(self):
        secret = new_secret()
        OperatorTotp.objects.create(user=self.op, secret=secret, confirmed=True,
                                    confirmed_at=timezone.now())
        return secret

    def _login(self, path, body):
        return self.client.post(path, data=json.dumps(body), content_type="application/json")

    def test_a_confirmed_factor_is_demanded_at_login(self):
        self._enrol()
        res = self._login("/api/admin/login", {"username": "bola", "password": "Ops-passw0rd!"})
        self.assertEqual(res.status_code, 401)
        # A distinct code, so the client prompts for a code instead of showing "wrong
        # password" for a correct one.
        self.assertEqual(res.json()["code"], "mfa_required")

    def test_a_valid_code_signs_in(self):
        secret = self._enrol()
        res = self._login("/api/admin/login",
                          {"username": "bola", "password": "Ops-passw0rd!",
                           "code": code_for(secret, current_step())})
        self.assertEqual(res.status_code, 200)
        self.assertIn("token", res.json())

    def test_a_code_cannot_be_replayed(self):
        secret = self._enrol()
        code = code_for(secret, current_step())
        body = {"username": "bola", "password": "Ops-passw0rd!", "code": code}
        self.assertEqual(self._login("/api/admin/login", body).status_code, 200)
        replayed = self._login("/api/admin/login", body)
        self.assertEqual(replayed.status_code, 401)
        self.assertEqual(replayed.json()["code"], "mfa_invalid")

    def test_the_ops_surface_enforces_it_too(self):
        # An MFA gate on one operator login form and not the other is no gate at all.
        secret = self._enrol()
        res = self._login("/api/ops/login/",
                          {"identifier": "bola", "password": "Ops-passw0rd!"})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["code"], "mfa_required")

        res = self._login("/api/ops/login/",
                          {"identifier": "bola", "password": "Ops-passw0rd!",
                           "code": code_for(secret, current_step())})
        self.assertEqual(res.status_code, 200)

    def test_an_operator_without_a_factor_is_unaffected_by_default(self):
        res = self._login("/api/admin/login", {"username": "bola", "password": "Ops-passw0rd!"})
        self.assertEqual(res.status_code, 200)

    @override_settings(OPS_REQUIRE_MFA=True)
    def test_require_mfa_blocks_an_unenrolled_money_role_with_an_actionable_message(self):
        res = self._login("/api/admin/login", {"username": "bola", "password": "Ops-passw0rd!"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["code"], "mfa_enrolment_required")
        self.assertIn("enrol", res.json()["message"])

    @override_settings(OPS_REQUIRE_MFA=True)
    def test_require_mfa_does_not_block_a_read_only_operator(self):
        # A read-only account can move nothing; forcing enrolment on it buys nothing and
        # gives people a reason to resent the control.
        viewer = _operator("chidi", role="read_only")
        res = self._login("/api/admin/login",
                          {"username": viewer.username, "password": "Ops-passw0rd!"})
        self.assertEqual(res.status_code, 200)


@override_settings(ADMIN_MAX_MANUAL_CREDIT=500000, OPS_REQUIRE_DUAL_APPROVAL=True)
class MakerCheckerTests(TestCase):
    def setUp(self):
        self.maker = _operator("maker")
        self.checker = _operator("checker")
        self.customer, _ = make_user("08077770001", "cust@zitch.app")

    def _credit(self, operator, amount, reason="Goodwill for the outage"):
        return self.client.post(
            "/api/admin/wallet/credit",
            data=json.dumps({"uid": self.customer.id, "amount": str(amount), "reason": reason}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_admin_token(operator)}")

    def _decide(self, operator, req_id, approve, note=""):
        return self.client.post(
            "/api/admin/approvals/decide",
            data=json.dumps({"id": req_id, "approve": approve, "note": note}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_admin_token(operator)}")

    def _balance(self):
        return Wallet.objects.get(user=self.customer).balance

    def test_an_over_cap_credit_is_held_and_credits_nothing(self):
        res = self._credit(self.maker, Decimal("900000"))
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["pending_approval"])
        self.assertEqual(self._balance(), Decimal("0"))
        req = ApprovalRequest.objects.get(pk=body["approval_id"])
        self.assertEqual(req.status, ApprovalRequest.PENDING)
        self.assertEqual(req.requested_by_id, self.maker.id)

    def test_a_second_operator_approving_performs_the_credit(self):
        req_id = self._credit(self.maker, Decimal("900000")).json()["approval_id"]
        res = self._decide(self.checker, req_id, True)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], ApprovalRequest.EXECUTED)
        self.assertEqual(self._balance(), Decimal("900000"))

    def test_the_maker_cannot_approve_their_own_request(self):
        # A maker/checker control the maker can self-check is an extra click, not a
        # control. This is the whole point of the mechanism.
        req_id = self._credit(self.maker, Decimal("900000")).json()["approval_id"]
        res = self._decide(self.maker, req_id, True)
        self.assertEqual(res.status_code, 409)
        self.assertIn("cannot approve your own", res.json()["message"])
        self.assertEqual(self._balance(), Decimal("0"))

    def test_rejecting_credits_nothing_and_closes_the_request(self):
        req_id = self._credit(self.maker, Decimal("900000")).json()["approval_id"]
        res = self._decide(self.checker, req_id, False, note="No evidence of the outage")
        self.assertEqual(res.json()["status"], ApprovalRequest.REJECTED)
        self.assertEqual(self._balance(), Decimal("0"))

    def test_string_false_cannot_be_coerced_into_an_approval(self):
        req_id = self._credit(self.maker, Decimal("900000")).json()["approval_id"]
        res = self.client.post(
            "/api/admin/approvals/decide",
            data=json.dumps({"id": req_id, "approve": "false"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_admin_token(self.checker)}",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["code"], "invalid_decision")
        self.assertEqual(ApprovalRequest.objects.get(pk=req_id).status,
                         ApprovalRequest.PENDING)
        self.assertEqual(self._balance(), Decimal("0"))

    def test_a_decided_request_cannot_be_decided_again(self):
        # Deciding twice would run the executor twice, which for a credit means
        # crediting twice.
        req_id = self._credit(self.maker, Decimal("900000")).json()["approval_id"]
        self._decide(self.checker, req_id, True)
        again = self._decide(self.checker, req_id, True)
        self.assertEqual(again.status_code, 409)
        self.assertEqual(self._balance(), Decimal("900000"))

    def test_a_within_cap_credit_still_goes_straight_through(self):
        # Dual approval must not change the everyday path.
        res = self._credit(self.maker, Decimal("10000"))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])
        self.assertEqual(self._balance(), Decimal("10000"))

    def test_the_queue_marks_the_viewers_own_requests(self):
        self._credit(self.maker, Decimal("900000"))
        res = self.client.post(
            "/api/admin/approvals/list", data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_admin_token(self.maker)}")
        row = res.json()["rows"][0]
        self.assertTrue(row["is_own_request"])
        self.assertEqual(row["action"], "wallet.credit")

    def test_a_failing_execution_is_recorded_not_swallowed(self):
        req_id = self._credit(self.maker, Decimal("900000")).json()["approval_id"]
        # Make the target unresolvable the way reality would: the account becomes staff.
        self.customer.is_staff = True
        self.customer.save(update_fields=["is_staff"])
        res = self._decide(self.checker, req_id, True)
        self.assertEqual(res.json()["status"], ApprovalRequest.FAILED)
        self.assertIn("error", res.json()["result"])
        self.assertEqual(self._balance(), Decimal("0"))

    @override_settings(OPS_REQUIRE_DUAL_APPROVAL=False)
    def test_with_the_flag_off_an_over_cap_credit_is_refused_as_before(self):
        # The flag exists so enabling maker/checker is deliberate: a queue nobody can
        # drain would silently break a working workflow.
        res = self._credit(self.maker, Decimal("900000"))
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["code"], "credit_limit")
        self.assertEqual(self._balance(), Decimal("0"))

    def test_a_support_role_cannot_decide_a_money_approval(self):
        req_id = self._credit(self.maker, Decimal("900000")).json()["approval_id"]
        support = _operator("support-op", role="support")
        res = self._decide(support, req_id, True)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self._balance(), Decimal("0"))


class ReversalResolutionTests(TestCase):
    def setUp(self):
        self.maker = _operator("reversal-maker")
        self.checker = _operator("reversal-checker")
        self.customer, _ = make_user(
            "08077770002", "reversal-customer@zitch.app", balance="5000")
        self.payout = debit(
            self.customer, Decimal("1000"), "Bank transfer",
            meta={"bank": "Wema", "recipient_account": "0123456789"},
            reference="ZTRFREVERSAL001",
        )
        self.payout.transaction_status = Transaction.SUCCESS
        self.payout.meta = {
            **self.payout.meta,
            "wema_reversal_quarantine": {
                "active": True,
                "reason": "partial_amount",
                "reasons": ["partial_amount"],
                "inbound_reference": "BANKRETURN001",
                "inbound_references": ["BANKRETURN001"],
                "ledger_reference": "WEMA-CR-BANKRETURN001",
                "received_amount": "500.00",
                "payout_amount": "1000.00",
            },
        }
        self.payout.save(update_fields=["transaction_status", "meta"])
        Transaction.objects.create(
            user=self.customer,
            service="Payout reversal evidence",
            amount=Decimal("500"),
            direction=Transaction.IN,
            transaction_status=Transaction.FAILED,
            reference="WEMA-CR-BANKRETURN001",
            meta={
                "internal_evidence": True,
                "wema_reversal_evidence": {
                    "payout_reference": self.payout.reference,
                    "inbound_reference": "BANKRETURN001",
                    "received_amount": "500.00",
                    "payout_amount": "1000.00",
                    "matched": True,
                },
            },
        )

    def _request(self, operator, disposition="confirm_partial_return", *,
                 confirmed_amount=None, evidence_reference=""):
        payload = {
            "reference": self.payout.reference,
            "disposition": disposition,
            "reason": "Bank evidence reviewed and matched",
        }
        if confirmed_amount is not None:
            payload["confirmed_amount"] = str(confirmed_amount)
        if evidence_reference:
            payload["evidence_reference"] = evidence_reference
        return self.client.post(
            "/api/admin/txn/reversal-resolution",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_admin_token(operator)}",
        )

    def _decide(self, operator, req_id, approve=True):
        return self.client.post(
            "/api/admin/approvals/decide",
            data=json.dumps({"id": req_id, "approve": approve}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_admin_token(operator)}",
        )

    @override_settings(OPS_REQUIRE_DUAL_APPROVAL=False)
    def test_partial_return_always_requires_a_second_operator(self):
        before = Wallet.objects.get(user=self.customer).balance
        requested = self._request(self.maker)
        self.assertEqual(requested.status_code, 200)
        self.assertTrue(requested.json()["pending_approval"])
        self.assertEqual(Wallet.objects.get(user=self.customer).balance, before)

        decided = self._decide(self.checker, requested.json()["approval_id"])
        self.assertEqual(decided.status_code, 200)
        self.assertEqual(decided.json()["status"], ApprovalRequest.EXECUTED)
        self.assertEqual(Wallet.objects.get(user=self.customer).balance,
                         Decimal("4500"))
        self.payout.refresh_from_db()
        marker = self.payout.meta["wema_reversal_quarantine"]
        self.assertFalse(marker["active"])
        self.assertEqual(marker["resolution"]["disposition"],
                         "confirm_partial_return")
        adjustment = Transaction.objects.get(
            meta__reversal_resolution=True,
            meta__approval_id=requested.json()["approval_id"],
        )
        self.assertEqual(adjustment.direction, Transaction.IN)
        self.assertEqual(adjustment.amount, Decimal("500"))
        self.assertTrue((adjustment.meta or {}).get("internal_movement"))
        resolution = ReversalEvidenceResolution.objects.get(
            approval_id=requested.json()["approval_id"])
        self.assertEqual(resolution.movement_transaction, adjustment)
        self.assertEqual(resolution.confirmed_amount, Decimal("500"))

    def test_maker_cannot_approve_their_own_resolution(self):
        request_id = self._request(self.maker).json()["approval_id"]
        response = self._decide(self.maker, request_id)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Wallet.objects.get(user=self.customer).balance,
                         Decimal("4000"))


    def test_repeating_a_request_reuses_the_pending_approval(self):
        first = self._request(self.maker).json()["approval_id"]
        second = self._request(self.maker).json()["approval_id"]
        self.assertEqual(first, second)
        self.assertEqual(ApprovalRequest.objects.filter(
            action="wallet.reversal_quarantine_resolution").count(), 1)

    def test_full_reversal_does_not_create_a_second_credit_row(self):
        marker = dict(self.payout.meta["wema_reversal_quarantine"])
        marker["received_amount"] = "1000.00"
        marker["inbound_reference"] = "BANKRETURNFULL001"
        marker["ledger_reference"] = "WEMA-CR-BANKRETURNFULL001"
        self.payout.meta = {**self.payout.meta,
                            "wema_reversal_quarantine": marker}
        self.payout.save(update_fields=["meta"])
        Transaction.objects.create(
            user=self.customer,
            service="Payout reversal evidence",
            amount=Decimal("1000.00"),
            direction=Transaction.IN,
            transaction_status=Transaction.FAILED,
            reference="WEMA-CR-BANKRETURNFULL001",
            meta={
                "internal_evidence": True,
                "wema_reversal_evidence": {
                    "payout_reference": self.payout.reference,
                    "inbound_reference": "BANKRETURNFULL001",
                    "received_amount": "1000.00",
                    "payout_amount": "1000.00",
                    "matched": True,
                },
            },
        )

        requested = self._request(self.maker, "confirm_full_reversal")
        self._decide(self.checker, requested.json()["approval_id"])
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.transaction_status, Transaction.FAILED)
        self.assertEqual(Wallet.objects.get(user=self.customer).balance,
                         Decimal("5000"))
        self.assertFalse(Transaction.objects.filter(
            meta__reversal_resolution=True,
            meta__approval_id=requested.json()["approval_id"],
        ).exists())

    def test_resolved_evidence_is_not_reopened_by_the_next_bank_sweep(self):
        requested = self._request(self.maker)
        self._decide(self.checker, requested.json()["approval_id"])
        before = Wallet.objects.get(user=self.customer).balance

        from wallet.services import apply_wema_credit

        row = {
            "referenceId": "BANKRETURN001",
            "amount": "500.00",
            "creditType": "Credit",
            "status": "Successfull",
            "narration": f"REFUND {self.payout.reference}",
        }
        self.assertIsNone(apply_wema_credit(
            Wallet.objects.get(user=self.customer), row, [self.payout.reference]))

        self.payout.refresh_from_db()
        self.assertFalse(self.payout.meta["wema_reversal_quarantine"]["active"])
        self.assertEqual(Wallet.objects.get(user=self.customer).balance, before)
        self.assertEqual(Transaction.objects.filter(
            meta__reversal_resolution=True).count(), 1)

    def test_approval_fails_closed_if_evidence_changes_while_pending(self):
        requested = self._request(self.maker)
        evidence = ReversalEvidence.objects.get(payout=self.payout)
        ReversalEvidenceObservation.objects.create(
            evidence=evidence, amount=Decimal("600.00"))
        evidence.state = ReversalEvidence.CONFLICT
        evidence.reason = "evidence_amount_changed"
        evidence.version += 1
        evidence.save(update_fields=["state", "reason", "version", "last_seen"])

        decided = self._decide(self.checker, requested.json()["approval_id"])

        self.assertEqual(decided.json()["status"], ApprovalRequest.FAILED)
        self.assertIn("evidence changed", decided.json()["result"]["error"])
        self.assertEqual(Wallet.objects.get(user=self.customer).balance,
                         Decimal("4000"))

    def test_amount_conflict_requires_and_binds_an_observed_amount(self):
        from wallet.services import reversal_quarantine_evidence

        reversal_quarantine_evidence(self.payout)
        evidence = ReversalEvidence.objects.get(payout=self.payout)
        ReversalEvidenceObservation.objects.create(
            evidence=evidence, amount=Decimal("600.00"))
        evidence.state = ReversalEvidence.CONFLICT
        evidence.reason = "evidence_amount_changed"
        evidence.version += 1
        evidence.save(update_fields=["state", "reason", "version", "last_seen"])

        missing = self._request(self.maker, "credit_as_deposit")
        self.assertEqual(missing.status_code, 409)
        self.assertEqual(missing.json()["code"], "confirmed_amount_required")

        requested = self._request(
            self.maker, "credit_as_deposit", confirmed_amount="600.00")
        self.assertEqual(requested.status_code, 200)
        payload = ApprovalRequest.objects.get(pk=requested.json()["approval_id"]).payload
        self.assertEqual(payload["confirmed_amount"], "600.00")
        decided = self._decide(self.checker, requested.json()["approval_id"])
        self.assertEqual(decided.json()["status"], ApprovalRequest.EXECUTED)
        self.assertEqual(Wallet.objects.get(user=self.customer).balance,
                         Decimal("4600.00"))
        resolution = ReversalEvidenceResolution.objects.get(
            approval_id=requested.json()["approval_id"])
        self.assertEqual(resolution.confirmed_amount, Decimal("600.00"))
        self.assertTrue(resolution.movement_transaction.meta["internal_movement"])

    def test_unmatched_case_is_listed_and_can_be_released_as_a_deposit(self):
        from wallet.services import apply_wema_credit

        row = {
            "referenceId": "ALAT-UNMATCHED-OPS",
            "amount": "250.00",
            "creditType": "Credit",
            "status": "Successfull",
            "narration": "TRANSFER REVERSAL - ORIGINAL REFERENCE UNAVAILABLE",
        }
        with patch("utility.alerts.alert"):
            apply_wema_credit(Wallet.objects.get(user=self.customer), row, self_refs=[])
        evidence = ReversalEvidence.objects.get(
            provider_reference="ALAT-UNMATCHED-OPS")

        listed = self.client.get(
            "/api/admin/txn/reversal-cases",
            HTTP_AUTHORIZATION=f"Bearer {_admin_token(self.maker)}",
        )
        self.assertEqual(listed.status_code, 200)
        self.assertIn(evidence.pk, {item["id"] for item in listed.json()["cases"]})

        requested = self.client.post(
            "/api/admin/txn/reversal-resolution",
            data=json.dumps({
                "reference": "",
                "evidence_reference": evidence.provider_reference,
                "disposition": "credit_as_deposit",
                "reason": "Bank statement confirms ordinary customer deposit",
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_admin_token(self.maker)}",
        )
        self.assertEqual(requested.status_code, 200, requested.content)
        decided = self._decide(self.checker, requested.json()["approval_id"])
        self.assertEqual(decided.json()["status"], ApprovalRequest.EXECUTED)
        self.assertEqual(Wallet.objects.get(user=self.customer).balance,
                         Decimal("4250.00"))
        evidence.refresh_from_db()
        self.assertEqual(evidence.state, ReversalEvidence.RESOLVED)

    def test_already_credited_row_can_be_retained_as_an_unrelated_deposit(self):
        from unittest.mock import patch

        from wallet.services import apply_wema_credit, settle_reserved_funding

        settle_reserved_funding(
            "WEMA-CR-ALAT-OLD-CREDIT", Decimal("300.00"), self.customer)
        row = {
            "referenceId": "ALAT-OLD-CREDIT",
            "amount": "300.00",
            "creditType": "Credit",
            "status": "Successfull",
            "narration": f"REFUND {self.payout.reference}",
        }
        with patch("utility.alerts.alert"):
            apply_wema_credit(
                Wallet.objects.get(user=self.customer), row, [self.payout.reference])
        before = Wallet.objects.get(user=self.customer).balance

        requested = self._request(
            self.maker,
            "retain_existing_as_deposit",
            evidence_reference="ALAT-OLD-CREDIT",
        )
        self.assertEqual(requested.status_code, 200, requested.content)
        decided = self._decide(self.checker, requested.json()["approval_id"])
        self.assertEqual(decided.json()["status"], ApprovalRequest.EXECUTED)
        self.assertEqual(Wallet.objects.get(user=self.customer).balance, before)
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.transaction_status, Transaction.SUCCESS)

    def test_provider_success_conflict_can_restore_the_original_debit_once(self):
        from unittest.mock import patch

        from wallet.services import apply_wema_credit, debit, settle_or_refund

        customer, _ = make_user(
            "08077770009", "provider-race@zitch.app", balance="5000")
        payout = debit(
            customer, Decimal("1000"), "Bank transfer",
            meta={"bank": "Wema", "recipient_account": "0123456789"},
            reference="ZTRFPROVIDERRACE1",
        )
        row = {
            "referenceId": "ALAT-PROVIDER-RACE",
            "amount": "1000.00",
            "creditType": "Credit",
            "status": "Successfull",
            "narration": f"REFUND {payout.reference}",
        }
        with patch("utility.alerts.alert"):
            apply_wema_credit(
                Wallet.objects.get(user=customer), row, [payout.reference])
        payout.refresh_from_db()
        self.assertEqual(settle_or_refund(
            payout, {"success": True, "status": "SUCCESSFUL"}), "quarantined")

        requested = self.client.post(
            "/api/admin/txn/reversal-resolution",
            data=json.dumps({
                "reference": payout.reference,
                "evidence_reference": "ALAT-PROVIDER-RACE",
                "disposition": "confirm_provider_success",
                "confirmed_amount": "1000.00",
                "reason": "Provider trace confirms beneficiary received funds",
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_admin_token(self.maker)}",
        )
        self.assertEqual(requested.status_code, 200, requested.content)
        decided = self._decide(self.checker, requested.json()["approval_id"])
        self.assertEqual(decided.json()["status"], ApprovalRequest.EXECUTED)
        payout.refresh_from_db()
        self.assertEqual(payout.transaction_status, Transaction.SUCCESS)
        self.assertEqual(Wallet.objects.get(user=customer).balance,
                         Decimal("4000.00"))
        resolution = ReversalEvidenceResolution.objects.get(
            approval_id=requested.json()["approval_id"])
        self.assertEqual(resolution.movement_direction, Transaction.OUT)
        self.assertIsNone(resolution.movement_transaction)

    def test_django_admin_action_drains_the_checked_approval_queue(self):
        from django.contrib.admin.sites import AdminSite

        from whatsapp.admin import ApprovalRequestAdmin

        requested = self._request(self.maker)
        approval_id = requested.json()["approval_id"]
        model_admin = ApprovalRequestAdmin(ApprovalRequest, AdminSite())
        notices = []
        model_admin.message_user = lambda request, message, level=None: notices.append(message)

        model_admin.approve_selected(
            SimpleNamespace(user=self.checker),
            ApprovalRequest.objects.filter(pk=approval_id),
        )

        approval = ApprovalRequest.objects.get(pk=approval_id)
        self.assertEqual(approval.status, ApprovalRequest.EXECUTED)
        self.assertEqual(approval.decided_by, self.checker)
        self.assertTrue(any("approved/executed" in message for message in notices))


class CardFundingResolutionTests(TestCase):
    def setUp(self):
        from cards.models import VirtualCard

        self.maker = _operator("card-maker")
        self.checker = _operator("card-checker")
        self.customer, _ = make_user(
            "08077770003", "card-resolution@zitch.app", balance="5000")
        self.card = VirtualCard.objects.create(
            user=self.customer, card_token="card_resolution_1",
            brand="Verve", last4="1234", expiry="01/30",
            holder="CARD CUSTOMER",
        )
        self.txn = debit(
            self.customer, Decimal("1000"), "Card funding",
            meta={"card": self.card.id, "card_funding": True,
                  "reconcile": True, "card_balance_applied": False},
            idempotency_key="card-resolution-1",
            reference="ZCARDFUNDRESOLVE1",
        )

    def _request(self, disposition):
        return self.client.post(
            "/api/admin/txn/card-funding-resolution",
            data=json.dumps({
                "reference": self.txn.reference,
                "disposition": disposition,
                "confirmed_amount": "1000",
                "evidence_reference": "ISSUER-TRACE-1001",
                "reason": "Issuer statement checked by finance",
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_admin_token(self.maker)}",
        )

    def _decide(self, approval_id):
        return self.client.post(
            "/api/admin/approvals/decide",
            data=json.dumps({"id": approval_id, "approve": True}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_admin_token(self.checker)}",
        )

    def test_confirm_loaded_applies_card_projection_once(self):
        requested = self._request("confirm_loaded")
        self.assertTrue(requested.json()["pending_approval"])
        self.assertEqual(Wallet.objects.get(user=self.customer).balance,
                         Decimal("4000"))
        self.assertEqual(self.card.balance, Decimal("0"))

        decided = self._decide(requested.json()["approval_id"])

        self.assertEqual(decided.json()["status"], ApprovalRequest.EXECUTED)
        self.txn.refresh_from_db()
        self.card.refresh_from_db()
        self.assertEqual(self.txn.transaction_status, Transaction.SUCCESS)
        self.assertTrue(self.txn.meta["card_balance_applied"])
        self.assertEqual(self.card.balance, Decimal("1000"))
        self.assertEqual(Wallet.objects.get(user=self.customer).balance,
                         Decimal("4000"))

    def test_confirm_failed_refunds_wallet_without_loading_card(self):
        requested = self._request("confirm_failed")
        decided = self._decide(requested.json()["approval_id"])

        self.assertEqual(decided.json()["status"], ApprovalRequest.EXECUTED)
        self.txn.refresh_from_db()
        self.card.refresh_from_db()
        self.assertEqual(self.txn.transaction_status, Transaction.FAILED)
        self.assertEqual(self.card.balance, Decimal("0"))
        self.assertEqual(Wallet.objects.get(user=self.customer).balance,
                         Decimal("5000"))

    def test_resolution_requires_explicit_issuer_evidence_and_exact_amount(self):
        base = {
            "reference": self.txn.reference,
            "disposition": "confirm_failed",
            "reason": "Issuer statement checked by finance",
        }
        missing_evidence = self.client.post(
            "/api/admin/txn/card-funding-resolution",
            data=json.dumps({**base, "confirmed_amount": "1000"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_admin_token(self.maker)}",
        )
        wrong_amount = self.client.post(
            "/api/admin/txn/card-funding-resolution",
            data=json.dumps({**base, "confirmed_amount": "999",
                             "evidence_reference": "ISSUER-TRACE-1001"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_admin_token(self.maker)}",
        )
        self.assertEqual(missing_evidence.status_code, 400)
        self.assertEqual(wrong_amount.status_code, 400)
        self.assertFalse(ApprovalRequest.objects.filter(
            action="wallet.card_funding_resolution").exists())

    def test_vas_requery_cannot_touch_a_pending_card_load(self):
        from unittest.mock import patch

        with patch("utility.providers.vtu_requery") as requery:
            response = self.client.post(
                "/api/admin/txn/requery",
                data=json.dumps({"ref": self.txn.reference}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {_admin_token(self.maker)}",
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "wrong_reconciliation_rail")
        requery.assert_not_called()
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.transaction_status, Transaction.PENDING)

    def test_legacy_projection_confirmation_never_increments_again(self):
        self.card.balance = Decimal("1000")
        self.card.save(update_fields=["balance"])
        self.txn.transaction_status = Transaction.SUCCESS
        self.txn.meta = {**self.txn.meta, "card_balance_review": True}
        self.txn.save(update_fields=["transaction_status", "meta"])

        requested = self._request("confirm_projection_applied")
        decided = self._decide(requested.json()["approval_id"])

        self.assertEqual(decided.json()["status"], ApprovalRequest.EXECUTED)
        self.card.refresh_from_db()
        self.txn.refresh_from_db()
        self.assertEqual(self.card.balance, Decimal("1000"))
        self.assertTrue(self.txn.meta["card_balance_applied"])
        self.assertNotIn("card_balance_review", self.txn.meta)


class FundingReviewResolutionTests(TestCase):
    def setUp(self):
        self.maker = _operator("funding-maker")
        self.checker = _operator("funding-checker")
        self.customer, _ = make_user(
            "08077770004", "funding-resolution@zitch.app",
        )
        self.intent = FundingIntent.objects.create(
            user=self.customer,
            reference="ZPAYFUNDINGREVIEW1",
            amount=Decimal("5000"),
            meta={
                "provider": "mono",
                "provider_reference": "mono-payment-1",
                "funding_review": {
                    "active": True,
                    "reason": "amount_mismatch",
                    "reasons": ["amount_mismatch"],
                    "expected_amount": "5000.00",
                    "observed_amount": "2500",
                    "currency": "NGN",
                    "event_count": 1,
                },
            },
        )

    def _request(self, disposition="confirm_paid", confirmed_amount="5000"):
        return self.client.post(
            "/api/admin/txn/funding-resolution",
            data=json.dumps({
                "reference": self.intent.reference,
                "disposition": disposition,
                "confirmed_amount": confirmed_amount,
                "evidence_reference": "MONO-CASE-1001",
                "reason": "Provider settlement statement reviewed",
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_admin_token(self.maker)}",
        )

    def _decide(self, approval_id, operator=None):
        return self.client.post(
            "/api/admin/approvals/decide",
            data=json.dumps({"id": approval_id, "approve": True}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_admin_token(operator or self.checker)}",
        )

    @override_settings(OPS_REQUIRE_DUAL_APPROVAL=False)
    def test_confirm_paid_always_waits_for_second_operator_then_credits_once(self):
        requested = self._request()

        self.assertEqual(requested.status_code, 200)
        self.assertTrue(requested.json()["pending_approval"])
        self.assertEqual(Wallet.objects.get(user=self.customer).balance, Decimal("0"))

        decided = self._decide(requested.json()["approval_id"])

        self.assertEqual(decided.json()["status"], ApprovalRequest.EXECUTED)
        self.intent.refresh_from_db()
        self.assertTrue(self.intent.credited)
        self.assertEqual(self.intent.amount, Decimal("5000"))
        self.assertFalse(self.intent.meta["funding_review"]["active"])
        resolution = self.intent.meta["funding_review"]["resolution"]
        self.assertEqual(resolution["disposition"], "confirm_paid")
        self.assertEqual(resolution["approval_id"], requested.json()["approval_id"])
        self.assertEqual(Wallet.objects.get(user=self.customer).balance, Decimal("5000"))
        self.assertEqual(Transaction.objects.filter(
            reference=self.intent.reference, direction=Transaction.IN,
        ).count(), 1)

    def test_mark_failed_closes_review_without_creating_money(self):
        requested = self._request("mark_failed", confirmed_amount="")
        decided = self._decide(requested.json()["approval_id"])

        self.assertEqual(decided.json()["status"], ApprovalRequest.EXECUTED)
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, FundingIntent.FAILED)
        self.assertFalse(self.intent.credited)
        self.assertFalse(self.intent.meta["funding_review"]["active"])
        self.assertEqual(Wallet.objects.get(user=self.customer).balance, Decimal("0"))

    def test_late_success_after_mark_failed_reopens_review_without_credit(self):
        requested = self._request("mark_failed", confirmed_amount="")
        decided = self._decide(requested.json()["approval_id"])
        self.assertEqual(decided.json()["status"], ApprovalRequest.EXECUTED)

        from wallet.services import settle_funding

        result = settle_funding(
            self.intent.reference, Decimal("5000"), verified_currency="NGN",
            evidence={"source": "late_provider_callback"},
        )

        self.assertIsNone(result)
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, FundingIntent.FAILED)
        self.assertFalse(self.intent.credited)
        self.assertTrue(self.intent.meta["funding_review"]["active"])
        self.assertEqual(
            self.intent.meta["funding_review"]["reason"], "success_after_failed",
        )
        self.assertEqual(Wallet.objects.get(user=self.customer).balance, Decimal("0"))

    def test_confirm_paid_rejects_non_exact_amount_before_approval(self):
        response = self._request(confirmed_amount="2500")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(ApprovalRequest.objects.filter(
            action="wallet.funding_review_resolution",
        ).count(), 0)
        self.assertEqual(Wallet.objects.get(user=self.customer).balance, Decimal("0"))

    def test_stuck_pending_intent_can_be_placed_on_durable_operator_hold(self):
        self.intent.meta = {
            "provider": "mono",
            "provider_reference": "mono-payment-1",
            "directpay_state": "pending",
        }
        self.intent.save(update_fields=["meta", "updated"])

        requested = self._request()

        self.assertEqual(requested.status_code, 200)
        self.assertTrue(requested.json()["pending_approval"])
        self.intent.refresh_from_db()
        self.assertTrue(self.intent.meta["funding_review"]["active"])
        self.assertEqual(
            self.intent.meta["funding_review"]["reason"],
            "operator_resolution_requested",
        )
        self.assertEqual(Wallet.objects.get(user=self.customer).balance, Decimal("0"))

        decided = self._decide(requested.json()["approval_id"])
        self.assertEqual(decided.json()["status"], ApprovalRequest.EXECUTED)
        self.assertEqual(Wallet.objects.get(user=self.customer).balance, Decimal("5000"))

    def test_approval_fails_closed_if_webhook_evidence_changes(self):
        requested = self._request()
        marker = dict(self.intent.meta["funding_review"])
        marker["event_count"] = 2
        self.intent.meta = {**self.intent.meta, "funding_review": marker}
        self.intent.save(update_fields=["meta", "updated"])

        decided = self._decide(requested.json()["approval_id"])

        self.assertEqual(decided.json()["status"], ApprovalRequest.FAILED)
        self.assertIn("evidence changed", decided.json()["result"]["error"])
        self.intent.refresh_from_db()
        self.assertFalse(self.intent.credited)
        self.assertEqual(Wallet.objects.get(user=self.customer).balance, Decimal("0"))
