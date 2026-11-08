"""Redacted configuration/readiness evidence; no external side effects."""
from .client import Client, ProviderError, config, configured, phone, sandbox
from .fees import transfer_fee

# First failing gate -> what the operator or tester does next. Order mirrors
# accounts.require_enabled() and accounts.start_identity().
TESTER_STEPS = {
    "invalid_environment": "Set NINEPSB_ENVIRONMENT to sandbox or production.",
    "waas_not_configured": "Set NINEPSB_BASE_URL and the four NINEPSB_WAAS_* credentials on the API and worker.",
    "banking_disabled": "Set NINEPSB_ENABLED=true.",
    "sandbox_customers_disabled": "Set NINEPSB_SANDBOX_CUSTOMERS_ENABLED=true for sandbox testers.",
    "production_uat_not_approved": "Production customers stay closed until bank UAT/ICAD approval.",
    "account_inactive": "Reactivate this Zitch account before testing.",
    "not_enrolled": "Add this user's ID to NINEPSB_SANDBOX_USER_IDS or their phone to NINEPSB_SANDBOX_PHONES.",
    "enrolled_phone_not_verified": "Phone enrollment needs the verified phone: finish phone OTP in the app.",
    "phone_not_verified": "Finish phone OTP verification in the app.",
    "email_not_verified": "Confirm the email code on the app's Verify your email screen.",
    "legal_name_incomplete": "The Zitch profile needs a first and last name matching the bank test identity.",
    "bank_account_in_other_environment": "This user's bank account belongs to the other bank environment; use a new tester.",
}


def find_tester(*, user_id=None, mobile=None):
    from accounts.models import User
    if user_id is not None:
        return User.objects.filter(pk=user_id).first()
    local = phone(mobile)
    return User.objects.filter(phone__in=[local, "+234" + local[1:], "234" + local[1:]]).first()


def tester_report(user):
    """Explain which onboarding gate blocks one tester, without bank I/O or PII."""
    from .accounts import account_for, same_name, sandbox_tester
    from .models import BankAccount, IdentityChallenge
    conf = config()
    environment = conf.get("ENVIRONMENT", "sandbox")
    blockers = []
    if environment not in {"sandbox", "production"}:
        blockers.append("invalid_environment")
    if not configured():
        blockers.append("waas_not_configured")
    if conf.get("ENABLED") is not True:
        blockers.append("banking_disabled")
    if environment == "sandbox" and conf.get("SANDBOX_CUSTOMERS_ENABLED") is not True:
        blockers.append("sandbox_customers_disabled")
    if environment == "production" and conf.get("UAT_APPROVED") is not True:
        blockers.append("production_uat_not_approved")
    if not user.is_active:
        blockers.append("account_inactive")
    enrolled_by = None
    if str(user.pk) in conf.get("SANDBOX_USER_IDS", []):
        enrolled_by = "user_id"
    elif sandbox_tester(user):
        enrolled_by = "phone"
    if environment == "sandbox" and not enrolled_by:
        listed = False
        try:
            listed = phone(user.phone) in {phone(x) for x in conf.get("SANDBOX_PHONES", []) if _valid_phone(x)}
        except ValueError:
            pass
        blockers.append("enrolled_phone_not_verified" if listed and not user.phone_verified else "not_enrolled")
    if not user.phone_verified and "enrolled_phone_not_verified" not in blockers:
        blockers.append("phone_not_verified")
    if not user.email_verified:
        blockers.append("email_not_verified")
    full_name = user.get_full_name()
    if not same_name(full_name, full_name):
        blockers.append("legal_name_incomplete")
    other = BankAccount.objects.filter(wallet__user_id=user.pk).exclude(environment=environment).exists()
    if other:
        blockers.append("bank_account_in_other_environment")
    bank = account_for(user)
    challenge = (IdentityChallenge.objects.filter(user=user, environment=environment)
                 .order_by("-created").first())
    nuban_ready = bool(bank and bank.status == "active" and not blockers)
    if blockers:
        next_step = TESTER_STEPS[blockers[0]]
    elif nuban_ready:
        next_step = "NUBAN attached; it is shown on the app's Add Money screen."
    elif bank:
        next_step = "A bank account is attached but not active; run reconcile_9psb and check its status."
    else:
        next_step = ("Open Verify your account in the app, choose BVN or NIN, enter the bank's sandbox "
                     "test identity and confirm the bank OTP.")
    return {"environment": environment, "user_id": user.pk,
        "phone_last4": (user.phone or "")[-4:], "sandbox_enrolled_by": enrolled_by,
        "test_otp_phone": _is_test_otp_phone(user.phone),
        "ready_for_bank_identity": not blockers, "blockers": blockers, "next_step": next_step,
        "bank_account": ({"status": bank.status, "tier": bank.tier, "nuban_last4": bank.number[-4:],
                          "nuban_ready": nuban_ready} if bank else None),
        "latest_identity_challenge": ({"kind": challenge.kind, "method": challenge.method,
                                       "state": challenge.state} if challenge else None)}


def _valid_phone(value):
    try:
        phone(value)
        return True
    except ValueError:
        return False


def _is_test_otp_phone(value):
    from django.conf import settings
    test = getattr(settings, "TEST_OTP", {}) or {}
    if not test.get("PHONE") or not test.get("CODE") or not _valid_phone(value) or not _valid_phone(test["PHONE"]):
        return False
    return phone(value) == phone(test["PHONE"])


def endpoint_report(product):
    """There is no provider, so there is no wire destination to describe.

    This used to resolve the client's base URL, authentication URL and the
    names of its credential fields. The 9PSB transport is gone and so are
    those internals, so the probe must not reach for them: /healthz calls
    this through `report`, and an AttributeError here is a 500 on the very
    endpoint Render health-checks.

    The shape is kept so every caller still reads the same keys, and the
    absence is stated as an error rather than reported as healthy.
    """
    return {"api_base_url": None, "authentication_url": None,
            "credentials_present": False, "authentication_body_fields": [],
            "errors": [product + "_provider_removed"], "configured": False}


def test_shortcut_blockers():
    """Pre-launch shortcuts that must be unset before real customers use the bank."""
    from django.conf import settings
    blockers = []
    if settings.TEST_OTP["PHONE"] and settings.TEST_OTP["CODE"]:
        blockers.append("test_otp_bypass_enabled")
    if (getattr(settings, "MONO", {}) or {}).get("SIMULATION"):
        blockers.append("simulation_mode_enabled")
    return blockers


def report():
    conf = config()
    gaps = []
    endpoints = {product: endpoint_report(product) for product in ("waas", "vas")}
    if conf.get("ENVIRONMENT") not in {"sandbox", "production"}:
        gaps.append("invalid_environment")
    for details in endpoints.values():
        gaps.extend(error for error in details["errors"] if error not in gaps)
    if not conf.get("WEBHOOK_USERNAME") or len(conf.get("WEBHOOK_PASSWORD", "")) < 24:
        gaps.append("webhook_credentials_missing_or_weak")
    if transfer_fee(1) is None:
        gaps.append("all_inclusive_transfer_fee_unconfirmed")
    if sandbox():
        gaps.append("sandbox_only")
    elif not (endpoints["waas"]["api_base_url"] or "").startswith("https://"):
        gaps.append("production_https_required")
    if not conf.get("UAT_APPROVED"):
        gaps.append("bank_uat_and_icad_approval_required")
    gaps.extend(test_shortcut_blockers())
    # These contracts are absent from the supplied materials. A configuration
    # boolean cannot substitute for an implemented and tested contract.
    gaps.extend(["history_and_reversal_contract_unconfirmed", "address_terminal_report_contract_unconfirmed",
                 "virtual_account_and_acquiring_contracts_missing"])
    return {"provider": "9psb", "environment": conf.get("ENVIRONMENT", "sandbox"),
        "endpoints": endpoints,
        "waas_configured": configured(), "vas_configured": configured("vas"),
        "enabled": conf.get("ENABLED") is True, "vas_enabled": conf.get("VAS_ENABLED") is True,
        "production_ready": not gaps, "blockers": gaps}
