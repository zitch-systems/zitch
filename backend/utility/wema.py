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
envelopes. The card + KYC rails were re-pointed to the real endpoints:
  * Card rail — moved to the real /card-management partnerCard endpoints, NUBAN-keyed
    (issue/reveal/block); no reversible freeze or top-up (report unsupported).
  * KYC rail — Wema has no standalone BVN/NIN lookup, so identity is verified by the
    name-matched account-creation flow (see the KYC section).
Still open before go-live:
  * securityInfo construction (algorithm/plaintext) — provisioned out-of-band by Wema.
  * tx-status legends — payout status strings, and the VAS/bills CheckTransactionStatus
    INTEGER enums, are undocumented in the specs; get the code→meaning map from Wema.
  * live host (WEMA_BASE_URL) + production keys.
"""
import hashlib
import json
import logging
import re
import secrets
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings

from .providers import mock_disabled_in_prod

REQUEST_TIMEOUT = 30
log = logging.getLogger("zitch")

# Per-product base path under settings.WEMA["BASE_URL"]. Each ALAT product is a
# distinct Azure APIM path; the mounts below are confirmed against the ALAT OpenAPI
# specs (Wema API bundle).
_PATH = {
    "wallet_nin": "/wallet-creation",        # create wallet with NIN (OTP)
    "wallet_bvn": "/account-creation",       # create wallet with BVN (OTP)
    "acct_mgt": "/ws-acct-mgt",              # balance + transaction history
    "upgrade": "/account-upgrade",           # tier upgrade + KYC/PND status read
    "credit": "/credit-wallet",              # fund a wallet from the channel account
    "debit": "/debit-wallet",                # payout / name enquiry / banks
    "airtime": "/airtime-data",              # airtime + data (VAS)
    "bills": "/bills-payment",               # bills payment (VAS)
    "remita": "/remita-payment",             # Remita RRR bill payment (VAS)
    "bnpl": "/alat-bnpl",                    # Buy-Now-Pay-Later (merchant-auth)
    "pwba": "/pwba-authenticator",           # Pay with Bank Account — ALAT Authenticator direct debit
    "kyc": "/kyc",                           # Nigeria identity lookups (see KYC section)
    "card": "/card-management",              # Virtual Naira Card (partnerCard endpoints)
}
# Products whose channel-id header is `access` (not `x-api-key`).
_ACCESS_PRODUCTS = {"credit", "debit", "airtime", "bills", "remita"}


def wema_live() -> bool:
    """Whether Wema has the channel id + the Wallet-Services subscription key."""
    m = settings.WEMA
    return bool(m.get("CHANNEL_ID") and (m.get("KEYS") or {}).get("wallet"))


def wema_simulation() -> bool:
    """WEMA_SIMULATION — serve the mock flow even in production (no real money)."""
    return bool(settings.WEMA.get("SIMULATION"))


def _mock_blocked() -> bool:
    return mock_disabled_in_prod() and not wema_simulation()


# Products the ALAT Wallet Services subscription authenticates. Its portal listing
# (confirmed 2026-07-27) bundles 13 APIs: Wallet Creation BVN + NIN, Wallet Services
# Account Management, Account Upgrade, Partnership Account KYC / Face Biometric /
# Address Verification, Credit Wallet, Debit Wallet, Airtime and Data, Bills Payment,
# Remita-Payment and Card Management. So none of these needs its own key.
#
# `card` is deliberately EXCLUDED. The Card Management API bundled here is for
# PHYSICAL card requests, while our card rail issues VIRTUAL cards — a separate
# "Virtual Naira Card" product with its own subscription key. Letting `card` borrow
# the wallet key would also silently flip card_provider() to Wema, which supports
# neither reversible freeze nor top-up (the generic CARD_ISSUER supports both), so an
# unkeyed card rail must stay disabled. `bnpl` and `pwba` are likewise their own
# products and stay excluded.
_WALLET_COVERED = ("wallet_nin", "wallet_bvn", "acct_mgt", "upgrade", "credit", "debit",
                   "airtime", "bills", "remita", "kyc")


def _sub_key(product: str) -> str:
    """Subscription key for a product: its own key if one is set, else the wallet key.

    A product-specific key always wins, so if Wema later issues a dedicated
    airtime/bills subscription, setting WEMA_AIRTIME_KEY / WEMA_BILLS_KEY switches
    to it with no code change.
    """
    keys = settings.WEMA.get("KEYS") or {}
    own = keys.get(product, "")
    if own:
        return own
    return keys.get("wallet", "") if product in _WALLET_COVERED else ""


def _bnpl_headers() -> dict:
    """BNPL uses merchant credentials (x-merchant-id + x-merchant-authorization-key)
    alongside its APIM subscription key — NOT the channel id the other products use."""
    m = settings.WEMA
    return {
        "Ocp-Apim-Subscription-Key": (m.get("KEYS") or {}).get("bnpl", ""),
        "x-merchant-id": m.get("BNPL_MERCHANT_ID", ""),
        "x-merchant-authorization-key": m.get("BNPL_AUTH_KEY", ""),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _headers(product: str) -> dict:
    if product == "bnpl":
        return _bnpl_headers()
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


def _post(product: str, path: str, body: dict, params: dict | None = None) -> requests.Response:
    return requests.post(_url(product, path), json=body, params=params or {},
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
        # ALAT can answer status=True with NO data envelope and no tracking id — e.g.
        # for an identity that already belongs to an ALAT customer it replies "Kindly
        # download ALAT and sign in to see Wema Bank account details" and provisions
        # nothing (observed against sandbox 2026-07-27). Reporting that as success
        # sends the client to an OTP screen it can never satisfy: validation needs the
        # tracking id, so the flow dead-ends with no recovery path. Fail closed instead
        # and surface the gateway's own message, which explains what happened.
        if _ok(data) and not tracking:
            return {"success": False, "tracking_id": "", "otp_destination": dest,
                    "message": _msg(data) or ("Bank account creation did not return a "
                                              "tracking reference"), "raw": data}
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


def lift_debit_restriction(account_number: str, *, bvn: bool = False, place: bool = False) -> dict:
    """Lift (or place) the Post-No-Debit hold on a freshly provisioned NUBAN.

    ALAT provisions a new Tier-1 partnership account under a PND (Post-No-Debit)
    restriction: it can RECEIVE funds but cannot be DEBITED until the partner lifts
    the hold. Since the per-user-balance model debits the sender's own NUBAN, a
    payout/VAS would fail until this runs, so it is called once right after the
    account is created. ``bvn`` selects the product the account was created under
    (PartnerDebitRestrictionManagement lives on both wallet-creation products).
    Best-effort: a failure leaves the NUBAN restricted and is retried later."""
    if not wema_live():
        return {"success": not _mock_blocked(), "mock": True}
    try:
        product = "wallet_bvn" if bvn else "wallet_nin"
        data = _post(product, "/api/CustomerAccount/PartnerDebitRestrictionManagement",
                     {"pndType": "PlacePnd" if place else "LiftPnd",
                      "accountNumber": account_number}).json()
        return {"success": _ok(data), "message": _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def get_kyc_status(account_number: str) -> dict:
    """Read a partnership NUBAN's KYC/tier + restriction state from the ALAT
    Account-Upgrade product (partner-account-kyc-status). Read-only — used to
    reconcile the account's real tier / PND state with our records.

    Returns {success, tier, account_status, restriction_status,
    address_verification, name}."""
    if not wema_live():
        if _mock_blocked():
            return {"success": False, "message": "Account services are not configured"}
        return {"success": True, "mock": True, "tier": "", "restriction_status": ""}
    try:
        data = _get("upgrade", "/api/partnership/partner-account-kyc-status",
                    {"accountNumber": account_number}).json()
        d = data.get("data", {}) or {}
        return {"success": _ok(data) and bool(d), "tier": d.get("accountTier", ""),
                "account_status": d.get("accountStatus", ""),
                "restriction_status": d.get("restrictionStatus", ""),
                "address_verification": d.get("addressVerificationStatus", ""),
                "name": d.get("accountName", ""), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def _residential_address(address) -> dict:
    """Map a free-form/partial address into ALAT's residentialAddress object. Accepts a
    string (used as fullAddress) or a dict of the known fields."""
    fields = ("buildingNumber", "apartment", "street", "city", "town", "state", "lga",
              "lcda", "landmark", "additionalInformation", "country", "fullAddress", "postalCode")
    if not isinstance(address, dict):
        return {"fullAddress": str(address or ""), "country": "Nigeria"}
    out = {k: address.get(k, "") for k in fields}
    if not out.get("country"):
        out["country"] = "Nigeria"
    if not out.get("fullAddress"):
        out["fullAddress"] = " ".join(v for v in (address.get("street"), address.get("city"),
                                                  address.get("state")) if v)
    return out


def upgrade_tier2(account_number: str, *, bvn: str = "", nin: str = "", live_image: str = "") -> dict:
    """Upgrade a partnership NUBAN to Tier 2 at the bank (partner-account-upgrade-tier2
    {accountNumber, nin, bvn, liveImageOfFace}).

    Best-effort sync of the account's BANK-side tier (and thus its NUBAN limits) when
    the user completes the matching Zitch KYC step — it does NOT change Zitch's own tier
    policy. Fails soft in production when unkeyed."""
    if not wema_live():
        return {"success": not _mock_blocked(), "mock": True}
    try:
        data = _post("upgrade", "/api/partnership/partner-account-upgrade-tier2",
                     {"accountNumber": account_number, "nin": nin, "bvn": bvn,
                      "liveImageOfFace": live_image}).json()
        return {"success": _ok(data), "message": _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def upgrade_tier3(account_number: str, address) -> dict:
    """Upgrade a partnership NUBAN to Tier 3 at the bank via address verification
    (partner-account-upgrade-tier3 {residentialAddress{...}, accountNumber}). Best-effort
    bank-side sync (see upgrade_tier2)."""
    if not wema_live():
        return {"success": not _mock_blocked(), "mock": True}
    try:
        data = _post("upgrade", "/api/partnership/partner-account-upgrade-tier3",
                     {"accountNumber": account_number,
                      "residentialAddress": _residential_address(address)}).json()
        return {"success": _ok(data), "message": _msg(data), "raw": data}
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


# transhistoryV2 `status` legend (documented in the Account-Maintenance OpenAPI:
# TransactionHistoryModel.status enum = Default | Successfull | Failed | Pending;
# note ALAT's "Successfull" spelling). Only a genuinely SETTLED credit is funding —
# a Failed row never arrived and a Pending one hasn't yet, so both are held back
# from the funding sweep (a Pending deposit is credited on a later run once it
# flips to Successfull, idempotent on referenceId). An absent/Default status
# carries no negative signal, so it stays creditable (never regress a deposit whose
# gateway omits the field).
_TX_UNSETTLED = {"failed", "reversed", "declined", "returned", "cancelled",
                 "pending", "processing", "inprogress", "in_progress"}


def normalize_transaction(tx: dict) -> dict:
    """Flatten one ALAT TransactionHistoryModel row to the fields reconciliation
    needs: {reference, amount_naira, is_credit, settled, status, narration, sender}.

    `referenceId` (fallback `tranId`) is the unique per-transaction key used as
    the ledger idempotency guard; `creditType == "Credit"` marks an inbound
    deposit; `settled` is False for a row the documented `status` marks Failed/
    Pending, so the funding sweep (apply_wema_credit) never credits a deposit that
    hasn't actually landed."""
    if not isinstance(tx, dict):
        return {"reference": "", "amount_naira": None, "is_credit": False,
                "settled": False, "status": "", "narration": "", "sender": ""}
    ref = str(tx.get("referenceId") or tx.get("tranId") or "").strip()
    # ALAT TransactionStatus enum is {Default, Successfull(sic), Failed, Pending}
    # (confirmed against wallet-services-account-maintenance-api). Only a SETTLED
    # credit is fundable, so `settled` blocks clearly-non-final rows; unknown /
    # blank / Successfull / Default still count, so a live gateway that omits or
    # re-spells the field can't strand real money — a Pending row simply credits on
    # a later sweep once it settles. apply_wema_credit gates on `settled`.
    is_credit = str(tx.get("creditType") or "").strip().lower() == "credit"
    status = str(tx.get("status") or "").strip().lower()
    return {"reference": ref, "amount_naira": _naira(tx.get("amount")),
            "is_credit": is_credit, "settled": status not in _TX_UNSETTLED,
            "status": status, "narration": tx.get("narration") or "",
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


def get_nip_charges() -> dict:
    """The NIP transfer fee schedule (GET /debit-wallet/api/Shared/GetNIPCharges).

    Returns {success, charges: [{name, transaction_type, charge, lower, upper}], terms,
    terms_url}. Each band applies a `charge` to amounts in [lower, upper]; `nip_fee_for`
    resolves the fee for a given amount."""
    if not wema_live():
        if _mock_blocked():
            return {"success": False, "message": "Transfers are not configured"}
        return {"success": True, "mock": True, "charges": []}
    try:
        data = _get("debit", "/api/Shared/GetNIPCharges").json()
        r = data.get("result", {}) or {}
        raw = r.get("chargeFees", []) or []
        charges = [{"name": c.get("chargeFeeName", ""), "transaction_type": c.get("transactionType"),
                    "charge": _naira(c.get("charge")), "lower": _naira(c.get("lower")),
                    "upper": _naira(c.get("upper"))}
                   for c in (raw if isinstance(raw, list) else [raw]) if isinstance(c, dict)]
        return {"success": _ok(data), "charges": charges,
                "terms": r.get("termsAndConditions", ""),
                "terms_url": r.get("termsAndConditionsUrl", ""), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def nip_fee_for(amount_naira, charges: list) -> Decimal | None:
    """The NIP fee that applies to `amount_naira` from a `get_nip_charges()` charge list,
    matching the first band whose [lower, upper] contains the amount (upper<=0 means open-
    ended). Returns None when no band matches (caller decides a default)."""
    amt = _naira(amount_naira)
    if amt is None:
        return None
    for c in charges or []:
        lo, up, ch = c.get("lower"), c.get("upper"), c.get("charge")
        if ch is None or lo is None:
            continue
        if amt >= lo and (up is None or up <= 0 or amt <= up):
            return ch
    return None


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


def _flatten_data_plans(result, network: str = "") -> list:
    """Flatten ALAT's GetDataPlans envelope (result[] of {networkProvider,
    dataPackages[]: {id, name, amount, validity_Period, ...}}) into normalised plan
    rows. Each row's ``code`` is the dataPackage ``id`` — the integer packageCode
    PurchaseData expects. Optionally filter to one network (normalised substring)."""
    want = re.sub(r"[^a-z0-9]", "", network.lower()) if network else ""
    rows = []
    for grp in result if isinstance(result, list) else []:
        if not isinstance(grp, dict):
            continue
        prov = str(grp.get("networkProvider") or grp.get("network") or "")
        if want and want not in re.sub(r"[^a-z0-9]", "", prov.lower()):
            continue
        for pkg in grp.get("dataPackages") or grp.get("packages") or []:
            if not isinstance(pkg, dict):
                continue
            rows.append({
                "code": str(pkg.get("id") or pkg.get("packageCode") or ""),
                "name": pkg.get("name") or pkg.get("dataPlan") or "",
                "amount": pkg.get("amount"),
                "network": prov,
                "validity": pkg.get("validity_Period") or pkg.get("validity") or "",
                "description": pkg.get("description") or "",
            })
    return rows


def get_data_plans(network: str = "") -> dict:
    """Wema's own data-plan catalog, flattened to {code, name, amount, network,
    validity, description} rows (``code`` = the dataPackage id PurchaseData wants).

    ALAT returns every network in one call, nested as result[].dataPackages[]
    (no server-side network filter), so `network` filters client-side."""
    if not _vas_live("airtime"):
        if _mock_blocked():
            return {"success": False, "message": "Data is not configured"}
        return {"success": True, "mock": True, "plans": []}
    try:
        data = _get("airtime", "/api/Data/GetDataPlans").json()
        ok = _ok(data) or bool(data.get("successful"))
        return {"success": ok,
                "plans": _flatten_data_plans(data.get("result", []) or [], network),
                "raw": data}
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


def _flatten_bills(result) -> list:
    """Flatten ALAT's GetAllBills envelope (result[] categories -> billers[] ->
    packages[]) into purchasable rows keyed by the PackageViewModel ``id`` — the
    integer packageId ValidateCustomer / PayBill expect. A biller with no packages
    is emitted as its own row (code = biller id) so nothing is dropped."""
    rows = []
    for cat in result if isinstance(result, list) else []:
        if not isinstance(cat, dict):
            continue
        cat_name = cat.get("name") or ""
        for biller in cat.get("billers") or []:
            if not isinstance(biller, dict):
                continue
            base = {"biller": biller.get("name") or "", "biller_id": biller.get("id"),
                    "category": cat_name, "identifier": biller.get("identifier") or "",
                    "requires_validation": bool(biller.get("requiredValidation")),
                    "charge": biller.get("charge")}
            pkgs = biller.get("packages") or []
            if isinstance(pkgs, list) and pkgs:
                for pkg in pkgs:
                    if isinstance(pkg, dict):
                        rows.append({**base, "code": str(pkg.get("id") or ""),
                                     "name": pkg.get("name") or biller.get("name") or "",
                                     "amount": pkg.get("amount")})
            else:
                rows.append({**base, "code": str(biller.get("id") or ""),
                             "name": biller.get("name") or "", "amount": None})
    return rows


def get_bills() -> dict:
    """Wema biller catalog, flattened to {code, name, amount, biller, category,
    identifier, requires_validation, charge} rows (``code`` = the packageId
    ValidateCustomer / PayBill want). ALAT nests it as
    result[] categories -> billers[] -> packages[]."""
    if not _vas_live("bills"):
        if _mock_blocked():
            return {"success": False, "message": "Bills are not configured"}
        return {"success": True, "mock": True, "bills": []}
    try:
        data = _get("bills", "/api/BillsPayment/GetAllBills").json()
        ok = _ok(data) or bool(data.get("successful"))
        return {"success": ok, "bills": _flatten_bills(data.get("result", []) or []), "raw": data}
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
    """Requery a VAS purchase by our transactionReference (settle/refund helper).

    The airtime/data status check takes an INTEGER ``transactionType`` (1 = airtime,
    2 = data); the bills check takes only the reference. Both return an integer
    ``transactionStatus`` whose legend ALAT doesn't publish — see _parse_vas for how
    that is handled money-safely."""
    if txn_type == "remita":
        # ProcessRemitaPayment is synchronous and ALAT exposes NO Remita status
        # endpoint, so a timed-out Remita payment can't be auto-requeried — leave it
        # PENDING for manual reconciliation (never auto-settle/refund, never mis-route
        # to the airtime status endpoint).
        return {"success": False, "pending": True, "status": "REMITA_MANUAL", "reference": reference}
    product = "bills" if txn_type == "bill" else "airtime"
    if not _vas_live(product):
        return {"success": not _mock_blocked(), "mock": True, "status": "SUCCESS", "reference": reference}
    try:
        if product == "bills":
            data = _post("bills", "/api/PartnerPayment/checktransactionstatus",
                         {"transactionReference": reference}).json()
        else:
            data = _post("airtime", "/api/PartnerPayment/CheckTransactionStatus",
                         {"transactionReference": reference,
                          "transactionType": 2 if txn_type == "data" else 1}).json()
        return _parse_vas(data, reference)
    except requests.RequestException as exc:
        return {"success": False, "pending": True, "message": f"Bank gateway unreachable: {exc}"}


def _parse_vas(data: dict, reference: str) -> dict:
    """Normalise a VAS response to the {success, pending, status, reference} shape
    settle_or_refund expects.

    Two response shapes: a purchase carries a STRING ``result.status``
    (SUCCESS/PROCESSING/…), while the PartnerPayment status-check carries only an
    INTEGER ``result.transactionStatus`` (enum 1..11) whose meaning ALAT doesn't
    document. On the integer-only shape we cannot safely decide settled vs failed,
    so we report ``pending`` — never auto-settle (which would strand a failed
    purchase debited) or auto-refund (which would double-spend a delivered one) on
    an un-decodable code — and surface the raw code for review."""
    r = data.get("result", {}) or {}
    if not isinstance(r, dict):
        r = {}
    status = str(r.get("status") or data.get("status") or "").upper()
    if not status and "transactionStatus" in r:
        code = r.get("transactionStatus")
        log.warning("wema_vas_status_code ref=%s transactionStatus=%r (legend undocumented — left pending)",
                    reference, code)
        return {"success": False, "pending": True, "status": f"CODE_{code}",
                "reference": r.get("transactionReference", reference),
                "message": _msg(data), "raw": data}
    ok = _ok(data) or bool(data.get("successful"))
    pending = status in ("PENDING", "PROCESSING", "IN_PROGRESS", "INPROGRESS")
    return {"success": ok and not pending, "pending": pending, "status": status,
            "reference": r.get("transactionReference", reference),
            "message": r.get("message") or _msg(data), "raw": data}


# ---------------------------------------------------------------------------
# Remita — pay a Remita RRR (bill payment) debiting the user's NUBAN.
#
# validate_rrr -> confirm the RRR + amount before paying; pay_remita -> the money call
# (carries securityInfo; mirrors the bill/VAS contract — success => paid; a network
# error returns pending=True so the caller never refunds a maybe-paid bill).
# ---------------------------------------------------------------------------
def validate_rrr(rrr: str) -> dict:
    """Validate a Remita Retrieval Reference (RRR) -> {success, name, amount, message}."""
    if not _vas_live("remita"):
        if _mock_blocked():
            return {"success": False, "message": "Remita is not configured"}
        return {"success": True, "mock": True, "name": "ADEYEMI WILLIAM", "amount": None}
    try:
        channel = settings.WEMA.get("CHANNEL_ID", "")
        data = _get("remita", f"/api/RemitaPayment/ValidateRrr/{rrr}/{channel}").json()
        r = data.get("result") or data.get("data") or {}
        if not isinstance(r, dict):
            r = {}
        return {"success": _ok(data) or bool(data.get("successful") or r.get("isValidated")),
                "name": r.get("name") or r.get("customerName", ""), "amount": _naira(r.get("amount")),
                "message": r.get("message") or _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def pay_remita(amount_naira, reference: str, *, rrr: str, source_account: str = "", charge=0,
               email: str = "", phone: str = "", name: str = "", payer_name: str = "",
               payer_email: str = "", payer_number: str = "", description: str = "") -> dict:
    """Pay a Remita RRR debiting the user's NUBAN (ProcessRemitaPayment)."""
    if not _vas_live("remita"):
        if _mock_blocked():
            return {"success": False, "message": "Remita is not configured"}
        return {"success": True, "mock": True, "status": "SUCCESS", "reference": reference}
    src = source_account or _vas_source()
    if not src:
        return {"success": False, "message": "Remita is temporarily unavailable"}
    try:
        body = {"channelId": settings.WEMA.get("CHANNEL_ID", ""), "customerAccount": src,
                "amount": float(amount_naira), "charge": float(charge),
                "transactionReference": reference, "customerEmail": email,
                "customerPhoneNumber": phone, "customerName": name, "channelType": "API",
                "accountName": name, "rrr": rrr, "payerEmail": payer_email or email,
                "payerName": payer_name or name, "payerNumber": payer_number or phone,
                "description": description or f"Remita {rrr}",
                "securityInfo": _security_info(op="remita", reference=reference, amount=amount_naira)}
        data = _post("remita", "/api/RemitaPayment/ProcessRemitaPayment", body).json()
        return _parse_vas(data, reference)
    except requests.RequestException as exc:
        return {"success": False, "pending": True, "message": f"Bank gateway unreachable: {exc}"}


def remita_receipt(rrr: str) -> dict:
    """Fetch a Remita payment receipt for a paid RRR (PrintRemitaReceipt)."""
    if not _vas_live("remita"):
        return {"success": not _mock_blocked(), "mock": True, "receipt": None}
    try:
        data = _get("remita", f"/api/RemitaPayment/PrintRemitaReceipt/{rrr}").json()
        return {"success": _ok(data) or bool(data.get("successful")),
                "receipt": data.get("result") or data.get("data"), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


# ---------------------------------------------------------------------------
# Buy-Now-Pay-Later (ALAT BNPL) — external credit against the user's NUBAN.
#
# Distinct auth (merchant headers, see _bnpl_headers) and a multi-step flow: offers
# (eligibility) -> consent (accountNumber, amount, tenor) -> accept terms -> poll status
# -> liquidate. This is REAL debt: only the read-only `offers` step is exposed to end
# users today; the commitment steps (consent/accept/liquidate) are built here but gated
# behind a product/compliance decision (see the loans app).
# ---------------------------------------------------------------------------
def _bnpl_live() -> bool:
    m = settings.WEMA
    return bool(m.get("BNPL_MERCHANT_ID") and m.get("BNPL_AUTH_KEY"))


def bnpl_offers() -> dict:
    """Get the BNPL product offers a customer is eligible for (read-only)."""
    if not _bnpl_live():
        if _mock_blocked():
            return {"success": False, "message": "BNPL is not configured"}
        return {"success": True, "mock": True, "offers": []}
    try:
        data = _get("bnpl", "/api/Eligibility/productoffers").json()
        rows = data.get("result") if isinstance(data, dict) else None
        if rows is None:
            rows = data.get("data") if isinstance(data, dict) else None
        if rows is None:
            rows = data if isinstance(data, list) else [data]
        rows = rows if isinstance(rows, list) else [rows]
        ok = _ok(data) if isinstance(data, dict) and ("status" in data or "hasError" in data) else bool(rows)
        return {"success": ok, "offers": rows, "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def bnpl_consent(account_number: str, product_amount, tenor: int, customer_reference: str, *,
                 equity_amount=0) -> dict:
    """Request BNPL consent for a loan against the NUBAN (ConsentRequest)."""
    if not _bnpl_live():
        if _mock_blocked():
            return {"success": False, "message": "BNPL is not configured"}
        return {"success": True, "mock": True, "eligibility_id": "BNPL-SIM-" + secrets.token_hex(6),
                "account_name": "ADEYEMI WILLIAM"}
    try:
        data = _post("bnpl", "/api/Eligibility/ConsentRequest",
                     {"accountNumber": account_number, "productAmount": float(product_amount),
                      "equityAmount": float(equity_amount or 0), "tenor": int(tenor),
                      "customerReference": customer_reference}).json()
        r = data.get("response", {}) or {}
        if not isinstance(r, dict):
            r = {}
        return {"success": bool(data.get("successful")) or _ok(data),
                "eligibility_id": r.get("eligibilityId") or data.get("eligibilityId", ""),
                "account_name": r.get("accountName", ""),
                "message": data.get("message") or _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def bnpl_accept_terms(eligibility_id: str, accepted: bool = True) -> dict:
    """Accept (or decline) the BNPL loan terms (AcceptTerms — a 200 No-Content endpoint)."""
    if not _bnpl_live():
        return {"success": not _mock_blocked(), "mock": True}
    try:
        resp = _post("bnpl", "/api/LoanApplication/AcceptTerms",
                     {"eligibilityId": eligibility_id, "isTermsAccepted": bool(accepted)})
        if resp.status_code < 300 and not (resp.content or b"").strip():
            return {"success": True}
        try:
            data = resp.json()
        except ValueError:
            return {"success": resp.status_code < 300}
        return {"success": bool(data.get("successful")) or _ok(data), "message": _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def bnpl_status(customer_reference: str) -> dict:
    """Poll a BNPL loan application's status by our customer reference."""
    if not _bnpl_live():
        return {"success": not _mock_blocked(), "mock": True, "status": "PENDING"}
    try:
        # ALAT spells the query param "customeReference" (sic).
        data = _get("bnpl", "/api/LoanApplication/loan-application-status",
                    {"customeReference": customer_reference}).json()
        r = data.get("result") or data.get("data") or data
        status = (r.get("status") if isinstance(r, dict) else "") or ""
        return {"success": bool(data.get("successful")) or _ok(data), "status": str(status), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def bnpl_liquidate(customer_reference: str, *, amount=None) -> dict:
    """Liquidate (early-repay) a BNPL loan (loan-liquidation)."""
    if not _bnpl_live():
        return {"success": not _mock_blocked(), "mock": True}
    try:
        body = {"customerReference": customer_reference}
        if amount is not None:
            body["amount"] = float(amount)
        data = _post("bnpl", "/api/LoanApplication/loan-liquidation", body).json()
        return {"success": bool(data.get("successful")) or _ok(data), "message": _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


# ---------------------------------------------------------------------------
# Pay with Bank Account — ALAT Authenticator (/pwba-authenticator).
#
# A single direct debit from a customer's OWN WEMA/ALAT account, authorised by them
# in the ALAT mobile app — a funding rail (pull money from the user's ALAT account
# into their Zitch wallet). Async: initiate -> customer approves in-app -> poll status
# -> credit. No securityInfo; the channel id travels in the body, not a header.
# ---------------------------------------------------------------------------
def _pwba_live() -> bool:
    return bool(settings.WEMA.get("CHANNEL_ID") and _sub_key("pwba"))


def pwba_fund_request(amount_naira, reference: str, *, source_account: str, narration: str = "") -> dict:
    """Initiate a direct debit from the customer's ALAT account (transfer-fund-request).
    The customer then approves it in the ALAT app; poll pwba_status for the outcome."""
    if not _pwba_live():
        if _mock_blocked():
            return {"success": False, "message": "Pay-with-bank is not configured"}
        return {"success": True, "mock": True, "status": "PENDING", "reference": reference}
    try:
        body = {"amount": float(amount_naira), "sourceAccountNumber": source_account,
                "channelId": settings.WEMA.get("CHANNEL_ID", ""),
                "narration": narration or "Zitch wallet funding", "transactionReference": reference}
        data = _post("pwba", "/api/EcommerceTransfer/v2/transfer-fund-request", body).json()
        r = data.get("result", {}) or {}
        if not isinstance(r, dict):
            r = {}
        return {"success": _ok(data), "status": (r.get("status") or "").upper(),
                "platform_reference": r.get("platformTransactionReference", ""),
                "message": r.get("message") or _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def pwba_status(reference: str) -> dict:
    """Check a Pay-with-Bank direct debit's status by our transactionReference.

    Maps to the {success, pending, amount_naira} shape the funding verify path wants:
    SUCCESS/SETTLED => credit; PENDING/PROCESSING => not yet; anything else => not
    successful (no credit)."""
    if not _pwba_live():
        # Mock: settle immediately in dev so the funding flow is testable offline.
        return {"success": not _mock_blocked(), "mock": True,
                "status": "SUCCESS" if not _mock_blocked() else "FAILED"}
    try:
        channel = settings.WEMA.get("CHANNEL_ID", "")
        data = _get("pwba", f"/api/EcommerceTransfer/CheckTransactionStatus/{channel}/{reference}").json()
        r = data.get("result", {}) or {}
        if not isinstance(r, dict):
            r = {}
        status = (r.get("status") or "").upper()
        pending = status in ("PENDING", "PROCESSING", "IN_PROGRESS", "INPROGRESS", "")
        settled = _ok(data) and status in ("SUCCESS", "SUCCESSFUL", "SUCCESSFULL", "COMPLETED", "PAID", "SETTLED")
        return {"success": settled, "pending": pending and not settled, "status": status,
                "platform_reference": r.get("platformTransactionReference", ""),
                "message": r.get("message") or _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


# ---------------------------------------------------------------------------
# Virtual Naira Card — ALAT Card-Management product (/card-management).
#
# ALAT keys a customer's virtual card by their NUBAN (accountNo), NOT a card token,
# so the "card_token" the cards app stores IS the account number. Three operations
# map to real endpoints: issue (partnerCard/virtualCard, funded at creation),
# reveal (partnerCard/virtual-card-details/{accountNo}) and a PERMANENT block
# (partnerCard/hotlistCard). ALAT's virtual-card product exposes NO reversible
# freeze and NO incremental top-up, so card_set_status(active=True) and card_fund
# report "unsupported" (the generic CARD_ISSUER, still the default, supports both) —
# they never fake a success against money/state ALAT can't move.
#
# Mock-first with a deterministic fake card offline; fails closed in production when
# unkeyed so a misconfigured deploy never fabricates a card that looks real.
#
# VERIFY-BEFORE-LIVE: issue/reveal return an OPAQUE `data` field whose structure
# isn't in the OpenAPI (masked PAN / expiry / CVV / card ref) and the virtualCard
# request's `cardKey` (card product id) must come from Wema — confirm both against
# Wema's card integration guide before go-live. (retrieveCard/{accountNo} is an
# alternative reveal endpoint if virtual-card-details doesn't return the full PAN/CVV.)
# ---------------------------------------------------------------------------
def _card_live() -> bool:
    """Whether the Card-Management product has its subscription key + channel id."""
    return bool(settings.WEMA.get("CHANNEL_ID") and _sub_key("card"))


def _card_data(data: dict) -> dict:
    """Best-effort dict view of ALAT's opaque card `data`/`result` field, which the
    OpenAPI types only as a string — it may arrive as a nested object or a JSON
    string. Returns {} when it can't be read as an object."""
    d = data.get("result")
    if d is None:
        d = data.get("data")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except (ValueError, TypeError):
            return {}
    return d if isinstance(d, dict) else {}


def card_issue(holder: str, customer_ref: str, *, account_number: str = "", email: str = "",
               phone: str = "", amount=0, address: str = "") -> dict:
    """Issue a virtual Naira card against the customer's NUBAN.

    Returns {success, card_token, brand, last4, expiry}. ALAT keys the card by the
    account number, so ``card_token`` is the NUBAN (the app reveals/blocks by it). A
    live issue needs the user's provisioned NUBAN; without one it fails closed."""
    if not _card_live():
        if _mock_blocked():
            return {"success": False, "message": "Card issuing is not configured"}
        seed = int(hashlib.sha256((account_number or customer_ref or holder or "x").encode()).hexdigest(), 16)
        return {"success": True, "mock": True,
                "card_token": account_number or ("wema_" + f"{seed % 10**12:012d}"),
                "brand": "Verve", "last4": f"{seed % 10000:04d}",
                "expiry": f"{1 + seed % 12:02d}/{29 + seed % 3}"}
    if not account_number:
        return {"success": False, "message": "Set up your Zitch account before creating a card"}
    try:
        body = {"emailaddress": email, "phoneNumber": phone, "amount": float(amount or 0),
                "accountNo": account_number, "customerAddress": address,
                "cardKey": settings.WEMA.get("CARD_PRODUCT_KEY", ""), "currency": "NGN"}
        data = _post("card", "/api/Partner/partnerCard/virtualCard", body).json()
        ok = bool(data.get("status")) or _ok(data)
        d = _card_data(data)
        pan = str(d.get("maskedPan") or d.get("cardPan") or d.get("cardNumber") or "")
        return {"success": ok, "card_token": account_number,
                "brand": d.get("scheme") or d.get("brand") or "Verve",
                "last4": pan[-4:] if pan else "",
                "expiry": d.get("expiryDate") or d.get("expiry") or "",
                "message": _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def card_set_status(card_token: str, active: bool, *, masked_pan: str = "") -> dict:
    """Block a virtual card (permanent hotlist). ``card_token`` is the NUBAN.

    ALAT has no reversible freeze, so unfreezing (active=True) is reported
    unsupported rather than faked; blocking (active=False) hotlists the card
    permanently (also passes the masked PAN when the caller has it)."""
    if not _card_live():
        if _mock_blocked():
            return {"success": False, "message": "Card issuing is not configured"}
        return {"success": True, "mock": True}
    if active:
        return {"success": False,
                "message": "This card was blocked and can't be reactivated — request a new card."}
    try:
        data = _post("card", "/api/Partner/partnerCard/hotlistCard", {},
                     params={"maskedPan": masked_pan, "accountNumber": card_token}).json()
        return {"success": bool(data.get("successful")) or _ok(data), "message": _msg(data), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


def card_fund(card_token: str, amount) -> dict:
    """Incremental top-up. ALAT's virtual card is funded at issue and exposes no
    top-up endpoint, so a live call reports unsupported (the caller refunds the
    debit) rather than faking a success against a card it can't move."""
    if not _card_live():
        if _mock_blocked():
            return {"success": False, "message": "Card issuing is not configured"}
        return {"success": True, "mock": True}
    return {"success": False, "message": "Top-up isn't supported for this card"}


def card_reveal(card_token: str) -> dict:
    """Fetch card details for a one-time reveal (never persisted). ``card_token`` is
    the NUBAN ALAT keys the card by."""
    if not _card_live():
        if _mock_blocked():
            return {"success": False, "message": "Card issuing is not configured"}
        seed = int(hashlib.sha256(card_token.encode()).hexdigest(), 16)
        pan = "5061" + "".join(str((seed >> (i * 4)) % 10) for i in range(12))
        return {"success": True, "mock": True, "pan": pan, "cvv": f"{seed % 1000:03d}"}
    try:
        data = _get("card", f"/api/Partner/partnerCard/virtual-card-details/{card_token}").json()
        d = _card_data(data)
        ok = bool(data.get("status")) or bool(data.get("successful")) or _ok(data)
        return {"success": ok and bool(d),
                "pan": d.get("cardPan") or d.get("cardNumber") or d.get("pan", ""),
                "cvv": d.get("cvv") or d.get("cvv2", ""), "raw": data}
    except requests.RequestException as exc:
        return _unreachable(exc)


# ---------------------------------------------------------------------------
# KYC — Nigeria identity (BVN / NIN / vNIN)
#
# ALAT has NO standalone BVN/NIN/vNIN lookup: its "Full KYC" product opens/upgrades
# accounts and returns the holder's name only AFTER a full OTP account-creation
# round-trip. So identity is verified by the NUBAN provisioning flow (the
# /api/wallet/wema/* endpoints -> create_wallet_request / validate_wallet_otp /
# get_account_details), and the holder name ALAT returns is name-matched against the
# user's registered name (holder_name_mismatch) before the KYC tier is lifted — see
# wallet.views.wema_wallet_verify_otp.
#
# The verify_bvn / verify_nin / verify_vnin entry points are kept for the
# provider-agnostic contract but no longer call a (non-existent) lookup endpoint: in
# production they direct the caller to account setup; dev/tests keep a mock so the
# offline KYC flow still exercises. Identity NEVER mock-passes in production, so a
# misconfigured deploy can't fabricate one.
# ---------------------------------------------------------------------------
def _name_tokens(name: str) -> set:
    """Significant lowercased word tokens of a holder name (drops 1-char bits),
    for tolerant BVN/NIN name comparison."""
    return {t for t in re.sub(r"[^a-z ]", " ", (name or "").lower()).split() if len(t) > 1}


def holder_name_mismatch(supplied: str, resolved: str) -> bool:
    """True only when BOTH names are non-empty and share NO tokens — a clear
    mismatch. Tolerant of order / middle names so a legitimate holder is never
    blocked by a formatting difference.

    Used to name-match the holder record ALAT returns during NUBAN provisioning
    against the user's registered name before the KYC tier is lifted, so a BVN/NIN
    that demonstrably belongs to someone else can't lift the requester's tier."""
    a, b = _name_tokens(supplied), _name_tokens(resolved)
    return bool(a and b and not (a & b))


def _identity_unavailable_standalone() -> dict:
    """What verify_* returns in production: ALAT can't verify a number standalone, so
    route the user to account setup, where provisioning does the real name-matched
    check. otp_required signals the app to run the NUBAN OTP flow."""
    return {"success": False, "otp_required": True,
            "message": "Verify your identity when you set up your Zitch account."}


def verify_bvn(bvn: str, name: str = "", date_of_birth: str = "", mobile: str = "") -> dict:
    """BVN verification entry point (provider-agnostic contract).

    ALAT has no standalone BVN lookup — verification happens in the name-matched
    NUBAN account-creation flow. In production this directs the caller there; dev and
    tests keep a mock so the offline flow still works."""
    if len(bvn) != 11 or not bvn.isdigit():
        return {"success": False, "message": "BVN must be 11 digits"}
    if mock_disabled_in_prod():
        return _identity_unavailable_standalone()
    return {"success": True, "mock": True, "first_name": "", "last_name": ""}


def verify_nin(nin: str, name: str = "") -> dict:
    """NIN verification entry point — see verify_bvn. ALAT has no standalone NIN
    lookup; verification is the name-matched NUBAN account-creation flow."""
    if len(nin) != 11 or not nin.isdigit():
        return {"success": False, "message": "NIN must be 11 digits"}
    if mock_disabled_in_prod():
        return _identity_unavailable_standalone()
    return {"success": True, "mock": True, "first_name": "", "last_name": ""}


def verify_vnin(vnin: str, name: str = "") -> dict:
    """Virtual-NIN (16-char) verification entry point — see verify_bvn. No standalone
    ALAT lookup; verification is the name-matched NUBAN account-creation flow."""
    if not vnin or len(vnin) != 16:
        return {"success": False, "message": "Virtual NIN must be 16 characters"}
    if mock_disabled_in_prod():
        return _identity_unavailable_standalone()
    return {"success": True, "mock": True, "first_name": "", "last_name": ""}


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
