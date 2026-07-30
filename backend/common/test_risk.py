"""Device-aware risk scoring, and the one money gate it adds.

The gate: a spend at or above RISK_NEW_DEVICE_FACE_THRESHOLD from a device this
account has never authenticated from requires face verification. It exists because a
stolen password arrives on an unfamiliar handset and drains what the tier allows — and
the tier cap, by construction, permits exactly that.

Everything here also pins the boundaries. The signals are attacker-controlled, so a
clean signal must earn no trust; a missing signal must not read as safe; and none of
it may break a request.
"""
from decimal import Decimal
from unittest import mock

from django.test import RequestFactory, TestCase, override_settings

from accounts.models import KnownDevice
from common.http import send_limit_error
from common.risk import (HIGH_RISK, SCORE_NO_DEVICE_HEADER, attach_device, evaluate_login,
                         parse_device, remember_device, score_device)
from wallet.tests import make_user

INFO_CLEAN = "os=android;osv=14;model=Pixel 7;brand=google;physical=1;rooted=0"
INFO_ROOTED = "os=android;osv=14;model=Pixel 7;brand=google;physical=1;rooted=1"
INFO_EMULATOR = "os=android;osv=14;model=sdk_gphone64;brand=google;physical=0;rooted=0"
INFO_NO_ROOT_ANSWER = "os=ios;osv=17;model=iPhone 15;brand=Apple;physical=1"


def _request(device_id="dev-aaa", info=INFO_CLEAN, ip="102.89.1.1"):
    kwargs = {"REMOTE_ADDR": ip}
    if device_id is not None:
        kwargs["HTTP_X_ZITCH_DEVICE"] = device_id
    if info is not None:
        kwargs["HTTP_X_ZITCH_DEVICE_INFO"] = info
    return RequestFactory().post("/api/x/", **kwargs)


class ParseDeviceTests(TestCase):
    def test_parses_the_descriptor(self):
        d = parse_device(_request())
        self.assertEqual(d["id"], "dev-aaa")
        self.assertEqual(d["model"], "Pixel 7")
        self.assertEqual(d["os"], "android")
        self.assertFalse(d["emulator"])
        self.assertIs(d["rooted"], False)

    def test_absent_headers_do_not_crash_or_imply_safety(self):
        d = parse_device(_request(device_id=None, info=None))
        self.assertEqual(d["id"], "")
        # None, not False: "the client didn't say" is not "the device is clean".
        self.assertIsNone(d["rooted"])

    def test_missing_root_answer_is_unknown_not_clean(self):
        self.assertIsNone(parse_device(_request(info=INFO_NO_ROOT_ANSWER))["rooted"])

    def test_emulator_is_read_from_physical(self):
        self.assertTrue(parse_device(_request(info=INFO_EMULATOR))["emulator"])

    def test_oversized_values_are_bounded(self):
        d = parse_device(_request(device_id="x" * 500, info="model=" + "y" * 500))
        self.assertLessEqual(len(d["id"]), 64)
        self.assertLessEqual(len(d["model"]), 48)


class ScoreDeviceTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08066660001", "risk@zitch.app")

    def _score(self, **kw):
        return score_device(self.user, attach_device(_request(**kw), self.user))

    def test_unknown_device_scores(self):
        score, reasons = self._score()
        self.assertIn("unknown_device", reasons)
        self.assertGreater(score, 0)

    def test_known_device_on_the_same_ip_is_quiet(self):
        remember_device(self.user, attach_device(_request(), self.user))
        score, reasons = self._score()
        self.assertEqual(reasons, [])
        self.assertEqual(score, 0)

    def test_a_known_device_on_a_new_ip_scores_only_mildly(self):
        remember_device(self.user, attach_device(_request(ip="102.89.1.1"), self.user))
        score, reasons = self._score(ip="41.58.2.2")
        self.assertEqual(reasons, ["new_ip"])
        # Mobile IPs change constantly; on its own this must not reach an alert.
        self.assertLess(score, HIGH_RISK)

    def test_rooted_and_unknown_together_reach_high_risk(self):
        score, reasons = self._score(info=INFO_ROOTED)
        self.assertIn("rooted", reasons)
        self.assertGreaterEqual(score, HIGH_RISK)

    def test_emulator_scores(self):
        _score, reasons = self._score(info=INFO_EMULATOR)
        self.assertIn("emulator", reasons)

    def test_no_header_is_scored_as_unknown_not_zero(self):
        # Stripping a header must not be the cheapest way to look trustworthy.
        score, reasons = self._score(device_id=None, info=None)
        self.assertEqual(reasons, ["no_device_header"])
        self.assertEqual(score, SCORE_NO_DEVICE_HEADER)


class RememberDeviceTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08066660002", "risk2@zitch.app")

    def test_first_sight_creates_one_row_and_repeats_do_not(self):
        for _ in range(3):
            remember_device(self.user, attach_device(_request(), self.user))
        self.assertEqual(KnownDevice.objects.filter(user=self.user).count(), 1)

    def test_a_changed_ip_is_recorded(self):
        remember_device(self.user, attach_device(_request(ip="102.89.1.1"), self.user))
        remember_device(self.user, attach_device(_request(ip="41.58.2.2"), self.user))
        self.assertEqual(KnownDevice.objects.get(user=self.user).last_ip, "41.58.2.2")

    def test_no_device_header_writes_nothing(self):
        remember_device(self.user, attach_device(_request(device_id=None), self.user))
        self.assertEqual(KnownDevice.objects.count(), 0)

    def test_a_database_failure_does_not_propagate(self):
        # Risk telemetry must never be the reason a money request fails.
        with mock.patch("accounts.models.KnownDevice.objects.get_or_create",
                        side_effect=RuntimeError("db down")):
            remember_device(self.user, attach_device(_request(), self.user))  # no raise


class EvaluateLoginTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08066660003", "risk3@zitch.app")

    def test_high_risk_signin_alerts_and_audits(self):
        with mock.patch("utility.alerts.alert") as alert_mock:
            score, reasons = evaluate_login(_request(info=INFO_ROOTED), self.user)
        self.assertGreaterEqual(score, HIGH_RISK)
        alert_mock.assert_called_once()
        from whatsapp.models import AuditLog
        row = AuditLog.objects.filter(action="auth.high_risk_signin").first()
        self.assertIsNotNone(row)
        self.assertIn("rooted", row.after["reasons"])

    def test_an_ordinary_signin_does_not_alert(self):
        remember_device(self.user, attach_device(_request(), self.user))
        with mock.patch("utility.alerts.alert") as alert_mock:
            evaluate_login(_request(), self.user)
        alert_mock.assert_not_called()

    def test_the_device_is_remembered_so_the_next_login_is_quiet(self):
        evaluate_login(_request(), self.user)
        self.assertTrue(KnownDevice.objects.filter(user=self.user).exists())


@override_settings(RISK_NEW_DEVICE_FACE_THRESHOLD=Decimal("50000"))
class NewDeviceStepUpTests(TestCase):
    """The gate is expressed through send_limit_error, so it applies on every send
    path — the app, and anything else that funnels through the shared limit check."""

    def setUp(self):
        # Tier 3 so the tier cap (₦5,000,000) is not what refuses these amounts.
        self.user, _ = make_user("08066660004", "risk4@zitch.app", balance="500000", tier=3)

    def _attach(self, **kw):
        attach_device(_request(**kw), self.user)

    def test_large_spend_from_an_unknown_device_needs_face(self):
        self._attach()
        msg = send_limit_error(self.user, Decimal("60000"))
        self.assertIn("verify your face", msg)

    def test_everyday_spend_from_a_new_phone_is_untouched(self):
        # A selfie for a ₦2,000 airtime top-up on a new handset would be absurd, and a
        # control people resent gets switched off.
        self._attach()
        self.assertIsNone(send_limit_error(self.user, Decimal("2000")))

    def test_known_device_is_not_stepped_up(self):
        self._attach()
        remember_device(self.user, attach_device(_request(), self.user))
        self.assertIsNone(send_limit_error(self.user, Decimal("60000")))

    def test_face_verified_user_is_not_asked_again(self):
        self.user.face_verified = True
        self.user.save(update_fields=["face_verified"])
        self._attach()
        self.assertIsNone(send_limit_error(self.user, Decimal("60000")))

    def test_no_device_header_is_a_deliberate_hole_not_a_gate(self):
        # Older builds and the WhatsApp channel have no device to report. Gating on a
        # missing header would break them; requiring it is a client-rollout decision.
        self._attach(device_id=None, info=None)
        self.assertIsNone(send_limit_error(self.user, Decimal("60000")))

    def test_a_non_http_caller_has_no_device_and_is_unaffected(self):
        # The WhatsApp router and the crons call the same helper with no request.
        self.assertIsNone(send_limit_error(self.user, Decimal("60000")))

    @override_settings(RISK_NEW_DEVICE_FACE_THRESHOLD=Decimal("0"))
    def test_threshold_zero_disables_the_gate(self):
        self._attach()
        self.assertIsNone(send_limit_error(self.user, Decimal("60000")))

    def test_the_existing_tier_and_face_rules_still_win_first(self):
        # ₦150,000 is over LARGE_TXN_THRESHOLD, so the pre-existing face gate should
        # answer — the new rule must not change which message a user sees there.
        self._attach()
        self.assertEqual(send_limit_error(self.user, Decimal("150000")),
                         "Face verification required for this amount.")
