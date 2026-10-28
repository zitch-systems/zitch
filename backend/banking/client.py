"""9PSB v3.1 WaaS and VAS transport.

Money instructions are sent once. A timeout, invalid response, duplicate reference
or server error is an unknown outcome, never evidence that money was not moved.
No request/response bodies, tokens or personal identifiers are logged here.
"""
import hashlib
import json
import re
import threading
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.cache import cache

TIMEOUT = (5, 20)
_local = threading.local()
_AUTH_PATH = {"waas": "/waas/api/v1/authenticate", "vas": "/identity/api/v1/authenticate"}
_PREFIX = {"waas": "/waas/api/v1/", "vas": "/vas/api/v1/"}


class ProviderError(Exception):
    def __init__(self, code, *, uncertain=False):
        self.code = code
        self.uncertain = uncertain
        super().__init__(code)


def config():
    return getattr(settings, "NINEPSB", {})


def configured(product="waas"):
    conf = config()
    names = ("USERNAME", "PASSWORD", "CLIENT_ID", "CLIENT_SECRET") if product == "waas" else ("USERNAME", "PASSWORD")
    return bool(conf.get("BASE_URL") and all(conf.get(f"{product.upper()}_{name}") for name in names))


def require_environment(environment):
    current = config().get("ENVIRONMENT", "sandbox")
    if environment not in {"sandbox", "production"} or environment != current:
        raise ProviderError("bank_environment_mismatch")


def sandbox():
    return config().get("ENVIRONMENT", "sandbox") == "sandbox"


def customer_enabled():
    """Sandbox credentials are never a production customer money rail."""
    conf = config()
    return configured() and conf.get("ENABLED") is True and (
        (sandbox() and conf.get("SANDBOX_CUSTOMERS_ENABLED") is True)
        or (conf.get("ENVIRONMENT") == "production" and conf.get("UAT_APPROVED") is True)
    )


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
        return (200 <= self.http_status < 300
                and str(self.body.get("status", "")).upper() == "SUCCESS")

    @property
    def identity_state(self):
        # Identity API explicitly uses message, not status, as its decision.
        if not 200 <= self.http_status < 300:
            return "PENDING"
        state = str(self.body.get("message", "")).upper()
        return state if state in {"SUCCESS", "PENDING", "FAILED"} else "PENDING"

    def payment_state(self, *, product="waas", query=False):
        if self.http_status >= 500 or self.http_status in {408, 409, 429}:
            return "pending"
        body = self.body
        data = self.data if isinstance(self.data, dict) else {}
        code = str(body.get("responseCode", body.get("statusCode", ""))).upper()
        nested_code = str(data.get("responseCode", "")).upper()
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
        if (nested_code and nested_code != code) or nested_status not in {"", status}:
            return "pending"
        if status == "SUCCESS" and data.get("isSuccessful", True) is not True:
            return "pending"
        if status == "FAILED" and data.get("isSuccessful", False) is not False:
            return "pending"
        if product == "vas":
            if self.ok and code == "200":
                return "success"
            if status == "FAILED" and code in {"300", "400", "401", "403"}:
                return "failed"
        else:
            # 42 is an existing transaction and must be requeried, not refunded.
            if code in {"09", "42", "96", "97", "99"}:
                return "pending"
            if self.ok and code == "00":
                return "success"
            if status == "FAILED" and code in {"03", "05", "06", "07", "08", "12", "13", "15", "16", "17", "25", "26", "51", "61", "93", "94", "95"}:
                return "failed"
        return "pending"


class Client:
    def __init__(self, product="waas"):
        if product not in _PREFIX:
            raise ValueError("Unknown bank product")
        self.product = product

    def _base(self):
        base = str(config().get("BASE_URL", "")).rstrip("/")
        parsed = urlsplit(base)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ProviderError("invalid_base_url")
        if parsed.scheme != "https":
            # The bank supplied this exact HTTP-only sandbox host. No arbitrary
            # insecure-host bypass, and no real customer data may use it.
            if not (sandbox() and base == "http://102.216.128.75:9090"):
                raise ProviderError("https_required")
        if not parsed.hostname:
            raise ProviderError("invalid_base_url")
        return base

    def _credentials(self):
        prefix = self.product.upper()
        result = {"username": config().get(prefix + "_USERNAME"),
                  "password": config().get(prefix + "_PASSWORD")}
        if self.product == "waas":
            result.update(clientId=config().get("WAAS_CLIENT_ID"),
                          clientSecret=config().get("WAAS_CLIENT_SECRET"))
        if not all(result.values()):
            raise ProviderError("credentials_missing")
        return result

    def _cache_key(self):
        # Changing credentials or environment cannot reuse another tenant's token.
        material = json.dumps([self._base(), self._credentials()], sort_keys=True)
        return "9psb:token:" + self.product + ":" + hashlib.sha256(material.encode()).hexdigest()

    @staticmethod
    def _send(method, url, *, payload=None, params=None, token=None):
        session = getattr(_local, "session", None)
        if session is None:
            session = _local.session = requests.Session()
            # requests defaults to no retries, including POST. Do not mount an
            # adapter that replays money instructions on disconnects.
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            response = session.request(method, url, json=payload, params=params,
                                       headers=headers, timeout=TIMEOUT, allow_redirects=False)
        except requests.RequestException:
            raise ProviderError("network_unavailable", uncertain=method != "GET") from None
        if 300 <= response.status_code < 400:
            raise ProviderError("redirect_refused", uncertain=method != "GET")
        try:
            body = response.json()
        except ValueError:
            raise ProviderError("invalid_response", uncertain=method != "GET") from None
        if not isinstance(body, dict):
            raise ProviderError("invalid_response", uncertain=method != "GET")
        return Reply(body, response.status_code)

    def token(self):
        key = self._cache_key()
        token = cache.get(key)
        if token:
            return token
        # A concurrent request waits by returning retryable unavailable, without
        # stamping out dozens of paid authentication requests or holding threads.
        lock = key + ":auth"
        if not cache.add(lock, True, timeout=30):
            raise ProviderError("authentication_in_progress")
        try:
            try:
                result = self._send("POST", self._base() + _AUTH_PATH[self.product], payload=self._credentials())
            except ProviderError as exc:
                raise ProviderError(exc.code, uncertain=False) from None
            data = result.body if self.product == "waas" else result.data
            token = data.get("accessToken") if isinstance(data, dict) else None
            if not 200 <= result.http_status < 300 or not isinstance(token, str) or not token:
                raise ProviderError("authentication_failed")
            try:
                expires = int(data.get("expiresIn", 300))
            except (ValueError, TypeError):
                expires = 300
            cache.set(key, token, timeout=max(1, min(expires - 30, 3600)))
            return token
        finally:
            cache.delete(lock)

    def request(self, path, payload=None, *, method="POST", params=None):
        if path.startswith("/") or ".." in path or ":" in path:
            raise ValueError("Invalid provider path")
        token = self.token()  # Auth failures happen before the instruction.
        result = self._send(method, self._base() + _PREFIX[self.product] + path,
                            payload=payload, params=params, token=token)
        if result.http_status == 401:
            cache.delete(self._cache_key())
            # Never automatically resubmit a money instruction after auth failure.
        return result

    def initiate_identity(self, ref, kind, identifier, *, method="OTP", mobile="", image=""):
        if kind not in {"bvn", "nin"} or not re.fullmatch(r"[0-9]{11}", identifier or ""):
            raise ValueError("An eleven-digit BVN or NIN is required")
        if method not in {"OTP", "FACIAL"}:
            raise ValueError("Invalid identity verification method")
        from .validation import base64_file
        payload = {"transactionRef": reference(ref), kind: identifier, "type": method}
        if mobile:
            payload["phoneNo"] = phone(mobile)
        if method == "FACIAL":
            payload["image"] = base64_file(image, maximum_bytes=5 * 1024 * 1024, image_only=True)
        return self.request("identity/initiate", payload)

    def verify_identity(self, ref, otp):
        if not re.fullmatch(r"[0-9]{6}", otp or ""):
            raise ValueError("Enter the six-digit code")
        return self.request("identity/verify-otp", {"transactionRef": reference(ref), "otp": otp})

    def open_wallet(self, ref, profile, *, kind, identifier):
        from .validation import wallet_profile
        if kind not in {"bvn", "nin"} or not re.fullmatch(r"[0-9]{11}", identifier or ""):
            raise ValueError("Verified identity is required")
        payload = {"transactionTrackingRef": reference(ref), **wallet_profile(profile)}
        payload["bvn" if kind == "bvn" else "nationalIdentityNo"] = identifier
        if kind == "nin" and not payload.get("ninUserId"):
            raise ValueError("NIN user ID is required for a NIN-only wallet")
        return self.request("open_wallet", payload)

    def wallet(self, number):
        return self.request("wallet_enquiry", {"accountNo": account(number)})

    def lookup_wallet(self, kind, identifier):
        if kind not in {"bvn", "nin"} or not re.fullmatch(r"[0-9]{11}", identifier or ""):
            raise ValueError("Verified identity is required")
        # July v3.1 explicitly specifies GET with a JSON request body. Keep
        # personal identifiers out of URL query strings and access logs.
        return self.request("get_wallet", {kind: identifier}, method="GET")

    def banks(self):
        return self.request("get_banks", method="GET")

    def resolve(self, number, bank_code):
        if not re.fullmatch(r"[0-9]{3,6}", str(bank_code)):
            raise ValueError("Invalid bank code")
        return self.request("other_banks_enquiry", {"customer": {"account": {"bank": bank_code, "number": account(number)}}})

    def transfer(self, ref, amount, source, destination, bank_code, destination_name, sender_name, narration):
        return self.request("wallet_other_banks", {
            "customer": {"account": {"bank": str(bank_code), "name": destination_name,
                "number": account(destination), "senderaccountnumber": account(source), "sendername": sender_name}},
            "narration": str(narration)[:100],
            "order": {"amount": money(amount), "country": "NGA", "currency": "NGN", "description": str(narration)[:100]},
            "transaction": {"reference": reference(ref)}, "transactionType": "OTHER_BANKS",
            "merchant": {"isFee": False},
        })

    def float_transfer(self, direction, ref, amount, number, narration):
        if direction not in {"credit", "debit"}:
            raise ValueError("Invalid movement")
        return self.request(direction + "/transfer", {
            "accountNo": account(number), "totalAmount": money(amount), "transactionId": reference(ref),
            "narration": str(narration)[:100], "merchant": {"isFee": False}})

    def transaction_status(self, ref, amount, kind, date, number):
        # Preserve decimal naira exactly; never round kobo to fit a sample schema.
        return self.request("wallet_requery", {"transactionId": reference(ref), "amount": money(amount),
            "transactionType": kind, "transactionDate": date, "accountNo": account(number)})

    def notification(self, session, number):
        if not isinstance(session, str) or not session or len(session) > 160:
            raise ValueError("Invalid notification reference")
        return self.request("notification_requery", {"sessionID": session, "accountNumber": account(number)})

    def history(self, number, start, end, limit=100):
        from datetime import date
        if date.fromisoformat(start) > date.fromisoformat(end):
            raise ValueError("Invalid date range")
        return self.request("wallet_transactions", {"accountNumber": account(number), "fromDate": start,
            "toDate": end, "numberOfItems": str(max(1, min(int(limit), 1000)))})

    def upgrade(self, number, tier, payload):
        from .validation import upgrade_profile
        return self.request("wallet_upgrade", {"accountNumber": account(number), **upgrade_profile(tier, payload)})

    def upgrade_status(self, number):
        return self.request("upgrade_status", {"accountNumber": account(number)})

    def wallet_status(self, number):
        return self.request("wallet_status", {"accountNo": account(number)})

    def set_wallet_status(self, number, status):
        if status not in {"ACTIVE", "SUSPENDED"}:
            raise ValueError("Invalid wallet status")
        return self.request("change_wallet_status", {"accountNumber": account(number), "accountStatus": status})

    def network(self, mobile):
        return self.request("topup/network", method="GET", params={"phone": phone(mobile)})

    def data_plans(self, mobile):
        return self.request("topup/dataPlans", method="GET", params={"phone": phone(mobile)})

    def categories(self):
        return self.request("billspayment/categories", method="GET")

    def billers(self, category):
        return self.request("billspayment/billers/" + self._catalog_id(category), method="GET")

    def fields(self, biller):
        return self.request("billspayment/fields/" + self._catalog_id(biller), method="GET")

    @staticmethod
    def _catalog_id(value):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(value)):
            raise ValueError("Invalid catalog ID")
        return str(value)

    def validate_customer(self, biller, customer, *, item="", amount=None):
        payload = {"billerId": self._catalog_id(biller), "customerId": str(customer)}
        if item:
            payload["itemId"] = self._catalog_id(item)
        if amount is not None:
            payload["amount"] = money(amount)
        return self.request("billspayment/validate", payload)

    def topup(self, kind, ref, amount, number, mobile, network, *, product_id=""):
        if kind not in {"airtime", "data"}:
            raise ValueError("Invalid top-up type")
        payload = {"phoneNumber": phone(mobile), "network": str(network), "amount": money(amount),
            "debitAccount": account(number), "transactionReference": reference(ref, length=18)}
        if kind == "data":
            payload["productId"] = self._catalog_id(product_id)
        return self.request("topup/" + kind, payload)

    def pay_bill(self, ref, amount, number, *, biller, item, customer, mobile, name, other_field=""):
        return self.request("billspayment/pay", {"billerId": self._catalog_id(biller),
            "itemId": self._catalog_id(item), "customerId": str(customer), "customerPhone": phone(mobile),
            "customerName": name, "otherField": other_field, "amount": money(amount),
            "debitAccount": account(number), "transactionReference": reference(ref, length=18)})

    def vas_status(self, ref, *, bill=False):
        return self.request(("billspayment" if bill else "topup") + "/status", method="GET",
                            params={"transReference": reference(ref, length=18)})
