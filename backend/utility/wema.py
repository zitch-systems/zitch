"""Retired provider compatibility for historical evidence only.

The complete former integration is preserved on the Git archive branch. No code
in this module can contact that bank, issue an account, lift PND or move funds.
Remove the compatibility surface after historical operator callers migrate.
"""

def _unavailable(*args, **kwargs):
    return {"success": False, "pending": True, "status": "REVIEW", "retired": True,
            "code": "provider_archived", "message": "This historical provider is archived; manual review is required."}

def _disabled(*args, **kwargs):
    return False

def _none(*args, **kwargs):
    return None

def holder_name_mismatch(supplied, resolved):
    from banking.accounts import same_name
    return not same_name(supplied, resolved)

def classify_transfer_status(status, *, envelope_ok=True):
    # Retired-provider statuses cannot authorize new settlement.
    return "pending"

def normalize_transaction(value):
    from wallet.historical_evidence import normalize_transaction as decode
    return decode(value)

def _naira(value):
    from wallet.historical_evidence import _naira as decode
    return decode(value)

def _first_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _first_text(next(iter(value.values()), ""))
    if isinstance(value, (list, tuple)):
        return _first_text(value[0]) if value else ""
    return ""


def face_verification_url(*args, **kwargs):
    return ""

def _vas_legend(*args, **kwargs):
    return {}

_vas_live = _disabled
bank_tier_limit = _none
wema_live = _disabled
wema_keys_configured = _disabled
wema_simulation = _disabled
security_info_value = _unavailable
security_info_for_reference = _unavailable
create_wallet_request = _unavailable
validate_wallet_otp = _unavailable
resend_wallet_otp = _unavailable
address_verify_live = _disabled
face_verify_live = _disabled
face_verify_on_nonprod_host = _disabled
face_cb_mode = _unavailable
create_wallet_with_face = _unavailable
get_account_details = _unavailable
lift_debit_restriction = _unavailable
get_kyc_status = _unavailable
upgrade_tier2 = _unavailable
upgrade_tier3 = _unavailable
get_balance = _unavailable
get_transactions = _unavailable
get_banks = _unavailable
get_nip_charges = _unavailable
nip_fee_for = _none
resolve_account = _unavailable
transfer = _unavailable
confirm_transfer_status = _unavailable
credit_wallet = _unavailable
confirm_credit_status = _unavailable
purchase_airtime = _unavailable
get_data_plans = _unavailable
purchase_data = _unavailable
get_bills = _unavailable
validate_bill_customer = _unavailable
pay_bill = _unavailable
vas_status = _unavailable
vas_status_entitlement = _unavailable
validate_rrr = _unavailable
pay_remita = _unavailable
remita_receipt = _unavailable
bnpl_offers = _unavailable
bnpl_consent = _unavailable
bnpl_accept_terms = _unavailable
bnpl_status = _unavailable
bnpl_liquidate = _unavailable
card_opted_in = _disabled
card_issue = _unavailable
card_set_status = _unavailable
card_fund = _unavailable
card_reveal = _unavailable
verify_bvn = _unavailable
verify_nin = _unavailable
verify_vnin = _unavailable
wema_probe = _unavailable
wema_diagnostics = _unavailable
