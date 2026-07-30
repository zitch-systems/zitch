"""Operator second factor, and maker/checker on the one action that creates money.

Both were deferred in docs/hardening/GAP_ANALYSIS.md as things that matter once there
is a real ops team. That reasoning holds for most of the portal and fails for a manual
wallet credit: it mints a balance from nothing, and every control downstream — tier
caps, velocity, the ledger — treats that balance as legitimate, because it is.
"""
import json
from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken, OperatorTotp, User
from accounts.totp import DIGITS, code_for, current_step, new_secret, verify
from wallet.models import Wallet
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
        # RFC 6238 test vector: secret "12345678901234567890" (ASCII), SHA-1, T=59
        # => 94287082. Base32 of that ASCII string is GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ.
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
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
        self.assertFalse(OperatorTotp.objects.get(user=self.op).confirmed)

        res = self._post("mfa/confirm", {"code": code_for(secret, current_step())})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(OperatorTotp.objects.get(user=self.op).confirmed)

    def test_confirm_refuses_a_wrong_code(self):
        self._post("mfa/enroll")
        res = self._post("mfa/confirm", {"code": "000000"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["code"], "mfa_invalid")
        self.assertFalse(OperatorTotp.objects.get(user=self.op).confirmed)

    def test_re_enrolling_a_confirmed_factor_needs_a_current_code(self):
        # Otherwise a hijacked operator session could swap the factor for one the
        # attacker controls, making it decorative.
        res = self._post("mfa/enroll")
        secret = res.json()["secret"]
        self._post("mfa/confirm", {"code": code_for(secret, current_step())})

        res = self._post("mfa/enroll")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["code"], "mfa_code_required")
        self.assertEqual(OperatorTotp.objects.get(user=self.op).secret, secret)

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
