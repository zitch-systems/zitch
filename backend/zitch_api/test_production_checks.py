from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from zitch_api.production_checks import validate_production_configuration


class ProductionConfigurationTests(SimpleTestCase):
    def validate(self, **overrides):
        values = {
            "is_production": True,
            "test_otp_phone": "",
            "test_otp_code": "",
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
