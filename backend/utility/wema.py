"""Wema / ALAT (Banking-as-a-Service) integration — Phase 1: the money rails.

Covers, mock-first:
- Wallet creation: provision a dedicated NUBAN per user via a BVN/NIN + OTP flow
  (request -> validate OTP -> fetch account details). Funds arrive by bank
  transfer; Wema exposes NO inbound-credit webhook in the specs, so credits are
  reconciled by POLLING balance / transaction history.
- Balance + transaction history (account maintenance).
- Payout: bank list, recipient name enquiry, process transfer, poll status.
- Credit wallet: push a credit into a wallet from the channel funding account.

AUTH (Azure APIM) — TWO credentials per call:
  * per-PRODUCT subscription key -> header ``Ocp-Apim-Subscription-Key``
  * channel id -> header ``x-api-key`` on most products, ``access`` on the
    credit/debit-wallet products. (Same value; different header name.)
Per-product base path under one host: sandbox ``https://apiplayground.alat.ng``;
the LIVE host differs (set WEMA_BASE_URL).

securityInfo: every MONEY-MOVEMENT call (transfer / credit / VAS) requires an
encrypted ``securityInfo`` whose construction is NOT in the OpenAPI. ``_security_info``
is the single place to implement it once Wema supplies the scheme. Account
creation / balance / name-enquiry do NOT need it, so funding is buildable now.

Envelopes (two shapes, both handled by ``_ok``):
  * creation/acct-mgt: {message, status(bool), code, statusCode, errors[], data}
  * credit/debit:      {result, errorMessage, errorMessages[], hasError(bool), ...}

MOCK mode when unconfigured; fails closed in production (providers.mock_disabled_in_prod)
so a misconfigured deploy never fabricates an account/credit. WEMA_SIMULATION=true
serves the mock flow even in production to test a real build without live keys.

VERIFY-BEFORE-LIVE: paths/fields follow the ALAT OpenAPI specs but were not
exercised against a live gateway — confirm each (esp. securityInfo, the live host,
the tx-status code legend, and inbound-credit detection) before go-live.
"""
import hashlib
import logging
import secrets
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings

from .providers import mock_disabled_in_prod

REQUEST_TIMEOUT = 30
log = logging.getLogger("zitch")

# Per-product base path under settings.WEMA["BASE_URL"].
_PATH = {
    "wallet_nin": "/wallet-creation",       # create wallet with NIN (OTP)
    "wallet_bvn": "/account-creation",       # create wallet with BVN (OTP)
    "acct_mgt": "/ws-acct-mgt",              # balance + transaction history
    "credit": "/credit-wallet",              # fund a wallet from the channel account
    "debit": "/debit-wallet",                # payout / name enquiry / banks
}
# Products whose channel-id header is `access` (not `x-api-key`).
_ACCESS_PRODUCTS = {"credit", "debit"}


def wema_live() -> bool:
    """Whether Wema has the channel id + the Wallet-Services subscription key."""
    m = settings.WEMA
    return bool(m.get("CHANNEL_ID") and (m.get("KEYS") or {}).get("wallet"))


def wema_simulation() -> bool:
    """WEMA_SIMULATION — serve the mock flow even in production (no real money)."""
    return bool(settings.WEMA.get("SIMULATION"))


def _mock_blocked() -> bool:
    return mock_disabled_in_prod() and not wema_simulation()


def _sub_key(product: str) -> str:
    keys = settings.WEMA.get("KEYS") or {}
    # Wallet Services subscription covers wallet-creation, acct-mgt, credit & debit.
    if product in ("wallet_nin", "wallet_bvn", "acct_mgt", "credit", "debit"):
        return keys.get("wallet", "")
    return keys.get(product, "")


def _headers(product: str) -> dict:
    channel = settings.WEMA.get("CHANNEL_ID", "")
    channel_header = "access" if product in _ACCESS_PRODUCTS else "x-api-key"
    return {
        "Ocp-Apim-Subscription-Key": _sub_key(product),
        channel_header: channel,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _url(product: str, path: str) -> str:
    base = settings.WEMA["BASE_URL"].rstrip("/")
    return f"{base}{_PATH[product]}{path}"


def _ok(data: dict) -> bool:
    """Success across both ALAT envelope shapes."""
    if not isinstance(data, dict):
        return False
    if data.get("status") is True:               # creation / acct-mgt envelope
        return True
    if "hasError" in data:                        # credit / debit envelope
        return not data.get("hasError")
    return False


def _msg(data: dict) -> str:
    return (data.get("message") or data.get("errorMessage")
            or (data.get("errorMessages") or [""])[0] or "Request failed")


def _naira(v) -> Decimal | None:
    try:
        return Decimal(str(v)).quantize(Decimal("0.01"))
    except (TypeError, ValueError, InvalidOperation):
        return None


def _get(product: str, path: str, params: dict | None = None) -> requests.Response:
    return requests.get(_url(product, path), params=params or {},
                        headers=_headers(product), timeout=REQUEST_TIMEOUT)


def _post(product: str, path: str, body: dict) -> requests.Response:
    return requests.post(_url(product, path), json=body,
                         headers=_headers(product), timeout=REQUEST_TIMEOUT)


def _unreachable(exc: Exception) -> dict:
    return {"success": False, "message": f"Bank gateway unreachable: {exc}"}


def _security_info(**kwargs) -> str:
    """Build the encrypted ``securityInfo`` Wema requires on money-movement calls.

    NOT documented in the OpenAPI — construction (algorithm, what is encrypted,
    key/certificate) must come from Wema's integration guide. This is the single
    place to implement it; until then it returns "" and logs a warning so a live
    money call fails loudly rather than silently sending an unsigned payload.
    """
    conf = settings.WEMA.get("SECURITY_INFO", "")
    if conf:
        return conf  # a static prebuilt value, if Wema issues one
    if wema_live():
        log.warning("wema_security_info_unset — money-movement calls will be rejected until "
                    "the securityInfo scheme is configured")
    return ""


# ---------------------------------------------------------------------------
# Wallet creation (dedicated funding account) — BVN/NIN + OTP flow
# ---------------------------------------------------------------------------
def _mock_account(reference: str, name: str) -> dict:
    seed = int(hashlib.sha256(reference.encode()).hexdigest(), 16)
    return {"success": True, "mock": True,
            "account_number": "01" + f"{seed % 10**8:08d}",
            "account_name": name or "ADEYEMI WILLIAM", "bank_name": "Wema Bank (demo)",
            "reference": reference}


def create_wallet_request(phone: str, email: str, *, bvn: str = "", nin: str = "") -> dict:
    """Step 1 — request wallet creation; Wema sends an OTP to the customer's phone.

    Returns {success, tracking_id, otp_destination, message}. Use BVN or NIN.
    """
    if not wema_live():
        if _mock_blocked():
            return {"success": False, "message": "Bank account creation is not configured"}
        return {"success": True, "mock": True, "tracking_id": "WEMA-SIM-" + secrets.token_hex(6),
                "otp_destination": phone, "message": "OTP sent (demo)"}
    try:
        if bvn:
            resp = _post("wallet_bvn", "/api/CustomerAccount/PostPartnershipAccountCreationWithBvn",
                         {"phoneNumber": phone, "email": email, "bvn": bvn})
        else:
            resp = _post("wallet_nin", "/api/CustomerAccount/GenerateWalletAccountForPartnerships/Request",
                         {"phoneNumber": phone, "email": email, "nin": nin})
        data = resp.json()
        d = data.get("data", {}) or {}
        return {"success": _ok(data),
                "tracking_id": d.get("trackingId") or d.get("otpTrackingID", ""),
                "otp_destination": d.get("otpDestination", phone),
                "message": _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def validate_wallet_otp(phone: str, otp: str, tracking_id: str, *, bvn: bool = False) -> dict:
    """Step 2 — validate the OTP and enqueue account creation."""
    if not wema_live():
        if _mock_blocked():
            return {"success": False, "message": "Bank account creation is not configured"}
        return {"success": True, "mock": True, "message": "OTP validated (demo)"}
    try:
        path = ("/api/CustomerAccount/ValidateBVNandEnqueueAccountCreation" if bvn
                else "/api/CustomerAccount/GenerateWalletAccountForPartnershipsV2/Otp")
        product = "wallet_bvn" if bvn else "wallet_nin"
        data = _post(product, path, {"phoneNumber": phone, "otp": otp, "trackingId": tracking_id}).json()
        return {"success": _ok(data), "message": _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def resend_wallet_otp(phone: str, tracking_id: str, *, bvn: bool = False) -> dict:
    if not wema_live():
        return {"success": not _mock_blocked(), "mock": True, "message": "OTP resent (demo)"}
    try:
        product = "wallet_bvn" if bvn else "wallet_nin"
        data = _post(product, "/api/CustomerAccount/ResendOtpRequest/ResendOtp",
                     {"trackingId": tracking_id, "phoneNumber": phone}).json()
        return {"success": _ok(data), "message": _msg(data)}
    except requests.RequestException as exc:
        return _unreachable(exc)


def get_account_details(phone: str, *, bvn: bool = False) -> dict:
    """Step 3 — fetch the created account (poll until accountNumber is populated)."""
    if not wema_live():
        if _mock_blocked():
            return {"success": False, "message": "Bank account creation is not configured"}
        return _mock_account(f"phone:{phone}", "")
    try:
        product = "wallet_bvn" if bvn else "wallet_nin"
        data = _get(product, "/api/CustomerAccount/GetPartnershipAccountDetails",
                    {"phoneNumber": phone}).json()
        d = data.get("data", {}) or {}
        num = d.get("accountNumber", "")
        name = " ".join(x for x in (d.get("firstName", ""), d.get("lastName", "")) if x).strip()
        return {"success": _ok(data) and bool(num), "account_number": num,
                "account_name": name, "bank_name": "Wema Bank",
                "email": d.get("email", ""), "message": _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


# ---------------------------------------------------------------------------
# Account maintenance — balance + history (credit detection is by polling)
# ---------------------------------------------------------------------------
def get_balance(account_number: str) -> dict:
    if not wema_live():
        if _mock_blocked():
            return {"success": False, "message": "Account services are not configured"}
        return {"success": True, "mock": True, "balance_naira": Decimal("0.00")}
    try:
        data = _get("acct_mgt",
                    f"/api/AccountMaintenance/CustomerAccount/GetAccountV2/accountNumber/{account_number}").json()
        r = data.get("result", {}) or {}
        return {"success": _ok(data), "balance_naira": _naira(r.get("availableBalance")),
                "wallet_status": r.get("walletStatus", ""), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def get_transactions(account_number: str, date_from: str, date_to: str, keyword: str = "") -> dict:
    """Transaction history — the source for detecting inbound credits (creditType=='Credit')."""
    if not wema_live():
        if _mock_blocked():
            return {"success": False, "message": "Account services are not configured"}
        return {"success": True, "mock": True, "transactions": []}
    try:
        data = _post("acct_mgt", "/api/AccountMaintenance/CustomerAccount/transhistoryV2",
                     {"accountNumber": account_number, "from": date_from, "to": date_to,
                      "keyWord": keyword}).json()
        # This envelope uses {successful, result[], message} rather than status/hasError.
        ok = bool(data.get("successful")) or _ok(data)
        return {"success": ok, "transactions": data.get("result", []) or [], "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def normalize_transaction(tx: dict) -> dict:
    """Flatten one ALAT TransactionHistoryModel row to the fields reconciliation
    needs: {reference, amount_naira, is_credit, narration, sender}.

    `referenceId` (fallback `tranId`) is the unique per-transaction key used as
    the ledger idempotency guard; `creditType == "Credit"` marks an inbound
    deposit (the only rows funding applies)."""
    if not isinstance(tx, dict):
        return {"reference": "", "amount_naira": None, "is_credit": False, "narration": "", "sender": ""}
    ref = str(tx.get("referenceId") or tx.get("tranId") or "").strip()
    is_credit = str(tx.get("creditType") or "").strip().lower() == "credit"
    return {"reference": ref, "amount_naira": _naira(tx.get("amount")),
            "is_credit": is_credit, "narration": tx.get("narration") or "",
            "sender": tx.get("sender") or tx.get("senderAccountNumber") or ""}


# ---------------------------------------------------------------------------
# Payout — bank list, name enquiry, transfer, status
# ---------------------------------------------------------------------------
def get_banks() -> dict:
    if not wema_live():
        if _mock_blocked():
            return {"success": False, "message": "Transfers are not configured"}
        return {"success": True, "mock": True,
                "banks": [{"bank_name": "Wema Bank", "bank_code": "035"}]}
    try:
        data = _get("debit", "/api/Shared/GetAllBanks").json()
        raw = data.get("result", []) or []
        rows = raw if isinstance(raw, list) else [raw]
        banks = [{"bank_name": b.get("bankName", ""), "bank_code": b.get("bankCode", "")}
                 for b in rows if b.get("bankCode")]
        return {"success": _ok(data), "banks": banks, "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def resolve_account(account_number: str, bank_code: str) -> dict:
    """Name enquiry — (account number, bank code) -> holder name."""
    if not wema_live():
        if _mock_blocked():
            return {"success": False, "message": "Name enquiry is not configured"}
        return {"success": True, "mock": True, "name": "ADEYEMI WILLIAM"}
    try:
        data = _get("debit", f"/api/Shared/AccountNameEnquiry/{bank_code}/{account_number}").json()
        r = data.get("result", {}) or {}
        name = r.get("accountName", "")
        return {"success": _ok(data) and bool(name), "name": name,
                "bank_code": r.get("bankCode", bank_code), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def _parse_transfer(data: dict, reference: str) -> dict:
    r = data.get("result", {}) or {}
    return {"success": _ok(data), "status": (r.get("status") or "").upper(),
            "reference": r.get("transactionReference", reference),
            "platform_reference": r.get("platformTransactionReference", ""),
            "message": r.get("message") or _msg(data), "raw": data}


def transfer(amount_naira, reference: str, narration: str, *, source_account: str,
             destination_account: str, destination_bank_code: str, destination_bank_name: str,
             destination_name: str) -> dict:
    """ProcessClientTransfer — debit source wallet, credit destination (intra/inter bank).

    Requires the encrypted ``securityInfo`` (see _security_info). ``reference`` is
    our idempotency key; poll confirm_transfer_status(reference) for terminal state.
    """
    if not wema_live():
        if _mock_blocked():
            return {"success": False, "message": "Transfers are not configured"}
        return {"success": True, "mock": True, "status": "SUCCESS", "reference": reference,
                "platform_reference": "WEMA-SIM-" + secrets.token_hex(6)}
    try:
        body = {
            "securityInfo": _security_info(op="transfer", reference=reference, amount=amount_naira),
            "amount": float(amount_naira),
            "destinationBankCode": destination_bank_code,
            "destinationBankName": destination_bank_name,
            "destinationAccountNumber": destination_account,
            "destinationAccountName": destination_name,
            "sourceAccountNumber": source_account,
            "narration": narration,
            "transactionReference": reference,
            "useCustomNarration": bool(narration),
        }
        data = _post("debit", "/api/Shared/ProcessClientTransfer", body).json()
        out = _parse_transfer(data, reference)
        if not out["success"]:
            log.warning("wema_transfer_failed ref=%s msg=%s", reference, out.get("message"))
        return out
    except requests.RequestException as exc:
        return _unreachable(exc)


def confirm_transfer_status(reference: str) -> dict:
    """Poll terminal status of a transfer by our transactionReference (no webhook)."""
    if not wema_live():
        return {"success": not _mock_blocked(), "mock": True, "status": "SUCCESS", "reference": reference}
    try:
        data = _get("debit", f"/api/IntraBankTransfer/ConfirmClientTransferStatus/{reference}").json()
        r = (data.get("result", {}) or {}).get("data", {}) or {}
        return {"success": _ok(data), "status": (r.get("status") or "").upper(),
                "reference": r.get("transactionReference", reference),
                "platform_reference": r.get("platformTransactionReference", ""), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def credit_wallet(amount_naira, reference: str, narration: str, *, destination_account: str) -> dict:
    """FundWallet — push a credit into a wallet from the channel funding account.

    Requires ``securityInfo``. Used to credit a user's wallet from our settlement
    balance (NOT for detecting third-party deposits — that's polling)."""
    if not wema_live():
        if _mock_blocked():
            return {"success": False, "message": "Wallet crediting is not configured"}
        return {"success": True, "mock": True, "status": "SUCCESS", "reference": reference}
    try:
        body = {
            "securityInfo": _security_info(op="credit", reference=reference, amount=amount_naira),
            "destinationAccountNumber": destination_account,
            "amount": float(amount_naira),
            "narration": narration,
            "transactionReference": reference,
            "useCustomNarration": bool(narration),
        }
        data = _post("credit", "/api/IntraBankTransfer/FundWallet", body).json()
        return _parse_transfer(data, reference)
    except requests.RequestException as exc:
        return _unreachable(exc)


# ---------------------------------------------------------------------------
# Diagnostics — mirrors kora/mono/monnify diagnostics
# ---------------------------------------------------------------------------
def wema_diagnostics() -> dict:
    m = settings.WEMA
    keys = m.get("KEYS") or {}
    out = {"base_url": m["BASE_URL"], "channel_id_set": bool(m.get("CHANNEL_ID")),
           "wallet_key_set": bool(keys.get("wallet")), "security_info_set": bool(m.get("SECURITY_INFO")),
           "wema_live": wema_live(), "simulation": wema_simulation()}
    if not wema_live():
        out["status"] = "simulation" if wema_simulation() else "keys_incomplete"
        out["hint"] = ("Set WEMA_CHANNEL_ID + WEMA_WALLET_KEY (and the per-product keys), the live "
                       "WEMA_BASE_URL, and the securityInfo scheme. WEMA_SIMULATION=true tests the flow "
                       "without live keys.")
        return out
    out["status"] = "configured" if m.get("SECURITY_INFO") else "security_info_missing"
    out["hint"] = ("Keys present. Confirm the securityInfo construction, live host, and tx-status legend "
                   "against Wema's integration guide before go-live.")
    return out
