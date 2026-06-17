"""Third-party integration layer.

Providers: Monnify (payments; also optional KYC/VAS + bills via monnify_*),
VTU.ng (VTU — airtime, data, cable, electricity, betting; via vtu_purchase /
vtu_requery / vtu_verify_customer), Sendchamp (SMS/OTP), Prembly/IdentityPass
(KYC: BVN/NIN/face). Each function returns {"success": bool, ...}. When the
relevant key is blank it runs in MOCK mode and simulates success so the whole app
flow is testable without an external account — EXCEPT in production (DEBUG off),
where the VTU mock fails closed (see mock_disabled_in_prod) so a misconfigured
deploy never fakes a money movement.

TODO before go-live: verify each provider's exact request/response field names,
endpoints and auth against their dashboards/docs. Live calls can't be exercised
from CI, so the shapes below are documented best-effort scaffolding; the MOCK
paths are the source of truth until real keys are configured.
"""
import base64
import hashlib
import hmac
import logging
import secrets

import requests
from django.conf import settings

REQUEST_TIMEOUT = 30
log = logging.getLogger("zitch")


# ---------------------------------------------------------------------------
# Payments (wallet funding) — Monnify
# ---------------------------------------------------------------------------
def payments_live() -> bool:
    m = settings.MONNIFY
    return bool(m["API_KEY"] and m["SECRET_KEY"] and m["CONTRACT_CODE"])


def _monnify_token() -> str:
    """OAuth login: Basic base64(apiKey:secretKey) -> bearer access token."""
    m = settings.MONNIFY
    basic = base64.b64encode(f"{m['API_KEY']}:{m['SECRET_KEY']}".encode()).decode()
    resp = requests.post(
        f"{m['BASE_URL']}/api/v1/auth/login",
        headers={"Authorization": f"Basic {basic}"},
        timeout=REQUEST_TIMEOUT,
    )
    return (resp.json().get("responseBody", {}) or {}).get("accessToken", "")


def payment_initialize(email: str, amount_naira, reference: str) -> dict:
    """Start a funding transaction; returns a checkout URL the app opens.

    MOCK mode returns a sentinel URL so funding is testable offline (the tester
    'completes' it by calling the verify endpoint).
    """
    if not payments_live():
        return {
            "success": True, "mock": True, "reference": reference,
            "authorization_url": f"mock://monnify/checkout/{reference}",
        }
    try:
        token = _monnify_token()
        if not token:
            log.warning("monnify_auth_failed base=%s", settings.MONNIFY["BASE_URL"])
            return {"success": False, "message": (
                "Payment gateway authentication failed — verify MONNIFY_API_KEY/SECRET_KEY and "
                "that MONNIFY_BASE_URL matches them (live: https://api.monnify.com, "
                "test: https://sandbox.monnify.com)."
            )}
        m = settings.MONNIFY
        resp = requests.post(
            f"{m['BASE_URL']}/api/v1/merchant/transactions/init-transaction",
            json={
                "amount": float(amount_naira),  # Monnify amounts are in naira
                "customerName": (email or "Zitch user").split("@")[0],
                "customerEmail": email,
                "paymentReference": reference,
                "contractCode": m["CONTRACT_CODE"],
                "currencyCode": "NGN",
                "redirectUrl": m.get("REDIRECT_URL", ""),
                "paymentMethods": ["CARD", "ACCOUNT_TRANSFER"],
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        success = bool(data.get("requestSuccessful")) and bool(rb.get("checkoutUrl"))
        if not success:
            # Surface Monnify's actual reason (bad contract code, inactive merchant,
            # live/sandbox mismatch) instead of a generic failure, and log it so ops
            # can see why funding is failing.
            log.warning("monnify_init_failed ref=%s code=%s msg=%s",
                        reference, data.get("responseCode"), data.get("responseMessage"))
        return {
            "success": success,
            "reference": rb.get("paymentReference", reference),
            "authorization_url": rb.get("checkoutUrl", ""),
            "message": data.get("responseMessage") or "Could not start payment",
            "raw": data,
        }
    except requests.RequestException as exc:
        return {"success": False, "message": f"Payment gateway unreachable: {exc}"}


def payment_verify(reference: str) -> dict:
    """Confirm a transaction with Monnify (source of truth for crediting).

    MOCK mode treats any reference as paid so funding works without real money.
    """
    if not payments_live():
        return {"success": True, "mock": True, "amount_naira": None, "reference": reference}
    try:
        token = _monnify_token()
        m = settings.MONNIFY
        resp = requests.get(
            f"{m['BASE_URL']}/api/v1/merchant/transactions/query",
            params={"paymentReference": reference},
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        paid = bool(data.get("requestSuccessful")) and rb.get("paymentStatus") == "PAID"
        return {
            "success": paid,
            "amount_naira": rb.get("amountPaid"),
            "reference": rb.get("paymentReference", reference),
            "raw": data,
        }
    except requests.RequestException as exc:
        return {"success": False, "message": f"Payment gateway unreachable: {exc}"}


def payment_verify_signature(body: bytes, signature: str) -> bool:
    """Validate a Monnify webhook via the `monnify-signature` header
    (SHA-512 HMAC of the raw body with the secret key)."""
    if not payments_live():
        # No keys = mock mode. Accept ONLY in dev/test so local webhook testing
        # works. In production this MUST fail closed: a Monnify webhook moves
        # money (credits a wallet on funding; refunds a payout on disbursement
        # failure), so an unsigned/forged callback in a keyless prod deploy would
        # otherwise mint free credit or reverse a real transfer. (CRITICAL.)
        return not mock_disabled_in_prod()
    if not signature:
        return False
    digest = hmac.new(settings.MONNIFY["SECRET_KEY"].encode(), body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(digest, signature)


# ---------------------------------------------------------------------------
# Reserved (dedicated) virtual accounts — Monnify Bank Transfer
#
# Mints a permanent NUBAN per user that funds the wallet by bank transfer (no
# checkout page). Monnify requires the customer's BVN/NIN for a dedicated
# account (CBN compliance), so reservation is driven from the KYC step where the
# raw number is still in hand. Inbound funding arrives as a SUCCESSFUL_TRANSACTION
# webhook whose eventData.product.type is RESERVED_ACCOUNT — credited by
# wallet.services.credit_reserved_account_funding. Blank Monnify keys => MOCK.
# ---------------------------------------------------------------------------
def _parse_reserved(data: dict) -> dict:
    """Normalise a reserve/get-reserved-account response into our shape.

    V2 with getAllAvailableBanks returns the issued accounts under ``accounts``;
    the single-bank shape carries accountNumber/bankName at the top level. We
    surface both: a flat primary (account_number/bank_name) plus the full list.
    """
    rb = data.get("responseBody", {}) or {}
    accounts = [
        {"bank_name": a.get("bankName", ""), "account_number": a.get("accountNumber", ""),
         "bank_code": a.get("bankCode", "")}
        for a in (rb.get("accounts") or []) if a.get("accountNumber")
    ]
    primary_num = accounts[0]["account_number"] if accounts else rb.get("accountNumber", "")
    primary_bank = accounts[0]["bank_name"] if accounts else rb.get("bankName", "")
    return {
        "success": bool(data.get("requestSuccessful")) and bool(primary_num),
        "account_number": primary_num,
        "bank_name": primary_bank,
        "account_name": rb.get("accountName", ""),
        "reference": rb.get("accountReference", ""),
        "reservation_reference": rb.get("reservationReference", ""),
        "accounts": accounts,
        "message": data.get("responseMessage", "Could not reserve account"),
        "raw": data,
    }


def reserve_account(account_reference: str, account_name: str, customer_email: str,
                    customer_name: str, bvn: str = "", nin: str = "") -> dict:
    """Reserve a dedicated virtual account for a customer.

    POST /api/v2/bank-transfer/reserved-accounts. ``account_reference`` is our
    stable per-user key (Monnify rejects a duplicate, so reuse it). MOCK mode
    fabricates a deterministic NUBAN so the funding flow is testable offline.
    """
    if not payments_live():
        seed = int(hashlib.sha256(account_reference.encode()).hexdigest(), 16)
        num = "99" + f"{seed % 10**8:08d}"
        return {
            "success": True, "mock": True,
            "account_number": num, "bank_name": "Moniepoint MFB",
            "account_name": account_name, "reference": account_reference,
            "reservation_reference": "MOCK-" + secrets.token_hex(6).upper(),
            "accounts": [{"bank_name": "Moniepoint MFB", "account_number": num, "bank_code": "50515"}],
        }
    try:
        token = _monnify_token()
        if not token:
            return {"success": False, "message": "Monnify authentication failed"}
        m = settings.MONNIFY
        body = {
            "accountReference": account_reference,
            "accountName": account_name,
            "currencyCode": "NGN",
            "contractCode": m["CONTRACT_CODE"],
            "customerEmail": customer_email,
            "customerName": customer_name,
            "getAllAvailableBanks": True,
        }
        # Monnify accepts either; supply whichever KYC value we hold.
        if bvn:
            body["bvn"] = bvn
        if nin:
            body["nin"] = nin
        resp = requests.post(
            f"{m['BASE_URL']}/api/v2/bank-transfer/reserved-accounts",
            json=body, headers={"Authorization": f"Bearer {token}"}, timeout=REQUEST_TIMEOUT,
        )
        out = _parse_reserved(resp.json())
        if not out["success"]:
            log.warning("monnify_reserve_failed ref=%s msg=%s", account_reference, out.get("message"))
        return out
    except requests.RequestException as exc:
        return {"success": False, "message": f"Payment gateway unreachable: {exc}"}


def get_reserved_account(account_reference: str) -> dict:
    """Fetch an existing reserved account by our accountReference.

    GET /api/v2/bank-transfer/reserved-accounts/{accountReference}. Used to
    recover the account details when a prior reserve_account succeeded at Monnify
    but we failed to persist it (a re-create would be rejected as a duplicate).
    """
    if not payments_live():
        return {"success": False, "message": "mock"}
    try:
        token = _monnify_token()
        if not token:
            return {"success": False, "message": "Monnify authentication failed"}
        m = settings.MONNIFY
        resp = requests.get(
            f"{m['BASE_URL']}/api/v2/bank-transfer/reserved-accounts/{account_reference}",
            headers={"Authorization": f"Bearer {token}"}, timeout=REQUEST_TIMEOUT,
        )
        return _parse_reserved(resp.json())
    except requests.RequestException as exc:
        return {"success": False, "message": f"Payment gateway unreachable: {exc}"}


# ---------------------------------------------------------------------------
# Bank transfers / payouts — Monnify disbursements
#
# Draws from your Monnify wallet (MONNIFY_SOURCE_ACCOUNT) to any NIBSS bank.
# Name enquiry is mandatory: Monnify rejects a single transfer whose
# destinationAccountName doesn't match the enquiry result, so callers resolve
# server-side and pass the authoritative name.
# ---------------------------------------------------------------------------
def disbursement_resolve_account(account_number: str, bank_code: str) -> dict:
    """Name enquiry: account number + NIBSS bank code -> account holder name."""
    if not payments_live():
        return {"success": True, "mock": True, "name": "ADEYEMI WILLIAM"}
    try:
        token = _monnify_token()
        m = settings.MONNIFY
        resp = requests.get(
            f"{m['BASE_URL']}/api/v1/disbursements/account/validate",
            params={"accountNumber": account_number, "bankCode": bank_code},
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        name = rb.get("accountName", "")
        return {"success": bool(data.get("requestSuccessful")) and bool(name), "name": name, "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"Payout provider unreachable: {exc}"}


def disbursement_send(amount_naira, reference: str, narration: str,
                      bank_code: str, account_number: str, account_name: str) -> dict:
    """Initiate a single transfer to a bank account.

    SUCCESS/PENDING means Monnify accepted it (money sent or queued); anything
    else — including a 2FA OTP-authorization requirement — is treated as
    not-sent so the caller refunds the wallet.
    TODO before relying on PENDING: handle the OTP-authorization step and a
    disbursement webhook (SUCCESSFUL/FAILED_DISBURSEMENT) to reconcile finally.
    """
    if not payments_live():
        return {"success": True, "mock": True, "status": "SUCCESS"}
    m = settings.MONNIFY
    if not m.get("SOURCE_ACCOUNT"):
        return {"success": False, "message": "MONNIFY_SOURCE_ACCOUNT is not configured"}
    try:
        token = _monnify_token()
        resp = requests.post(
            f"{m['BASE_URL']}/api/v2/disbursements/single",
            json={
                "amount": float(amount_naira),
                "reference": reference,
                "narration": narration or "Zitch transfer",
                "destinationBankCode": bank_code,
                "destinationAccountNumber": account_number,
                "destinationAccountName": account_name,
                "currency": "NGN",
                "sourceAccountNumber": m["SOURCE_ACCOUNT"],
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        status = (data.get("responseBody", {}) or {}).get("status", "")
        return {
            "success": bool(data.get("requestSuccessful")) and status in ("SUCCESS", "PENDING", "COMPLETED"),
            "status": status,
            "message": data.get("responseMessage", "Transfer not completed"),
            "raw": data,
        }
    except requests.RequestException as exc:
        return {"success": False, "message": f"Payout provider unreachable: {exc}"}


# ---------------------------------------------------------------------------
# KYC / identity — Monnify Verification (VAS)
#
# An ALTERNATE to the Prembly KYC block below: same BVN/NIN intent, but vended
# through Monnify's VAS endpoints and authed with the same Monnify OAuth token
# (payments_live() gate + _monnify_token()). Exposed as standalone monnify_*
# functions so the app's KYC provider can be switched without disturbing
# Prembly. Blank Monnify keys => MOCK mode, matching the rest of this file.
#
# VERIFY-BEFORE-LIVE: the /api/v1/vas/* paths and field names follow Monnify's
# published VAS docs but can't be exercised from CI — confirm against the
# dashboard before go-live. Each VAS call is metered/billed by Monnify.
# ---------------------------------------------------------------------------
def monnify_verify_bvn(bvn: str, name: str = "", date_of_birth: str = "",
                       mobile: str = "") -> dict:
    """BVN information match: confirm the BVN exists and (optionally) that the
    supplied name / DOB / mobile match what's linked to it.

    POST /api/v1/vas/bvn-details-match. MOCK accepts any 11-digit BVN. ``match``
    carries Monnify's per-field result (``bvnInformationMatch``) when live.
    """
    if len(bvn) != 11 or not bvn.isdigit():
        return {"success": False, "message": "BVN must be 11 digits"}
    if not payments_live():
        return {"success": True, "mock": True, "match": {}}
    try:
        token = _monnify_token()
        if not token:
            return {"success": False, "message": "Monnify authentication failed"}
        m = settings.MONNIFY
        resp = requests.post(
            f"{m['BASE_URL']}/api/v1/vas/bvn-details-match",
            json={"bvn": bvn, "name": name, "dateOfBirth": date_of_birth, "mobileNo": mobile},
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        return {
            "success": bool(data.get("requestSuccessful")) and bool(rb),
            "match": rb.get("bvnInformationMatch", {}),
            "raw": data,
        }
    except requests.RequestException as exc:
        return {"success": False, "message": f"KYC provider unreachable: {exc}"}


def monnify_match_bvn_account(bvn: str, bank_code: str, account_number: str) -> dict:
    """Confirm a BVN is linked to a given bank account.

    POST /api/v1/vas/bvn-account-match. MOCK accepts any 11-digit BVN.
    """
    if len(bvn) != 11 or not bvn.isdigit():
        return {"success": False, "message": "BVN must be 11 digits"}
    if not payments_live():
        return {"success": True, "mock": True, "matched": True}
    try:
        token = _monnify_token()
        if not token:
            return {"success": False, "message": "Monnify authentication failed"}
        m = settings.MONNIFY
        resp = requests.post(
            f"{m['BASE_URL']}/api/v1/vas/bvn-account-match",
            json={"bankCode": bank_code, "accountNumber": account_number, "bvn": bvn},
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        matched = str(rb.get("matchStatus") or rb.get("accountNameMatch") or "").upper().startswith(
            ("FULL", "MATCH", "TRUE"))
        return {"success": bool(data.get("requestSuccessful")), "matched": matched, "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"KYC provider unreachable: {exc}"}


def monnify_verify_nin(nin: str) -> dict:
    """Verify a NIN against NIMC via Monnify VAS.

    POST /api/v1/vas/nin-details. MOCK accepts any 11-digit NIN.
    VERIFY-BEFORE-LIVE: confirm the exact path/field ('nin') on the dashboard.
    """
    if len(nin) != 11 or not nin.isdigit():
        return {"success": False, "message": "NIN must be 11 digits"}
    if not payments_live():
        return {"success": True, "mock": True}
    try:
        token = _monnify_token()
        if not token:
            return {"success": False, "message": "Monnify authentication failed"}
        m = settings.MONNIFY
        resp = requests.post(
            f"{m['BASE_URL']}/api/v1/vas/nin-details",
            json={"nin": nin},
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        return {"success": bool(data.get("requestSuccessful")) and bool(rb), "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"KYC provider unreachable: {exc}"}


# ---------------------------------------------------------------------------
# Bills payment — Monnify Biller Service (airtime / data / electricity / cable)
#
# An ALTERNATE to the VTU.ng VTU block: Monnify's unified Biller Service.
# Flow is Discovery (categories -> billers -> products) -> Validate (customer)
# -> Vend (pay) -> requery. Same Monnify OAuth token + payments_live() gate.
# Blank Monnify keys => MOCK mode.
#
# VERIFY-BEFORE-LIVE: the bills endpoint paths are centralised in _MONNIFY_BILLS
# below and follow Monnify's bills docs, but couldn't be exercised from CI —
# confirm each path/field against the dashboard before go-live.
# ---------------------------------------------------------------------------
_MONNIFY_BILLS = "/api/v1/bill-payment"  # VERIFY-BEFORE-LIVE: Biller Service base path


def _monnify_bills_get(path: str, params: dict | None = None) -> dict:
    """Shared GET for the bills discovery / requery endpoints."""
    token = _monnify_token()
    if not token:
        return {"success": False, "message": "Monnify authentication failed"}
    m = settings.MONNIFY
    resp = requests.get(
        f"{m['BASE_URL']}{path}", params=params or {},
        headers={"Authorization": f"Bearer {token}"}, timeout=REQUEST_TIMEOUT,
    )
    data = resp.json()
    return {"success": bool(data.get("requestSuccessful")),
            "responseBody": data.get("responseBody"), "raw": data}


def monnify_bill_categories() -> dict:
    """List biller categories (AIRTIME, DATA, ELECTRICITY, CABLE_TV, ...)."""
    if not payments_live():
        return {"success": True, "mock": True,
                "responseBody": ["AIRTIME", "DATA", "ELECTRICITY", "CABLE_TV"]}
    try:
        return _monnify_bills_get(f"{_MONNIFY_BILLS}/biller-categories")
    except requests.RequestException as exc:
        return {"success": False, "message": f"Bills provider unreachable: {exc}"}


def monnify_billers(category_code: str) -> dict:
    """List active billers for a category (filtered by categoryCode)."""
    if not payments_live():
        return {"success": True, "mock": True,
                "responseBody": [{"billerCode": "MOCK-BILLER", "name": f"Mock {category_code} biller"}]}
    try:
        return _monnify_bills_get(f"{_MONNIFY_BILLS}/billers", {"categoryCode": category_code})
    except requests.RequestException as exc:
        return {"success": False, "message": f"Bills provider unreachable: {exc}"}


def monnify_biller_products(biller_code: str) -> dict:
    """List products offered by a biller (filtered by billerCode)."""
    if not payments_live():
        return {"success": True, "mock": True,
                "responseBody": [{"productCode": "MOCK-PROD", "name": "Mock product", "amount": 1000}]}
    try:
        return _monnify_bills_get(f"{_MONNIFY_BILLS}/billers/products", {"billerCode": biller_code})
    except requests.RequestException as exc:
        return {"success": False, "message": f"Bills provider unreachable: {exc}"}


def monnify_validate_customer(product_code: str, customer_id: str) -> dict:
    """Validate a customer (meter / smartcard / phone) for a product.

    Surfaces the resolved customer name and whether a validationReference must
    be threaded into the vend (Monnify's vendInstruction.requireValidationRef).
    """
    if not payments_live():
        return {"success": True, "mock": True, "customer_name": "ADEYEMI WILLIAM",
                "requires_validation_ref": False, "validation_reference": ""}
    try:
        token = _monnify_token()
        if not token:
            return {"success": False, "message": "Monnify authentication failed"}
        m = settings.MONNIFY
        resp = requests.post(
            f"{m['BASE_URL']}{_MONNIFY_BILLS}/validate-customer",
            json={"productCode": product_code, "customerId": customer_id},
            headers={"Authorization": f"Bearer {token}"}, timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        vi = rb.get("vendInstruction", {}) or {}
        return {
            "success": bool(data.get("requestSuccessful")),
            "customer_name": rb.get("customerName") or rb.get("name", ""),
            "requires_validation_ref": bool(vi.get("requireValidationRef")),
            "validation_reference": vi.get("validationReference", ""),
            "raw": data,
        }
    except requests.RequestException as exc:
        return {"success": False, "message": f"Bills provider unreachable: {exc}"}


def monnify_pay_bill(product_code: str, customer_id: str, amount_naira,
                     reference: str, validation_reference: str = "") -> dict:
    """Vend a bill (airtime / data / electricity / cable / ...).

    Pass ``reference`` as the idempotency key (the wallet ledger reference) and
    the ``validation_reference`` from monnify_validate_customer when the product
    requires one. On a network error returns ``pending=True`` so the caller does
    NOT refund — reconcile via monnify_bill_status instead. ``token`` carries a
    prepaid-meter token where the biller returns one.
    """
    if not payments_live():
        return {"success": True, "mock": True, "status": "SUCCESS",
                "provider_reference": "MOCK-" + secrets.token_hex(6).upper(), "token": ""}
    try:
        token = _monnify_token()
        if not token:
            return {"success": False, "message": "Monnify authentication failed"}
        m = settings.MONNIFY
        body = {
            "productCode": product_code,
            "customerId": customer_id,
            "amount": float(amount_naira),
            "reference": reference,
        }
        if validation_reference:
            body["validationReference"] = validation_reference
        resp = requests.post(
            f"{m['BASE_URL']}{_MONNIFY_BILLS}/process-bill",
            json=body, headers={"Authorization": f"Bearer {token}"}, timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        rb = data.get("responseBody", {}) or {}
        status = str(rb.get("status") or rb.get("transactionStatus", "")).upper()
        out = {
            "status": status,
            "provider_reference": str(rb.get("transactionReference") or rb.get("reference", "")),
            "token": rb.get("token", ""),
            "raw": data,
        }
        accepted = bool(data.get("requestSuccessful"))
        # A vend Monnify ACCEPTS but has not yet delivered (PENDING/PROCESSING, or
        # an accepted call with no terminal status yet) is NOT a success: returning
        # success here would let settle_or_refund mark the debit Successful and clear
        # the reconcile flag, so the requery job would never run — money debited,
        # never delivered, never refunded. Mirror monnify_bill_status: pending stays
        # pending so the caller reconciles via monnify_bill_status rather than either
        # claiming delivery or blind-refunding a vend that may still land.
        # An outright rejection (requestSuccessful=False, or a terminal failure
        # status) refunds, since the vend did not enter Monnify's queue.
        if accepted and status in ("SUCCESS", "SUCCESSFUL", "DELIVERED"):
            return {"success": True, **out}
        if accepted and status in ("PENDING", "PROCESSING", ""):
            return {"success": False, "pending": True, **out}
        return {"success": False, **out}
    except requests.RequestException as exc:
        return {"success": False, "pending": True, "message": f"Bills provider unreachable: {exc}"}


def monnify_bill_status(reference: str) -> dict:
    """Requery a vend by our reference to settle a PENDING bill.

    Returns the {"success", "pending", ...} shape settle_or_refund expects:
    success => delivered; pending => still unknown (retry later); neither =>
    a definitive failure the caller refunds.
    """
    if not payments_live():
        return {"success": True, "mock": True, "status": "SUCCESS"}
    try:
        out = _monnify_bills_get(f"{_MONNIFY_BILLS}/transaction-status", {"reference": reference})
        rb = out.get("responseBody", {}) or {}
        status = str(rb.get("status") or rb.get("transactionStatus", "")).upper()
        if status in ("SUCCESS", "SUCCESSFUL", "DELIVERED"):
            return {"success": True, "status": status, "raw": out.get("raw")}
        if status in ("PENDING", "PROCESSING", ""):
            return {"success": False, "pending": True, "status": status or "PENDING", "raw": out.get("raw")}
        return {"success": False, "status": status, "raw": out.get("raw")}
    except requests.RequestException:
        return {"success": False, "pending": True, "message": "Requery failed; will retry"}


# ---------------------------------------------------------------------------
# VTU (airtime / data / cable / electricity / betting) — VTU.ng
#
# VTU.ng is the sole VTU provider; its client lives in utility/vtung.py. The
# vtu_purchase / vtu_requery / vtu_verify_customer wrappers below are the stable
# contract the views and the reconcile job call, so callers never import the
# provider module directly.
# ---------------------------------------------------------------------------
def mock_disabled_in_prod() -> bool:
    """True when a provider's MOCK responses must be suppressed.

    A money provider with no credentials falls back to MOCK mode, which *fakes
    success*. That's fine in dev/tests, but in production it would tell a customer
    their airtime/data purchase succeeded while nothing was delivered (and the
    wallet was debited). When this returns True, the provider must fail closed
    instead — the debit is then refunded by the normal failure path.
    """
    return not settings.DEBUG and not getattr(settings, "TESTING", False)


def vtu_live() -> bool:
    """Whether the VTU provider (VTU.ng) has credentials configured."""
    from .vtung import _live
    return _live()


def vtu_purchase(service_id: str, payload: dict, reference: str | None = None) -> dict:
    """Submit a VTU purchase via VTU.ng.

    Pass the wallet ledger `reference` so it becomes VTU.ng's request_id
    (idempotency key + requery handle). On a network error returns
    ``pending=True``: the purchase may have landed, so the caller must NOT refund
    — reconciliation requeries it by reference instead.
    """
    from .vtung import vt_purchase
    return vt_purchase(service_id, payload, reference)


def vtu_requery(reference: str) -> dict:
    """Requery a submitted purchase by our request_id to settle a PENDING
    transaction (e.g. one whose original send timed out).

    Returns the {"success", "pending", ...} shape settle_or_refund expects:
    success => delivered; pending => still unknown (retry later); neither =>
    a definitive failure the caller refunds.
    """
    from .vtung import vt_requery
    return vt_requery(reference)


def vtu_verify_customer(service_id: str, billers_code: str, variation: str = "") -> dict:
    """Validate a meter / smartcard number, returning the customer name."""
    from .vtung import vt_verify_customer
    return vt_verify_customer(service_id, billers_code, variation)


# ---------------------------------------------------------------------------
# SMS / OTP — Sendchamp
# ---------------------------------------------------------------------------
def send_sms(phone: str, message: str) -> dict:
    cfg = settings.SENDCHAMP
    if not cfg["API_KEY"]:
        return {"success": True, "mock": True, "message": "SMS sent (mock mode)"}
    try:
        resp = requests.post(
            f"{cfg['BASE_URL']}/sms/send",
            json={
                "to": [phone],
                "message": message,
                "sender_name": cfg["SENDER_NAME"],
                "route": "dnd",
            },
            headers={
                "Authorization": f"Bearer {cfg['API_KEY']}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        return {"success": resp.ok and str(data.get("status", "")).lower() == "success", "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"SMS provider unreachable: {exc}"}


def send_email(to: str, subject: str, message: str, html: str | None = None) -> dict:
    """Send a transactional email via Resend. Mirrors send_sms's mock-mode
    contract: blank API_KEY or empty `to` returns a silent-success dict so
    callers can fire-and-forget without branching on configuration. Used as a
    parallel OTP channel alongside Sendchamp so SMS routing issues never strand
    a user mid-signup. Pass `html` for a branded body (the plain `message` is
    kept as the text fallback for clients that don't render HTML)."""
    cfg = settings.RESEND
    if not cfg["API_KEY"] or not to:
        return {"success": True, "mock": True, "message": "Email sent (mock mode)"}
    payload = {"from": cfg["FROM_EMAIL"], "to": [to], "subject": subject, "text": message}
    if html:
        payload["html"] = html
    try:
        resp = requests.post(
            f"{cfg['BASE_URL']}/emails",
            json=payload,
            headers={
                "Authorization": f"Bearer {cfg['API_KEY']}",
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json() if resp.content else {}
        return {"success": resp.ok and "id" in data, "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"Email provider unreachable: {exc}"}


# ---------------------------------------------------------------------------
# KYC — BVN / NIN / liveness — Prembly (IdentityPass)
# ---------------------------------------------------------------------------
def _prembly_live() -> bool:
    return bool(settings.PREMBLY["API_KEY"] and settings.PREMBLY["APP_ID"])


def _prembly_headers() -> dict:
    return {
        "x-api-key": settings.PREMBLY["API_KEY"],
        "app-id": settings.PREMBLY["APP_ID"],
        "Content-Type": "application/json",
    }


def kyc_verify_bvn(bvn: str) -> dict:
    """Verify a BVN. MOCK mode accepts any 11-digit value."""
    if len(bvn) != 11 or not bvn.isdigit():
        return {"success": False, "message": "BVN must be 11 digits"}
    if not _prembly_live():
        return {"success": True, "mock": True, "first_name": "", "last_name": ""}
    try:
        resp = requests.post(
            f"{settings.PREMBLY['BASE_URL']}/identitypass/verification/bvn",
            json={"number": bvn}, headers=_prembly_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        d = data.get("data", {}) or {}
        return {"success": bool(data.get("status")) and bool(d), "raw": data,
                "first_name": d.get("first_name", ""), "last_name": d.get("last_name", "")}
    except requests.RequestException as exc:
        return {"success": False, "message": f"KYC provider unreachable: {exc}"}


def kyc_verify_nin(nin: str) -> dict:
    """Verify a NIN. MOCK mode accepts any 11-digit value."""
    if len(nin) != 11 or not nin.isdigit():
        return {"success": False, "message": "NIN must be 11 digits"}
    if not _prembly_live():
        return {"success": True, "mock": True}
    try:
        resp = requests.post(
            f"{settings.PREMBLY['BASE_URL']}/identitypass/verification/nin",
            json={"number": nin}, headers=_prembly_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        return {"success": bool(data.get("status")), "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"KYC provider unreachable: {exc}"}


def kyc_verify_nin_document(image: str) -> dict:
    """Verify an uploaded NIN slip / ID image (OCR + match). MOCK accepts
    offline; LIVE must call Prembly's document endpoint and fail closed without
    a real pass. VERIFY-BEFORE-LIVE: confirm the exact endpoint/field names on
    the Prembly dashboard before relying on this."""
    if not _prembly_live():
        return {"success": True, "mock": True}
    if not image:
        return {"success": False, "message": "Upload your NIN slip to continue"}
    try:
        resp = requests.post(
            f"{settings.PREMBLY['BASE_URL']}/identitypass/verification/document/analysis",
            json={"doc_type": "nin", "image": image},
            headers=_prembly_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        return {"success": bool(data.get("status")), "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"KYC provider unreachable: {exc}"}


def kyc_verify_face(selfie: str = "") -> dict:
    """Liveness / selfie-match — the gate for large transfers.

    MOCK accepts offline. LIVE requires a real liveness result AND a captured
    selfie; fails closed without one, so the step-up can't be cleared without
    genuine verification once a provider is configured.
    """
    if not _prembly_live():
        return {"success": True, "mock": True}
    if not selfie:
        return {"success": False, "message": "A selfie capture is required for face verification"}
    try:
        resp = requests.post(
            f"{settings.PREMBLY['BASE_URL']}/identitypass/verification/biometrics/face",
            json={"image": selfie}, headers=_prembly_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        d = data.get("data", {}) or {}
        return {"success": bool(data.get("status")) and bool(d.get("liveness") or d.get("face_match")), "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"KYC provider unreachable: {exc}"}


# ---------------------------------------------------------------------------
# Card issuer (virtual cards) — provider TBD. Blank key => MOCK mode.
# ---------------------------------------------------------------------------
def _card_issuer_live() -> bool:
    return bool(settings.CARD_ISSUER["API_KEY"])


def _card_issuer_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.CARD_ISSUER['API_KEY']}",
        "Content-Type": "application/json",
    }


def issue_card(holder: str, customer_ref: str) -> dict:
    """Create a virtual card with the issuer. MOCK fabricates presentation data."""
    if not _card_issuer_live():
        return {
            "success": True, "mock": True,
            "card_token": "mock_" + secrets.token_hex(8),
            "brand": settings.CARD_ISSUER.get("BRAND", "Verve"),
            "last4": f"{secrets.randbelow(10000):04d}",
            "expiry": f"{1 + secrets.randbelow(12):02d}/{29 + secrets.randbelow(3)}",
        }
    try:
        resp = requests.post(
            f"{settings.CARD_ISSUER['BASE_URL']}/cards",
            json={"type": "virtual", "currency": "NGN",
                  "brand": settings.CARD_ISSUER.get("BRAND", "Verve"),
                  "holderName": holder, "customerId": customer_ref},
            headers=_card_issuer_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        d = data.get("data", {}) or {}
        return {
            "success": bool(d.get("_id") or d.get("id")),
            "card_token": d.get("_id") or d.get("id", ""),
            "brand": d.get("brand", settings.CARD_ISSUER.get("BRAND", "Verve")),
            "last4": (d.get("maskedPan") or d.get("number") or "")[-4:],
            "expiry": f"{d.get('expiryMonth', '')}/{str(d.get('expiryYear', ''))[-2:]}",
            "raw": data,
        }
    except requests.RequestException as exc:
        return {"success": False, "message": f"Card issuer unreachable: {exc}"}


def set_card_status(card_token: str, active: bool) -> dict:
    """Freeze/unfreeze a card with the issuer. MOCK always succeeds."""
    if not _card_issuer_live():
        return {"success": True, "mock": True}
    try:
        resp = requests.put(
            f"{settings.CARD_ISSUER['BASE_URL']}/cards/{card_token}",
            json={"status": "active" if active else "inactive"},
            headers=_card_issuer_headers(), timeout=REQUEST_TIMEOUT,
        )
        return {"success": resp.ok, "raw": resp.json()}
    except requests.RequestException as exc:
        return {"success": False, "message": f"Card issuer unreachable: {exc}"}


def card_secure_details(card_token: str) -> dict:
    """Fetch full PAN/CVV for a one-time reveal. Never persisted server-side.

    MOCK returns a deterministic-looking fake so the reveal UI works.
    """
    if not _card_issuer_live():
        seed = int(hashlib.sha256(card_token.encode()).hexdigest(), 16)
        pan = "5061" + "".join(str((seed >> (i * 4)) % 10) for i in range(12))
        cvv = f"{seed % 1000:03d}"
        return {"success": True, "mock": True, "pan": pan, "cvv": cvv}
    try:
        resp = requests.get(
            f"{settings.CARD_ISSUER['BASE_URL']}/cards/{card_token}/secure-data",
            headers=_card_issuer_headers(), timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        d = data.get("data", {}) or {}
        return {"success": resp.ok, "pan": d.get("number", ""), "cvv": d.get("cvv2", ""), "raw": data}
    except requests.RequestException as exc:
        return {"success": False, "message": f"Card issuer unreachable: {exc}"}


def fund_card(card_token: str, amount) -> dict:
    """Top up an issued card from the funding source. MOCK succeeds."""
    if not _card_issuer_live():
        return {"success": True, "mock": True}
    try:
        resp = requests.post(
            f"{settings.CARD_ISSUER['BASE_URL']}/cards/{card_token}/fund",
            json={"amount": float(amount), "currency": "NGN"},
            headers=_card_issuer_headers(), timeout=REQUEST_TIMEOUT,
        )
        return {"success": resp.ok, "raw": resp.json()}
    except requests.RequestException as exc:
        return {"success": False, "message": f"Card issuer unreachable: {exc}"}


# --------------------------------------------------------------------------- #
# Fincra — FX conversion rail (multi-currency). MOCK mode when no secret key:
# deterministic mid-market rates + auto-settle, so the flow is testable offline.
# --------------------------------------------------------------------------- #
def fincra_live() -> bool:
    return bool(settings.FINCRA.get("SECRET_KEY"))


# Mock mid-market reference (NGN per 1 unit) — only used without keys.
_NGN_PER = {"NGN": "1", "USD": "1600", "GBP": "2000", "CAD": "1150", "CNY": "220"}


def fx_quote(from_ccy: str, to_ccy: str, sell_amount) -> dict:
    """Quote a conversion: {success, rate, receive_amount, quote_ref, ttl_seconds}.
    `rate` is units of `to` per 1 `from`."""
    from decimal import Decimal

    if not fincra_live():
        f, t = _NGN_PER.get(from_ccy), _NGN_PER.get(to_ccy)
        if f is None or t is None:
            return {"success": False, "message": f"Unsupported pair {from_ccy}/{to_ccy}"}
        rate = Decimal(f) / Decimal(t)
        receive = Decimal(str(sell_amount)) * rate
        return {"success": True, "mock": True, "rate": rate, "receive_amount": receive,
                "quote_ref": "FXQ-" + secrets.token_hex(6).upper(), "ttl_seconds": 90}
    try:
        r = requests.post(
            f"{settings.FINCRA['BASE_URL']}/quotes",
            json={"action": "send", "sourceCurrency": from_ccy, "destinationCurrency": to_ccy,
                  "amount": str(sell_amount), "feeBearer": "business"},
            headers={"api-key": settings.FINCRA["SECRET_KEY"]}, timeout=20,
        )
        d = (r.json() or {}).get("data", {})
        if not r.ok or not d.get("rate"):
            return {"success": False, "message": (r.json() or {}).get("message", "Quote failed")}
        return {"success": True, "rate": d["rate"], "receive_amount": d.get("destinationAmount"),
                "quote_ref": d.get("reference") or d.get("quoteReference", ""), "ttl_seconds": int(d.get("expiry", 90))}
    except requests.RequestException as exc:
        return {"success": False, "message": f"FX provider unreachable: {exc}"}


def fx_execute(quote_ref: str) -> dict:
    """Execute a previously quoted conversion against its quote reference."""
    if not fincra_live():
        return {"success": True, "mock": True}
    try:
        r = requests.post(
            f"{settings.FINCRA['BASE_URL']}/conversions",
            json={"quoteReference": quote_ref, "business": settings.FINCRA.get("BUSINESS_ID", "")},
            headers={"api-key": settings.FINCRA["SECRET_KEY"]}, timeout=30,
        )
        return {"success": bool(r.ok), "raw": (r.json() if r.content else {})}
    except requests.RequestException as exc:
        return {"success": False, "message": f"FX execute failed: {exc}"}
