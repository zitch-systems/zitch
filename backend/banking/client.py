"""The bank provider boundary, with no provider behind it.

The 9PSB WaaS and VAS transport was removed. What remains is the shape the rest
of the platform imports: the error type, the provider-state predicates (all of
which now answer "no bank"), the pure amount/reference/account validators, and
`Reply`, which parses a provider envelope and is not itself provider-specific.

A future partner bank is implemented behind this same boundary: give `Client`
real methods and make `config`/`configured`/`customer_enabled` read its
settings. Nothing outside this module needs to change for that.
"""
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

log = logging.getLogger(__name__)


class ProviderError(Exception):
    def __init__(self, code, *, uncertain=False, http_status=None, response_code="", provider_status=""):
        self.code = code
        self.uncertain = uncertain
        self.http_status = http_status
        self.response_code = response_code
        self.provider_status = provider_status
        super().__init__(code)


def config():
    """No provider settings exist. Kept so callers reading flags still work."""
    return {}


def configured(product="waas"):
    return False


def require_environment(environment):
    current = config().get("ENVIRONMENT", "sandbox")
    if environment not in {"sandbox", "production"} or environment != current:
        raise ProviderError("bank_environment_mismatch")


def sandbox():
    return config().get("ENVIRONMENT", "sandbox") == "sandbox"


def sandbox_auto_verify():
    """Sandbox-only identity and address fixtures for enrolled testers.

    It never applies outside the sandbox environment, whatever the flag says,
    Wallet opening and tier upgrades still need the bank's own response and
    independent readback. Address fixtures never initiate a physical visit.
    """
    return sandbox() and config().get("SANDBOX_AUTO_VERIFY") is True


def customer_enabled():
    """No bank rail, so no customer may be onboarded onto one."""
    return False


def money(value):
    if isinstance(value, bool):
        raise ValueError("Invalid amount")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Invalid amount") from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Amount must be positive with at most two decimal places")
    if amount >= Decimal("1000000000000"):
        raise ValueError("Amount is too large")
    if amount != amount.quantize(Decimal(".01")):
        raise ValueError("Amount must be positive with at most two decimal places")
    return format(amount, ".2f")


def vas_amount(value):
    """VAS wire amount in the supplied samples' form: whole naira as "100".

    Every VAS request and catalog sample uses whole-naira strings, and data and
    bill payments must send the catalog/validation value. Kobo is kept, never
    rounded away.
    """
    amount = money(value)
    return amount[:-3] if amount.endswith(".00") else amount


def account(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{10}", value):
        raise ValueError("A ten-digit account number is required")
    return value


def phone(value):
    value = re.sub(r"[\s()+-]", "", str(value or ""))
    if value.startswith("234"):
        value = "0" + value[3:]
    if not re.fullmatch(r"0[789][01][0-9]{8}", value):
        raise ValueError("A valid Nigerian mobile number is required")
    return value


def reference(value, *, length=None, maximum=25):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid transaction reference")
    if len(value) > maximum or (length and len(value) != length):
        raise ValueError("Invalid transaction reference length")
    return value


def wallet_number(data):
    """The observed NUBAN is distinct from 9PSB's internal wallet number."""
    if not isinstance(data, dict):
        return ""
    for key in ("nuban", "accountNumber", "number"):
        if key in data:
            value = data[key]
            return value if isinstance(value, str) and re.fullmatch(r"[0-9]{10}", value) else ""
    return ""


def _code(value):
    # JSON null or a blank field carries no code; str(None) would read as "None".
    return "" if value is None else str(value).strip()


def affirmative_code(value):
    """Success code on a read-only WaaS enquiry (bank directory, account readback).

    The WaaS Postman samples confirm with "00"; the sandbox's get_banks answers
    responseCode "SUCCESS" (30 October build log). Payment and identity replies
    keep their stricter, sample-based codes.
    """
    return _code(value).upper() in {"", "00", "SUCCESS"}


def bank_directory(reply):
    data = reply.data if isinstance(reply.data, dict) else {}
    codes = [_code(reply.body.get("responseCode")), _code(data.get("responseCode"))]
    rejected = next((code for code in codes if not affirmative_code(code)), "")
    if not reply.ok or rejected:
        # Name only the HTTP status, a short code token and a one-word status;
        # never the message or body.
        status = str(reply.body.get("status", "")).strip()
        raise ProviderError("bank_directory_unconfirmed", http_status=reply.http_status,
            response_code=(rejected if re.fullmatch(r"[A-Za-z0-9_-]{1,24}", rejected)
                           else "withheld" if rejected else ""),
            provider_status=(status if re.fullmatch(r"[A-Za-z][A-Za-z_-]{0,23}", status)
                             else "unrecognized" if status else "absent"))
    rows = data.get("bankList")
    if not isinstance(rows, list):
        raise ProviderError("bank_directory_malformed", http_status=reply.http_status)
    result = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("bankName"), str):
            continue
        name = row["bankName"].strip()
        code = str(row.get("bankCode", "")).strip()
        if name and len(name) <= 160 and re.fullmatch(r"[0-9]{3,6}", code):
            result.append({"bank_name": name, "bank_code": code})
    if not result:
        raise ProviderError("bank_directory_empty")
    return result


def confirmed_recipient(reply, number, bank_code):
    """Bind a successful name enquiry to the exact requested account and route.

    Keep the observed top-level NIP payload and support that same customer/account
    object inside the July WaaS PDF's status/data envelope. Conflicting echoes
    must never be resolved by picking whichever representation looks successful.
    """
    if reply.http_status != 200 or not isinstance(reply.body, dict):
        return None
    body = reply.body
    if not (reply.ok or _code(body.get("code")) == "00"):
        return None
    data = body.get("data")
    if data is not None and not isinstance(data, dict):
        return None
    envelopes = [body, *([data] if isinstance(data, dict) else [])]
    names = []
    for envelope in envelopes:
        for field in ("code", "responseCode", "statusCode"):
            if not affirmative_code(envelope.get(field)):
                return None
        if _code(envelope.get("status")).upper() not in {"", "SUCCESS"}:
            return None
        if envelope.get("isSuccessful", True) is not True:
            return None
        if "customer" not in envelope:
            continue
        customer = envelope["customer"]
        info = customer.get("account") if isinstance(customer, dict) else None
        if not isinstance(info, dict):
            return None
        name = info.get("name")
        if (str(info.get("number")) != number or str(info.get("bank")) != str(bank_code)
                or not isinstance(name, str) or not 0 < len(name.strip()) <= 160):
            return None
        names.append(name.strip())
    if not names or len({" ".join(name.casefold().split()) for name in names}) != 1:
        return None
    return {"name": names[0], "account_number": number, "bank_code": str(bank_code)}


def found_wallet(reply, kind, identifier):
    """The get_wallet reply lists wallets under data.wallet. Return a reply whose
    data is the one wallet opened with this identity, in the open_wallet shape."""
    entries = reply.data.get("wallet") if isinstance(reply.data, dict) else None
    if not isinstance(entries, list):
        return reply
    field = "bvn" if kind == "bvn" else "nationalIdentityNumber"
    matches = [w for w in entries if isinstance(w, dict) and str(w.get(field, "")) == identifier]
    if len(matches) != 1:
        return Reply({**reply.body, "data": {}}, reply.http_status)
    wallet = matches[0]
    name = str(wallet.get("accountName") or "")
    merchant = str(wallet.get("merchantId") or "")
    # The bank prefixes the account name with the merchant id: "MERCHANT/Jane Doe".
    if merchant and name.upper().startswith(merchant.upper() + "/"):
        name = name[len(merchant) + 1:]
    return Reply({**reply.body, "data": {"accountNumber": wallet.get("accountNumber"), "fullName": name.strip()}},
                 reply.http_status)


@dataclass(frozen=True)
class Reply:
    body: dict
    http_status: int

    @property
    def data(self):
        value = self.body.get("data")
        return value if isinstance(value, (dict, list)) else {}

    @property
    def ok(self):
        # Supplied contracts confirm results with HTTP 200. An accepted (202)
        # request or empty/partial success response is not completion evidence.
        return (self.http_status == 200
                and str(self.body.get("status", "")).upper() == "SUCCESS")

    @property
    def identity_state(self):
        # Identity API explicitly uses message, not status, as its decision.
        if self.http_status != 200:
            return "PENDING"
        state = str(self.body.get("message", "")).upper()
        if state == "SUCCESS" and (
                str(self.body.get("status", "")).upper() in {"FAILED", "FAILURE", "ERROR"}
                or str(self.body.get("responseCode", "00")) != "00"):
            return "PENDING"
        return state if state in {"SUCCESS", "PENDING", "FAILED"} else "PENDING"

    def payment_state(self, *, product="waas", query=False):
        if self.http_status not in {200, 400, 401, 403, 422}:
            return "pending"
        body = self.body
        data = self.data if isinstance(self.data, dict) else {}
        # A null or absent code carries no decision; str(None) would read "NONE".
        code = _code(body.get("responseCode")).upper() or _code(body.get("statusCode")).upper()
        nested_code = _code(data.get("responseCode")).upper()
        # The sandbox answers credit/transfer with the response code under data
        # and none at the top level, so read either code for WaaS. VAS keeps
        # its separate envelope-code contract; requery below still requires
        # explicit terminal transaction evidence. Both absent decides nothing.
        confirmed = code or (nested_code if product == "waas" else "")
        success_code = "200" if product == "vas" else "00"
        # A successful status lookup does not mean the queried payment succeeded.
        if query:
            if not self.ok or code not in {"", success_code}:
                return "pending"
            if product == "vas":
                # VAS PDF and supplied Postman success sample.
                status = str(data.get("transactionStatus", "")).upper()
                if code != "200" or nested_code not in {"", "200"}:
                    return "pending"
                # The sandbox also labels a missing transaction FAILED. An
                # absent reference cannot establish the original debit outcome.
                description = data.get("description")
                if isinstance(description, str) and re.fullmatch(
                        r"Transaction with Reference:\s*\S+\s+not found\.?",
                        description.strip(), flags=re.IGNORECASE):
                    return "pending"
                if status == "SUCCESS" and data.get("isSuccessful", True) is True:
                    return "success"
                if status == "FAILED" and data.get("isSuccessful", False) is False:
                    return "failed"
            else:
                # WaaS Postman wallet_requery sample uses data.status,
                # data.isSuccessful and data.responseCode, not VAS's field.
                status = str(data.get("status", "")).upper()
                if (status == "SUCCESS" and nested_code == "00"
                        and data.get("isSuccessful") is True):
                    return "success"
                # No definitive negative requery sample has been supplied.
                # Preserve the reservation until an explicit failure contract
                # or reconciled bank evidence establishes that no money moved.
            return "pending"
        status = str(body.get("status", "")).upper()
        nested_status = str(data.get("transactionStatus", data.get("status", ""))).upper()
        # Two codes that disagree contradict each other; one absent code does not.
        if (code and nested_code and nested_code != code) or nested_status not in {"", status}:
            return "pending"
        if status == "SUCCESS" and data.get("isSuccessful", True) is not True:
            return "pending"
        if status == "FAILED" and data.get("isSuccessful", False) is not False:
            return "pending"
        if product == "vas":
            if self.ok and confirmed == "200":
                return "success"
            # The supplied Postman duplicate-reference example is HTTP 200 /
            # failed / 300. It does not prove the original debit failed. Query
            # its existing reference; never refund or replay this instruction.
            if status == "FAILED" and confirmed in {"400", "401", "403"}:
                return "failed"
        else:
            # 42 is an existing transaction and must be requeried, not refunded.
            if confirmed in {"09", "42", "96", "97", "99"}:
                return "pending"
            if self.ok and confirmed == "00":
                return "success"
            if status == "FAILED" and confirmed in {"03", "05", "06", "07", "08", "12", "13", "15", "16", "17", "25", "26", "51", "61", "93", "94", "95"}:
                return "failed"
        return "pending"


class Client:
    """The provider boundary with no provider behind it.

    Every operation the 9PSB transport performed is still declared here, with
    its original signature, and every one refuses. Keeping the real signatures
    is deliberate: a caller passing the wrong arguments still fails as a
    TypeError rather than silently reaching a refusal, and the test suite can
    still patch a named method to stand in for a bank.

    `uncertain` is False on every refusal. An uncertain outcome means a request
    may have moved money; nothing leaves this process, so nothing can have.
    """

    def __init__(self, product="waas", *, timeout=None):
        self.product = product
        self.timeout = timeout

    @staticmethod
    def _unavailable():
        raise ProviderError("no_bank_provider")

    # Transport members that outlived their transport. They are still reached
    # for: cache keys are derived from the credentials (transfers.services,
    # banking.bills, banking.adapter) and the token is warmed before concurrent
    # reads. Absent, each would raise AttributeError -- an unhandled 500 on a
    # path whose callers are all written to handle ProviderError and degrade
    # cleanly. Refusing in the same currency as everything else keeps that true.
    def _cache_key(self):
        return self._unavailable()

    def token(self):
        return self._unavailable()

    def request(self, path, payload=None, *, method="POST", params=None):
        return self._unavailable()

    def initiate_identity(self, ref, kind, identifier, *, method='OTP', mobile='', image=''):
        return self._unavailable()

    def verify_identity(self, ref, otp):
        return self._unavailable()

    def open_wallet(self, ref, profile, *, kind, identifier):
        return self._unavailable()

    def wallet(self, number):
        return self._unavailable()

    def lookup_wallet(self, kind, identifier):
        return self._unavailable()

    def banks(self):
        return self._unavailable()

    def resolve(self, number, bank_code):
        return self._unavailable()

    def transfer(self, ref, amount, source, destination, bank_code, destination_name, sender_name, narration):
        return self._unavailable()

    def float_transfer(self, direction, ref, amount, number, narration):
        return self._unavailable()

    def transaction_status(self, ref, amount, kind, date, number):
        return self._unavailable()

    def notification(self, session, number):
        return self._unavailable()

    def history(self, number, start, end, limit=100):
        return self._unavailable()

    def upgrade(self, number, tier, payload):
        return self._unavailable()

    def upgrade_status(self, number):
        return self._unavailable()

    def wallet_status(self, number):
        return self._unavailable()

    def set_wallet_status(self, number, status):
        return self._unavailable()

    def network(self, mobile):
        return self._unavailable()

    def data_plans(self, mobile):
        return self._unavailable()

    def categories(self):
        return self._unavailable()

    def billers(self, category):
        return self._unavailable()

    def fields(self, biller):
        return self._unavailable()

    def validate_customer(self, biller, customer, *, item='', amount=None, firstname='', lastname='', customer_phone='', phone_number=''):
        return self._unavailable()

    def topup(self, kind, ref, amount, number, mobile, network, *, product_id=''):
        return self._unavailable()

    def pay_bill(self, ref, amount, number, *, biller, item, customer, mobile, name, other_field=''):
        return self._unavailable()

    def vas_status(self, ref, *, bill=False):
        return self._unavailable()

    @staticmethod
    def _catalog_id(value):
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(value)):
                raise ValueError("Invalid catalog ID")
            return str(value)

    @staticmethod
    def _product_id(value):
            # Live plans include "MTN-2.5GB-27" and "MTN-2GB 2-75". Product IDs
            # are JSON body values, never URL path segments.
            value = str(value)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,79}", value) or ".." in value:
                raise ValueError("Invalid catalog ID")
            return value

    @staticmethod
    def _item_id(value):
            # Bundle selections are JSON values, not URL path segments. Live cable
            # catalogs join component IDs with '+', e.g. a package plus an add-on.
            if (not isinstance(value, str) or len(value) > 80
                    or not re.fullmatch(r"[A-Za-z0-9_-]+(?:\+[A-Za-z0-9_-]+)*", value)):
                raise ValueError("Invalid bill item ID")
            return value
