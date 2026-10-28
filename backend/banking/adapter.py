"""Application-facing 9PSB reads and payment dispatch.

Adapters return normalized application results, never bank credentials or identity
documents. Unsupported former-provider operations stay unavailable.
"""
from decimal import Decimal

from django.core.cache import cache

from .accounts import account_for, same_name, update_account
from .client import Client, ProviderError, config, configured, customer_enabled, money, bank_directory
from .models import BankAccount, Instruction
from .payments import requery, submit_existing


def unavailable(*args, **kwargs):
    return {"success": False, "not_charged": True, "message": "This service is not available with the current bank connection."}


def bank_live():
    return configured()


def bank_simulation():
    return False


def get_banks():
    try:
        client = Client()
        key = "9psb:banks:v2:" + client._cache_key()
        data = cache.get(key)
        if data is not None:
            return {"success": True, "banks": data}
        data = bank_directory(client.banks())
        cache.set(key, data, timeout=86400)
        return {"success": True, "banks": data}
    except ProviderError:
        return unavailable()


def resolve_account(account_number, bank_code):
    try:
        result = Client().resolve(account_number, bank_code)
        # Sandbox confirms this endpoint is a top-level NIP envelope, not the
        # usual WaaS status/data wrapper. Bind both identifiers before using name.
        data = result.body.get("customer", {}).get("account", {})
        name = data.get("name")
        if (result.http_status == 200 and str(result.body.get("code")) == "00"
                and str(data.get("number")) == account_number
                and str(data.get("bank")) == str(bank_code)
                and isinstance(name, str) and 0 < len(name.strip()) <= 160):
            return {"success": True, "name": name.strip(), "account_number": account_number,
                    "bank_code": str(bank_code)}
    except (ProviderError, ValueError, AttributeError):
        pass
    return {"success": False, "message": "We could not confirm this recipient. Check the account and bank."}


def get_balance(account_number):
    bank = BankAccount.objects.filter(number=account_number, environment=config().get("ENVIRONMENT", "sandbox")).first()
    if not bank:
        return unavailable()
    try:
        update_account(bank)
        if bank.available_balance is None:
            return unavailable()
        return {"success": True, "balance": str(bank.available_balance),
                "available_balance": str(bank.available_balance), "ledger_balance": str(bank.ledger_balance)}
    except ProviderError:
        return unavailable()


def get_kyc_status(account_number):
    bank = BankAccount.objects.filter(number=account_number, environment=config().get("ENVIRONMENT", "sandbox")).first()
    if not bank:
        return unavailable()
    try:
        update_account(bank)
        return {"success": True, "tier": bank.tier, "name": bank.name, "pnd": bank.pnd,
                "maximum_deposit": bank.maximum_deposit, "maximum_balance": bank.maximum_balance}
    except ProviderError:
        return unavailable()


def transfer(amount_naira, reference, narration, *, source_account, destination_account,
             destination_bank_code, destination_bank_name="", destination_name=""):
    return submit_existing(reference, "waas", "OTHER_BANKS", {"amount": money(amount_naira),
        "source": source_account, "destination": destination_account, "bank_code": destination_bank_code,
        "destination_name": destination_name, "narration": narration})


def confirm_transfer_status(reference, **kwargs):
    instruction = Instruction.objects.filter(transaction__reference=reference).first()
    if not instruction:
        return {"success": False, "pending": True, "status": "REVIEW", "message": "This historical payment requires review."}
    return requery(instruction)


def vas_status(reference, txn_type=""):
    return confirm_transfer_status(reference)


def holder_name_mismatch(supplied, resolved):
    return not same_name(supplied, resolved)


def classify_transfer_status(status, *, envelope_ok=True):
    if not envelope_ok:
        return "pending"
    value = str(status).upper()
    return "success" if value == "SUCCESS" else "failed" if value == "FAILED" else "pending"


def bank_tier_limit(tier, key):
    # Confirmed correspondence only. Deposit caps do not become spending caps.
    values = {1: {"single_inflow": Decimal("50000"), "max_balance": Decimal("300000")},
              2: {"max_balance": Decimal("500000")}, 3: {}}
    return values.get(tier, {}).get(key)


def lift_debit_restriction(account_number, **kwargs):
    # PND is a bank decision, particularly after an excess inflow. Never invoke
    # change_wallet_status to evade it. Only read back whether it is absent.
    result = get_kyc_status(account_number)
    return {"success": result.get("success") is True and result.get("pnd") is False}


def face_verify_live():
    return customer_enabled()


def address_verify_live():
    return False


def bank_diagnostics():
    return {"provider": "9psb", "environment": config().get("ENVIRONMENT", "sandbox"),
        "configured": configured(), "enabled": customer_enabled(),
        "vas_configured": configured("vas"), "vas_enabled": bool(config().get("VAS_ENABLED"))}
