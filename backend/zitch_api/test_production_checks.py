from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from zitch_api.production_checks import validate_production_configuration


class ProductionConfigurationTests(SimpleTestCase):
    def validate(self, **overrides):
        values = {
            "is_production": True,
            "test_otp_phone": "",
            "test_otp_code": "",
            "allow_test_otp": False,
            "simulate_deposit_token": "",
            "wema_simulation": False,
            "mono_simulation": False,
            "allow_simulation": False,
            "redis_url": "redis://cache:6379/0",
            "require_shared_cache": True,
        }
        values.update(overrides)
        return validate_production_configuration(**values)

    def test_safe_production_configuration_passes(self):
        self.validate()

    def test_non_production_configuration_is_not_blocked(self):
        self.validate(
            is_production=False,
            test_otp_phone="08030000000",
            simulate_deposit_token="test-token",
            wema_simulation=True,
            redis_url="",
        )

    def test_partial_or_complete_test_otp_is_rejected(self):
        for values in (
            {"test_otp_phone": "08030000000"},
            {"test_otp_code": "123456"},
            {"test_otp_phone": "08030000000", "test_otp_code": "123456"},
        ):
            with self.subTest(values=values), self.assertRaisesRegex(
                ImproperlyConfigured, "TEST_OTP_PHONE"
            ):
                self.validate(**values)

    def test_test_otp_requires_explicit_pre_launch_override(self):
        """The override names itself in the error, so the fix is discoverable from
        the crash alone — a boot failure is the worst place to have to go reading
        source to find the escape hatch."""
        with self.assertRaisesRegex(ImproperlyConfigured, "ALLOW_PRODUCTION_TEST_OTP"):
            self.validate(test_otp_phone="08030000000", test_otp_code="123456")
        self.validate(
            test_otp_phone="08030000000", test_otp_code="123456", allow_test_otp=True
        )

    def test_test_otp_override_does_not_unlock_the_other_gates(self):
        """Scoped strictly to the OTP bypass: an operator turning it on for
        pre-launch testing must not silently also permit simulated money or a
        single-process cache."""
        with self.assertRaisesRegex(ImproperlyConfigured, "SIMULATE_DEPOSIT_TOKEN"):
            self.validate(
                test_otp_phone="08030000000",
                test_otp_code="123456",
                allow_test_otp=True,
                simulate_deposit_token="test-token",
            )
        with self.assertRaisesRegex(ImproperlyConfigured, "ALLOW_PRODUCTION_SIMULATION"):
            self.validate(
                test_otp_phone="08030000000",
                test_otp_code="123456",
                allow_test_otp=True,
                wema_simulation=True,
            )

    def test_simulated_deposit_token_is_rejected(self):
        with self.assertRaisesRegex(ImproperlyConfigured, "SIMULATE_DEPOSIT_TOKEN"):
            self.validate(simulate_deposit_token="test-token")

    def test_provider_simulation_requires_explicit_isolated_deploy_override(self):
        with self.assertRaisesRegex(ImproperlyConfigured, "ALLOW_PRODUCTION_SIMULATION"):
            self.validate(wema_simulation=True)
        self.validate(wema_simulation=True, allow_simulation=True)

    def test_shared_cache_is_required_by_default(self):
        with self.assertRaisesRegex(ImproperlyConfigured, "REDIS_URL"):
            self.validate(redis_url="")
        self.validate(redis_url="", require_shared_cache=False)


class LoggingConfigurationTests(SimpleTestCase):
    """Every logger the code writes to must be able to emit at INFO.

    A name absent from LOGGING["loggers"] inherits root, which sits at WARNING —
    so its log.info calls are dropped before any handler sees them, silently and
    with nothing in the config pointing at the omission. "wallet" was missing for
    the whole life of the Wema integration, which is why the NUBAN provisioning
    trail (wema_account_provisioned, and the source= that says whether the account
    came from the OTP flow or the bank's callback) never once reached Render.
    """

    def logger_names_used_in_code(self):
        import re
        from pathlib import Path
        base = Path(__file__).resolve().parent.parent
        names = set()
        for path in base.rglob("*.py"):
            if "/migrations/" in str(path) or path.name.startswith("test"):
                continue
            for match in re.finditer(r'getLogger\(["\']([\w.]+)["\']\)', path.read_text(encoding="utf-8")):
                names.add(match.group(1))
        return names

    def test_every_logger_used_in_code_can_emit_at_info(self):
        import logging
        missing = []
        for name in sorted(self.logger_names_used_in_code()):
            if not logging.getLogger(name).isEnabledFor(logging.INFO):
                missing.append(name)
        self.assertEqual(
            missing, [],
            f"these loggers drop log.info because LOGGING['loggers'] has no entry "
            f"for them (or an ancestor): {missing}")

    def test_the_scan_actually_finds_the_known_loggers(self):
        """Guards the test above from passing because the scan found nothing."""
        found = self.logger_names_used_in_code()
        self.assertIn("wallet", found)
        self.assertIn("zitch.security", found)   # dotted: inherits from "zitch"
        self.assertGreaterEqual(len(found), 4)
