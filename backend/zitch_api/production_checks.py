"""Fail-fast validation for settings that must never reach a live deployment."""
from django.core.exceptions import ImproperlyConfigured


def validate_production_configuration(
    *,
    is_production: bool,
    test_otp_phone: str,
    test_otp_code: str,
    simulate_deposit_token: str,
    wema_simulation: bool,
    mono_simulation: bool,
    allow_simulation: bool,
    redis_url: str,
    require_shared_cache: bool,
) -> None:
    """Reject unsafe live settings before the application serves traffic."""
    if not is_production:
        return

    problems: list[str] = []
    if test_otp_phone or test_otp_code:
        problems.append("TEST_OTP_PHONE and TEST_OTP_CODE must both be unset")
    if simulate_deposit_token:
        problems.append("SIMULATE_DEPOSIT_TOKEN must be unset")
    if (wema_simulation or mono_simulation) and not allow_simulation:
        problems.append(
            "WEMA_SIMULATION and MONO_SIMULATION require "
            "ALLOW_PRODUCTION_SIMULATION=true on an isolated test deployment"
        )
    if require_shared_cache and not redis_url:
        problems.append("REDIS_URL is required for shared production rate limits")

    if problems:
        raise ImproperlyConfigured("Unsafe production configuration: " + "; ".join(problems))
