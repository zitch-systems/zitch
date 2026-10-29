"""Repeatable, redacted sandbox connectivity checks; no ledger or money writes.

These are prerequisites for customer UAT. Passing them never certifies money,
identity or callback acceptance, and never enables any runtime feature flag.
"""
import re
from decimal import Decimal, InvalidOperation

from .client import Client, ProviderError, account, config, phone, bank_directory, wallet_number
from .readiness import endpoint_report


def sandbox_checks(fixtures=None, *, product="all"):
    if product not in {"all", "waas", "vas"}:
        raise ValueError("Choose waas, vas or all")
    if config().get("ENVIRONMENT") != "sandbox":
        raise ValueError("This command only accepts the sandbox environment")
    fixtures = fixtures or {}
    if not isinstance(fixtures, dict) or set(fixtures) - {"account_number", "mobile"}:
        raise ValueError("Fixtures must contain only account_number and/or mobile")
    number = account(fixtures["account_number"]) if fixtures.get("account_number") else ""
    mobile = phone(fixtures["mobile"]) if fixtures.get("mobile") else ""
    checks = []

    def check(name, call, validate):
        try:
            value = call()
            passed = validate(value)
            checks.append({"check": name, "status": "passed" if passed else "failed",
                           **({} if passed else {"reason": "response_not_confirmed"})})
            return value if passed else None
        except (ProviderError, ValueError, TypeError, AttributeError, InvalidOperation) as exc:
            checks.append({"check": name, "status": "failed",
                           "reason": exc.code if isinstance(exc, ProviderError) else "invalid_response",
                           **({"http_status": exc.http_status, "response_code": exc.response_code}
                              if isinstance(exc, ProviderError) and exc.http_status is not None else {})})
            return None

    def blocked(name, reason):
        checks.append({"check": name, "status": "blocked", "reason": reason})

    if product in {"all", "waas"}:
        waas = Client()
        if check("waas_authentication", waas.token, lambda token: isinstance(token, str) and bool(token)):
            directory = check("bank_directory", lambda: bank_directory(waas.banks()), bool)
            if number:
                def valid_wallet(r):
                    if not r.ok or not isinstance(r.data, dict):
                        return False
                    data = r.data
                    if (wallet_number(data) != number
                            or not isinstance(data.get("name"), str) or not data["name"].strip()
                            or data.get("isSuccessful", True) is not True):
                        return False
                    for key in ("availableBalance", "ledgerBalance"):
                        value = data.get(key)
                        if isinstance(value, bool):
                            return False
                        amount = Decimal(str(value))
                        if not amount.is_finite():
                            return False
                    return True
                check("provided_test_wallet_read", lambda: waas.wallet(number), valid_wallet)
                rows = directory or []
                codes = [row["bank_code"] for row in rows
                    if re.sub(r"[^a-z0-9]", "", row["bank_name"].lower())
                    in {"9psb", "9paymentservicebank", "9paymentservicebanklimited"}]
                if len(codes) == 1:
                    def valid_enquiry(r):
                        data = r.body.get("customer", {}).get("account", {})
                        return (r.http_status == 200 and str(r.body.get("code")) == "00"
                            and str(data.get("number")) == number and str(data.get("bank")) == codes[0]
                            and isinstance(data.get("name"), str) and bool(data["name"].strip()))
                    check("provided_test_wallet_name_enquiry", lambda: waas.resolve(number, codes[0]), valid_enquiry)
                else:
                    blocked("provided_test_wallet_name_enquiry", "unambiguous_bank_route_required")
            else:
                blocked("provided_test_wallet_read", "test_account_fixture_missing")
        else:
            blocked("bank_directory", "waas_authentication_failed")
            blocked("provided_test_wallet_read", "waas_authentication_failed")

    if product in {"all", "vas"}:
        vas = Client("vas")
        if check("vas_authentication", vas.token, lambda token: isinstance(token, str) and bool(token)):
            check("vas_categories", vas.categories, lambda r: r.ok and str(r.body.get("responseCode")) == "200"
                and isinstance(r.data, list) and bool(r.data))
            if mobile:
                check("provided_test_phone_network", lambda: vas.network(mobile), lambda r: r.ok
                    and str(r.body.get("responseCode")) == "200" and isinstance(r.data, dict)
                    and isinstance(r.data.get("network"), str) and bool(r.data["network"].strip()))
                check("provided_test_phone_data_plans", lambda: vas.data_plans(mobile), lambda r: r.ok
                    and str(r.body.get("responseCode")) == "200" and isinstance(r.data, list) and bool(r.data))
            else:
                blocked("provided_test_phone_network", "test_phone_fixture_missing")
        else:
            for name in ("vas_categories", "provided_test_phone_network", "provided_test_phone_data_plans"):
                blocked(name, "vas_authentication_failed")

    return {"environment": "sandbox", "mode": "read_only", "checks": checks,
        "requested_product": product,
        "endpoints": {name: endpoint_report(name) for name in ("waas", "vas") if product in {"all", name}},
        "connectivity_passed": all(row["status"] == "passed" for row in checks),
        "uat_complete": False, "activation_ready": False,
        "unverified_scenarios": ["bvn_nin_and_face_ownership", "wallet_opening_and_upgrades",
            "funded_transfer_and_fee_debit", "vas_purchase_and_entitlement",
            "bank_delivered_callbacks_and_duplicate_delivery", "pending_requery_and_reversal",
            "tier_limits_and_pnd", "address_final_report"]}
