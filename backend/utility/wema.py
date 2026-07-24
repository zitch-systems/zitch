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

RECONCILED against the full ALAT OpenAPI spec set (see docs/wema-migration.md §Spec
reconciliation). Funding rails — wallet-creation, balance/history, payout/transfer,
credit-wallet, airtime/data, bills — have CONFIRMED-correct paths, fields, auth and
envelopes. Still open before go-live:
  * securityInfo construction (algorithm/plaintext) — provisioned out-of-band by Wema.
  * tx-status legends — payout status strings, and the VAS/bills CheckTransactionStatus
    INTEGER enums, are undocumented in the specs; get the code→meaning map from Wema.
  * Card rail — the /api/VirtualCard/* paths are WRONG (see the card section) and need
    the real /card-management endpoints + a NUBAN-keyed model.
  * KYC rail — Wema has no standalone BVN/NIN/vNIN lookup (see the KYC section); route
    those to Prembly or fold into account-creation.
  * live host (WEMA_BASE_URL) + production keys.
"""
import hashlib
import logging
import re
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
    "airtime": "/airtime-data",              # airtime + data (VAS)
    "bills": "/bills-payment",               # bills payment (VAS)
    "kyc": "/kyc",                           # Full KYC / Face: BVN / NIN / vNIN lookups
    "card": "/virtual-card",                 # Virtual Naira Card (issue/freeze/fund/reveal)
}
# Products whose channel-id header is `access` (not `x-api-key`).
_ACCESS_PRODUCTS = {"credit", "debit", "airtime", "bills", "card"}


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
    """Parse an ALAT money value to Decimal, tolerating thousands separators and a
    currency symbol/code (history amounts can arrive as "1,000.00" or "₦1,000").
    Returns None only when genuinely unparseable — callers must treat None as
    'skip', never as zero."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("₦", "").replace("NGN", "").strip()
    if not s:
        return None
    try:
        return Decimal(s).quantize(Decimal("0.01"))
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
        # The documented ResponseModel has no `data` envelope, but the live gateway
        # returns the OTP tracking id (schemas B2BOTPResponseModel/B2BOnboardingResponse)
        # — look for it at the top level and under data/result so we don't depend on
        # one undocumented shape. An empty tracking_id would break OTP validation.
        d = data.get("data") or data.get("result") or {}
        if not isinstance(d, dict):
            d = {}
        tracking = (d.get("trackingId") or d.get("otpTrackingID")
                    or data.get("trackingId") or data.get("otpTrackingID") or "")
        dest = d.get("otpDestination") or data.get("otpDestination") or phone
        return {"success": _ok(data), "tracking_id": tracking,
                "otp_destination": dest, "message": _msg(data), "raw": data}
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
        resp = _post(product, "/api/CustomerAccount/ResendOtpRequest/ResendOtp",
                     {"trackingId": tracking_id, "phoneNumber": phone})
        # The spec documents ResendOtp as 200 No-Content: a bare .json() on an empty
        # body raises ValueError (not a RequestException) and would crash a genuine
        # success. Treat any 2xx with an empty/non-JSON body as resent.
        if resp.status_code < 300 and not (resp.content or b"").strip():
            return {"success": True, "message": "OTP resent"}
        try:
            data = resp.json()
        except ValueError:
            return {"success": resp.status_code < 300, "message": "OTP resent"}
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
        # GetAccountV2 uses the account-maintenance envelope {result, successful,
        # message} — no status/hasError — so _ok() alone would report every valid
        # read as a failure (same envelope handled in get_transactions).
        ok = bool(data.get("successful")) or _ok(data)
        return {"success": ok, "balance_naira": _naira(r.get("availableBalance")),
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
    the ledger idempotency guard; a row funds the wallet only when
    `creditType == "Credit"` AND its status is settled (not Pending/Failed)."""
    if not isinstance(tx, dict):
        return {"reference": "", "amount_naira": None, "is_credit": False, "narration": "", "sender": ""}
    ref = str(tx.get("referenceId") or tx.get("tranId") or "").strip()
    # ALAT TransactionStatus enum is {Default, Successfull(sic), Failed, Pending}
    # (confirmed against wallet-services-account-maintenance-api). Only a SETTLED
    # credit is fundable: block clearly-non-final rows (Pending/Failed) so a deposit
    # still in flight — or one that later bounces — is never credited. Unknown /
    # blank / Successfull / Default still count, so a live gateway that omits or
    # re-spells the field can't strand real money; a Pending row simply credits on
    # a later sweep once it settles.
    status = str(tx.get("status") or "").strip().lower()
    settled = status not in ("pending", "failed")
    is_credit = settled and str(tx.get("creditType") or "").strip().lower() == "credit"
    return {"reference": ref, "amount_naira": _naira(tx.get("amount")),
            "is_credit": is_credit, "status": status,
            "narration": tx.get("narration") or "",
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
# VAS — airtime / data / bills (opt-in; VTU.ng stays the default)
#
# The Client (single-account) variants debit the user's own NUBAN
# (accountNumber / customerAccount) — matching the per-user-balance model — so
# `source_account` is the sender's wallet.account_number (falls back to the pool
# WEMA_SOURCE_ACCOUNT). Money-movement calls carry securityInfo (nullable in
# sandbox). Purchases mirror the VTU contract: success => delivered; a network
# error returns pending=True so the caller never refunds a maybe-delivered buy.
# Data/bills need Wema's own packageCode/packageId catalog (differs from our
# stored VTU.ng codes) — see docs/wema-migration.md.
# ---------------------------------------------------------------------------
def _vas_live(product: str) -> bool:
    """Whether the VAS product (airtime/bills) has its subscription key + channel."""
    return bool(settings.WEMA.get("CHANNEL_ID") and _sub_key(product))


def _vas_source() -> str:
    return settings.WEMA.get("SOURCE_ACCOUNT", "")


def purchase_airtime(amount_naira, reference: str, phone: str, network: str, *,
                     source_account: str = "") -> dict:
    """Airtime purchase debiting the user's NUBAN (Client single-account variant)."""
    if not _vas_live("airtime"):
        if _mock_blocked():
            return {"success": False, "message": "Airtime is not configured"}
        return {"success": True, "mock": True, "status": "SUCCESS", "reference": reference}
    src = source_account or _vas_source()
    if not src:
        return {"success": False, "message": "Airtime is temporarily unavailable"}
    try:
        body = {"transactionReference": reference, "accountNumber": src, "network": network,
                "phoneNumber": phone, "amount": float(amount_naira),
                "securityInfo": _security_info(op="airtime", reference=reference, amount=amount_naira),
                "clientId": settings.WEMA.get("CHANNEL_ID", "")}
        data = _post("airtime", "/api/Airtime/Client/PurchaseAirtime", body).json()
        return _parse_vas(data, reference)
    except requests.RequestException as exc:
        return {"success": False, "pending": True, "message": f"Bank gateway unreachable: {exc}"}


def get_data_plans(network: str = "") -> dict:
    """Wema's own data-plan catalog (packageCode differs from our stored codes)."""
    if not _vas_live("airtime"):
        if _mock_blocked():
            return {"success": False, "message": "Data is not configured"}
        return {"success": True, "mock": True, "plans": []}
    try:
        data = _get("airtime", "/api/Data/GetDataPlans", {"network": network} if network else None).json()
        raw = data.get("result", []) or []
        return {"success": _ok(data) or bool(data.get("successful")),
                "plans": raw if isinstance(raw, list) else [raw], "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def purchase_data(amount_naira, reference: str, phone: str, network: str, package_code: str, *,
                  source_account: str = "") -> dict:
    """Data purchase (Client single-account). `package_code` is Wema's plan code."""
    if not _vas_live("airtime"):
        if _mock_blocked():
            return {"success": False, "message": "Data is not configured"}
        return {"success": True, "mock": True, "status": "SUCCESS", "reference": reference}
    src = source_account or _vas_source()
    if not src:
        return {"success": False, "message": "Data is temporarily unavailable"}
    try:
        body = {"transactionReference": reference, "accountNumber": src, "phoneNumber": phone,
                "packageCode": package_code, "amount": float(amount_naira), "network": network,
                "securityInfo": _security_info(op="data", reference=reference, amount=amount_naira),
                "clientId": settings.WEMA.get("CHANNEL_ID", "")}
        data = _post("airtime", "/api/Data/Client/PurchaseData", body).json()
        return _parse_vas(data, reference)
    except requests.RequestException as exc:
        return {"success": False, "pending": True, "message": f"Bank gateway unreachable: {exc}"}


def get_bills() -> dict:
    """Wema biller catalog (packageId differs from our VTU.ng service ids)."""
    if not _vas_live("bills"):
        if _mock_blocked():
            return {"success": False, "message": "Bills are not configured"}
        return {"success": True, "mock": True, "bills": []}
    try:
        data = _get("bills", "/api/BillsPayment/GetAllBills").json()
        raw = data.get("result", []) or []
        return {"success": _ok(data) or bool(data.get("successful")),
                "bills": raw if isinstance(raw, list) else [raw], "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def validate_bill_customer(identifier: str, package_id: str) -> dict:
    """Validate a bill customer identifier (meter/smartcard) -> customer name."""
    if not _vas_live("bills"):
        if _mock_blocked():
            return {"success": False, "message": "Bills are not configured"}
        return {"success": True, "mock": True, "name": "ADEYEMI WILLIAM"}
    try:
        body = {"channelId": settings.WEMA.get("CHANNEL_ID", ""), "identifier": identifier,
                "packageId": package_id}
        data = _post("bills", "/api/BillsPayment/ValidateCustomer", body).json()
        r = data.get("result", {}) or {}
        return {"success": _ok(data) or bool(data.get("successful")),
                "name": r.get("customerName") or r.get("name", ""), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def pay_bill(amount_naira, reference: str, *, package_id: str, identifier: str, source_account: str = "",
             email: str = "", phone: str = "", name: str = "", charge=0) -> dict:
    """Pay a bill debiting the user's NUBAN (Client PayBill variant)."""
    if not _vas_live("bills"):
        if _mock_blocked():
            return {"success": False, "message": "Bills are not configured"}
        return {"success": True, "mock": True, "status": "SUCCESS", "reference": reference}
    src = source_account or _vas_source()
    if not src:
        return {"success": False, "message": "Bill payment is temporarily unavailable"}
    try:
        body = {"clientId": settings.WEMA.get("CHANNEL_ID", ""), "customerAccount": src,
                "amount": float(amount_naira), "charge": float(charge),
                "transactionReference": reference, "packageId": package_id,
                "customerIdentifier": identifier, "customerEmail": email,
                "customerPhoneNumber": phone, "customerName": name,
                "securityInfo": _security_info(op="bill", reference=reference, amount=amount_naira)}
        data = _post("bills", "/api/Shared/PayBill", body).json()
        return _parse_vas(data, reference)
    except requests.RequestException as exc:
        return {"success": False, "pending": True, "message": f"Bank gateway unreachable: {exc}"}


def vas_status(reference: str, txn_type: str = "") -> dict:
    """Requery a VAS purchase by our transactionReference (settle/refund helper)."""
    product = "bills" if txn_type == "bill" else "airtime"
    if not _vas_live(product):
        return {"success": not _mock_blocked(), "mock": True, "status": "SUCCESS", "reference": reference}
    try:
        path = ("/api/PartnerPayment/checktransactionstatus" if product == "bills"
                else "/api/PartnerPayment/CheckTransactionStatus")
        body = {"transactionReference": reference}
        if product != "bills":
            body["transactionType"] = txn_type or "airtime"
        data = _post(product, path, body).json()
        return _parse_vas(data, reference)
    except requests.RequestException as exc:
        return {"success": False, "pending": True, "message": f"Bank gateway unreachable: {exc}"}


def _parse_vas(data: dict, reference: str) -> dict:
    """Normalise a VAS response to the {success, pending, status, reference} shape
    settle_or_refund expects. A recognisably-processing status maps to pending."""
    r = data.get("result", {}) or {}
    if not isinstance(r, dict):
        r = {}
    status = str(r.get("status") or data.get("status") or "").upper()
    ok = _ok(data) or bool(data.get("successful"))
    pending = status in ("PENDING", "PROCESSING", "IN_PROGRESS", "INPROGRESS")
    return {"success": ok and not pending, "pending": pending, "status": status,
            "reference": r.get("transactionReference", reference),
            "message": r.get("message") or _msg(data), "raw": data}


# ---------------------------------------------------------------------------
# Virtual Naira Card (Wema card product): issue / freeze / fund / reveal
#
# Returns the same shapes the cards app + providers card_* wrappers expect
# (issue -> {card_token, brand, last4, expiry}; reveal -> {pan, cvv}). Mock-first
# with a deterministic fake card offline; fails closed in production when unkeyed so
# a misconfigured deploy never fabricates a card that looks real in the app.
#
# FIX-BEFORE-LIVE (confirmed against card-management-api spec — the paths below are
# WRONG and will 404 on the live gateway). The real Card Management API is:
#   • APIM suffix  /card-management   (NOT the current _PATH['card']='/virtual-card')
#   • auth header  x-api-key          (NOT 'access'; remove 'card' from _ACCESS_PRODUCTS)
#   • NO securityInfo on any card call (remove it from the bodies below)
#   • operations are keyed by the customer NUBAN (accountNo), not a card token:
#       issue   POST /api/Partner/partnerCard/virtualCard
#               body VirtualCardRequestObject {accountNo,emailaddress,phoneNumber,
#               amount,customerAddress,cardKey,currency:'NGN'}
#       reveal  GET  /api/Partner/partnerCard/retrieveCard/{accountNo}   (full PAN/CVV)
#               or   /api/Partner/partnerCard/virtual-card-details/{accountNo}
#       freeze  POST /api/Partner/partnerCard/hotlistCard?maskedPan=&accountNumber=
#               (query params, block-only — the rail has NO unfreeze endpoint)
#       fund    — none; funding is only the optional `amount` at creation
# Migrating requires threading the cardholder's NUBAN/email/phone into these calls
# (a card-model change) + a live-key smoke test, so it is intentionally NOT done here.
# The rail is gated off (_card_live() False until keyed) so this blocks virtual cards
# only, not the core funding/transfer/VAS launch.
# ---------------------------------------------------------------------------
def _card_live() -> bool:
    """Whether the Virtual Naira Card product has its subscription key + channel id."""
    return bool(settings.WEMA.get("CHANNEL_ID") and _sub_key("card"))


def _card_result(data: dict) -> dict:
    d = data.get("result") or data.get("data") or {}
    if not isinstance(d, dict):
        d = {}
    return d


def card_issue(holder: str, customer_ref: str) -> dict:
    """Create a virtual Naira card. Returns {success, card_token, brand, last4, expiry}."""
    if not _card_live():
        if _mock_blocked():
            return {"success": False, "message": "Card issuing is not configured"}
        seed = int(hashlib.sha256((customer_ref or holder or "x").encode()).hexdigest(), 16)
        return {"success": True, "mock": True, "card_token": "wema_" + f"{seed % 10**12:012d}",
                "brand": "Verve", "last4": f"{seed % 10000:04d}",
                "expiry": f"{1 + seed % 12:02d}/{29 + seed % 3}"}
    try:
        body = {"customerReference": customer_ref, "name": holder, "currency": "NGN",
                "securityInfo": _security_info(op="card_issue", reference=customer_ref)}
        data = _post("card", "/api/VirtualCard/CreateCard", body).json()
        r = _card_result(data)
        token = r.get("cardId") or r.get("id") or r.get("cardReference") or ""
        pan = str(r.get("maskedPan") or r.get("cardNumber") or "")
        return {"success": _ok(data) and bool(token), "card_token": token,
                "brand": r.get("scheme", "Verve"), "last4": pan[-4:],
                "expiry": r.get("expiry") or f"{r.get('expiryMonth', '')}/{str(r.get('expiryYear', ''))[-2:]}",
                "message": _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def card_set_status(card_token: str, active: bool) -> dict:
    """Freeze/unfreeze a virtual card."""
    if not _card_live():
        if _mock_blocked():
            return {"success": False, "message": "Card issuing is not configured"}
        return {"success": True, "mock": True}
    try:
        path = "/api/VirtualCard/Unfreeze" if active else "/api/VirtualCard/Freeze"
        data = _post("card", path, {"cardId": card_token,
                                    "securityInfo": _security_info(op="card_status", reference=card_token)}).json()
        return {"success": _ok(data), "message": _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def card_fund(card_token: str, amount) -> dict:
    """Top up a virtual card from the wallet."""
    if not _card_live():
        if _mock_blocked():
            return {"success": False, "message": "Card issuing is not configured"}
        return {"success": True, "mock": True}
    try:
        body = {"cardId": card_token, "amount": float(amount),
                "securityInfo": _security_info(op="card_fund", reference=card_token)}
        data = _post("card", "/api/VirtualCard/Fund", body).json()
        return {"success": _ok(data), "message": _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def card_reveal(card_token: str) -> dict:
    """Fetch full PAN/CVV for a one-time reveal (never persisted server-side)."""
    if not _card_live():
        if _mock_blocked():
            return {"success": False, "message": "Card issuing is not configured"}
        seed = int(hashlib.sha256(card_token.encode()).hexdigest(), 16)
        pan = "5061" + "".join(str((seed >> (i * 4)) % 10) for i in range(12))
        return {"success": True, "mock": True, "pan": pan, "cvv": f"{seed % 1000:03d}"}
    try:
        data = _get("card", f"/api/VirtualCard/Details/{card_token}").json()
        r = _card_result(data)
        return {"success": _ok(data), "pan": r.get("cardNumber") or r.get("pan", ""),
                "cvv": r.get("cvv") or r.get("cvv2", ""), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


# ---------------------------------------------------------------------------
# KYC — Nigeria identity lookups (Wema Full KYC product): BVN / NIN / vNIN
#
# On a valid lookup Wema returns the holder's record; verify_bvn additionally
# rejects a clear name mismatch when a name is supplied. Identity NEVER mock-passes
# in production (fails closed via mock_disabled_in_prod, even with WEMA_SIMULATION
# on) so a misconfigured deploy can't upgrade a real tier on a fabricated identity.
#
# FIX-BEFORE-LIVE (confirmed against the partnership-account spec family): Wema has
# NO standalone identity-lookup endpoint. The /api/Kyc/VerifyBvn|VerifyNin|VerifyVnin
# paths below are fabricated and will 404 live, and there is NO virtual-NIN (vNIN)
# endpoint anywhere on the Wema rail at all. On Wema, BVN/NIN are validated only as a
# byproduct of account creation (tier1-bvn/nin-withoutOtp-v2) or upgrade
# (partner-account-upgrade-tier2), and current tier is read via
# GET /api/partnership/partner-account-kyc-status — none of which return a holder
# name for a bare-lookup name-match, so _kyc_record/_name_mismatch have no live data
# source here. RESOLUTION (product decision, needs live keys): either (a) route
# BVN/NIN/vNIN identity lookups to Prembly/IdentityPass (which DO expose them — see
# utility.providers), or (b) fold BVN/NIN validation into the account-creation/upgrade
# flow. Left in place (fails closed without keys) pending that decision.
# ---------------------------------------------------------------------------
def _kyc_live() -> bool:
    """Whether the Full KYC product has its subscription key + channel id."""
    return bool(settings.WEMA.get("CHANNEL_ID") and _sub_key("kyc"))


def _name_tokens(name: str) -> set:
    """Significant lowercased word tokens of a holder name (drops 1-char bits),
    for tolerant BVN/NIN name comparison."""
    return {t for t in re.sub(r"[^a-z ]", " ", (name or "").lower()).split() if len(t) > 1}


def _name_mismatch(supplied: str, resolved: str) -> bool:
    """True only when BOTH names are non-empty and share NO tokens — a clear
    mismatch. Tolerant of order / middle names so a legitimate holder is never
    blocked by a formatting difference."""
    a, b = _name_tokens(supplied), _name_tokens(resolved)
    return bool(a and b and not (a & b))


def _kyc_record(data: dict) -> dict:
    """Pull the holder's first/last name out of either ALAT envelope shape."""
    d = data.get("result") or data.get("data") or {}
    if not isinstance(d, dict):
        d = {}
    return {"first_name": d.get("firstName", "") or d.get("firstname", ""),
            "last_name": d.get("lastName", "") or d.get("lastname", ""), "_d": d}


def verify_bvn(bvn: str, name: str = "", date_of_birth: str = "", mobile: str = "") -> dict:
    """Verify a BVN via Wema's Full KYC lookup (POST /kyc /api/Kyc/VerifyBvn {bvn}).

    On a valid lookup Wema returns the holder's record; when ``name`` is supplied we
    reject only a CLEAR mismatch (no shared name tokens), tolerant of order/middle
    names. Fails closed in production without keys."""
    if len(bvn) != 11 or not bvn.isdigit():
        return {"success": False, "message": "BVN must be 11 digits"}
    if not _kyc_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Identity verification is temporarily unavailable"}
        return {"success": True, "mock": True, "first_name": "", "last_name": ""}
    try:
        data = _post("kyc", "/api/Kyc/VerifyBvn", {"bvn": bvn}).json()
        rec = _kyc_record(data)
        ok = _ok(data) and bool(rec["_d"])
        if ok and name and _name_mismatch(name, f"{rec['first_name']} {rec['last_name']}"):
            return {"success": False, "message": "This BVN does not match your name", "raw": data}
        return {"success": ok, "first_name": rec["first_name"], "last_name": rec["last_name"],
                "message": _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def verify_nin(nin: str, name: str = "") -> dict:
    """Verify a NIN via Wema's Full KYC (POST /kyc /api/Kyc/VerifyNin {nin}) -> holder details.

    When ``name`` is supplied we reject only a CLEAR mismatch (no shared name
    tokens), tolerant of order/middle names — mirroring verify_bvn, so a NIN that
    demonstrably belongs to someone else can't lift the requester's KYC tier.
    Fails closed in production without keys."""
    if len(nin) != 11 or not nin.isdigit():
        return {"success": False, "message": "NIN must be 11 digits"}
    if not _kyc_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Identity verification is temporarily unavailable"}
        return {"success": True, "mock": True, "first_name": "", "last_name": ""}
    try:
        data = _post("kyc", "/api/Kyc/VerifyNin", {"nin": nin}).json()
        rec = _kyc_record(data)
        ok = _ok(data) and bool(rec["_d"])
        if ok and name and _name_mismatch(name, f"{rec['first_name']} {rec['last_name']}"):
            return {"success": False, "message": "This NIN does not match your name", "raw": data}
        return {"success": ok, "first_name": rec["first_name"],
                "last_name": rec["last_name"], "message": _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def verify_vnin(vnin: str, name: str = "") -> dict:
    """Verify a Virtual NIN (16-char tokenised NIN) via Wema's Full KYC
    (POST /kyc /api/Kyc/VerifyVnin {vnin}). Rejects a clear name mismatch when a
    name is supplied. Fails closed in production when unkeyed."""
    if not vnin or len(vnin) != 16:
        return {"success": False, "message": "Virtual NIN must be 16 characters"}
    if not _kyc_live():
        if mock_disabled_in_prod():
            return {"success": False, "message": "Identity verification is temporarily unavailable"}
        return {"success": True, "mock": True, "first_name": "", "last_name": ""}
    try:
        data = _post("kyc", "/api/Kyc/VerifyVnin", {"vnin": vnin}).json()
        rec = _kyc_record(data)
        ok = _ok(data) and bool(rec["_d"])
        if ok and name and _name_mismatch(name, f"{rec['first_name']} {rec['last_name']}"):
            return {"success": False, "message": "This NIN does not match your name", "raw": data}
        return {"success": ok, "first_name": rec["first_name"],
                "last_name": rec["last_name"], "message": _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


# ---------------------------------------------------------------------------
# Diagnostics — mirrors mono diagnostics
# ---------------------------------------------------------------------------
def _trim(raw, limit: int = 500):
    """Short, printable form of a provider response for a diagnostic (no secrets —
    Wema responses carry status/messages/holder names, never our keys)."""
    if raw is None:
        return None
    s = raw if isinstance(raw, str) else str(raw)
    return s[:limit]


def wema_probe(account_number: str = "", bank_code: str = "", phone: str = "",
               bvn: str = "", nin: str = "", otp: str = "", tracking_id: str = "") -> dict:
    """Live self-test against the configured Wema gateway (returns NO secrets).

    Runs the real calls a deploy needs, so ops can see exactly what auth /
    connectivity error the sandbox returns — without the app, a NUBAN, or a shell.
    Read-only by default. Two optional provisioning steps let you create a test
    NUBAN end-to-end from the browser:
      * phone + bvn/nin              -> step 1: start creation (sends a real OTP)
      * phone + otp + tracking_id    -> step 2: validate OTP + fetch the NUBAN
    """
    out = {"config": wema_diagnostics()}
    if not (wema_live() or wema_simulation()):
        out["hint"] = ("Wema keys are not fully configured — set WEMA_CHANNEL_ID + "
                       "WEMA_WALLET_KEY (and per-product keys). No live call was made.")
        return out

    # 1) Bank list — the simplest authenticated call (debit product, `access` header).
    banks = get_banks()
    out["banks"] = {"ok": banks.get("success"), "count": len(banks.get("banks", []) or []),
                    "message": banks.get("message", ""), "raw": _trim(banks.get("raw"))}

    # 2) Name enquiry — the read used before every transfer.
    if account_number and bank_code:
        enq = resolve_account(account_number, bank_code)
        out["name_enquiry"] = {"ok": enq.get("success"), "name": enq.get("name", ""),
                               "message": enq.get("message", ""), "raw": _trim(enq.get("raw"))}

    # 3) Data plans — tests the airtime/VAS product subscription (read-only).
    plans = get_data_plans()
    out["airtime_product"] = {"ok": plans.get("success"), "message": plans.get("message", ""),
                              "raw": _trim(plans.get("raw"))}

    using_bvn = bool(bvn)
    # 4a) Provision step 2 — validate OTP + fetch the created NUBAN.
    if phone and otp and tracking_id:
        val = validate_wallet_otp(phone, otp, tracking_id, bvn=using_bvn)
        acct = get_account_details(phone, bvn=using_bvn) if val.get("success") else {}
        out["wallet_verify"] = {"ok": val.get("success") and bool(acct.get("account_number")),
                                "account_number": acct.get("account_number", ""),
                                "account_name": acct.get("account_name", ""),
                                "message": val.get("message", "") or acct.get("message", ""),
                                "raw": _trim(val.get("raw"))}
    # 4b) Provision step 1 — start creation (sends a real OTP).
    elif phone and (bvn or nin):
        cw = create_wallet_request(phone, f"{phone}@zitch.app", bvn=bvn, nin=nin)
        out["wallet_create"] = {"ok": cw.get("success"), "tracking_id": cw.get("tracking_id", ""),
                                "message": cw.get("message", ""), "raw": _trim(cw.get("raw"))}
    return out


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
