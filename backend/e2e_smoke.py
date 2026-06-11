"""End-to-end smoke test against a RUNNING dev server (mock providers).

Drives the real HTTP surface the way the clients do:
  A. the Expo app's full journey (signup -> OTP -> password/PIN -> fund ->
     airtime/data -> transfers -> savings -> loans -> cards -> FX -> KYC ->
     exams/betting -> history -> idempotency replay)
  B. the ops portal API   (/api/ops/*)    incl. role-gated writes
  C. the console admin API (/api/admin/*) incl. every write action + RBAC denials
  D. the WhatsApp ops endpoints + webhook
  E. every web surface (landing, portals, prototype, health probes)

Usage:
    python manage.py migrate && python manage.py seed_plans
    python manage.py runserver 0.0.0.0:8000 &
    python e2e_smoke.py [BASE_URL]

Mutates the target database (creates users/transactions); run it against a
dev/staging instance only. Exits 0 when every step passed.
"""
import os
import random
import sys
import time
import uuid

import django
import requests

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zitch_api.settings")
django.setup()

from django.contrib.auth.models import Group  # noqa: E402

from accounts.models import OTP, User  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   <- {detail}"))
    return cond


def post(path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return requests.post(BASE + path, json=body or {}, headers=headers, timeout=30)


def get(path, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return requests.get(BASE + path, headers=headers, timeout=30)


def j(res):
    try:
        return res.json()
    except ValueError:
        return {}


def signup(phone, email, password="Sup3r#secret", pin="1234", first="Test", last="User"):
    """Full app signup; returns an access token (None on failure)."""
    r = post("/api/phone_verification/", {"phone": phone, "email": email})
    if r.status_code != 200:
        return None, f"phone_verification {r.status_code}: {j(r)}"
    otp = OTP.objects.filter(phone=phone, used=False).order_by("-created").first()
    if otp is None:
        return None, "no OTP row created"
    r = post("/api/verify_otp/", {"phone": phone, "otp": otp.code})
    tok = j(r).get("access_token")
    if not tok:
        return None, f"verify_otp {r.status_code}: {j(r)}"
    for path, body in (("/api/set-password/", {"password": password}),
                       ("/api/update_info/", {"first_name": first, "last_name": last}),
                       ("/api/set-transaction-pin/", {"pin": pin})):
        r = post(path, body, token=tok)
        if r.status_code != 200:
            return None, f"{path} {r.status_code}: {j(r)}"
    return tok, None


def fund(tok, amount="50000"):
    r = post("/api/fund/initialize/", {"amount": amount}, token=tok)
    ref = j(r).get("reference")
    if not ref:
        return False, f"fund/initialize {r.status_code}: {j(r)}"
    r = post("/api/fund/verify/", {"reference": ref}, token=tok)
    return j(r).get("success") is True, f"fund/verify {r.status_code}: {j(r)}"


def main():
    suffix = f"{random.randint(0, 9999):04d}"
    phone_a, phone_b = f"0809{suffix}111", f"0809{suffix}222"

    # =================================================================== A
    print("\n[A] Mobile-app journey")
    tok, err = signup(phone_a, f"ada{suffix}@e2e.test")
    if not check("signup → OTP → password → PIN", bool(tok), err or ""):
        report()
    r = post("/api/logout/", token=tok)
    check("logout", r.status_code == 200, str(j(r)))
    r = post("/api/sigin/", {"email_or_phone": phone_a, "password": "Sup3r#secret"})
    tok = j(r).get("access_token")
    check("signin returns access_token", bool(tok), str(j(r)))

    r = post("/api/wallet_balance/", token=tok)
    check("wallet_balance shape", j(r).get("success") is True and "wallet" in j(r), str(j(r)))

    okf, detail = fund(tok)
    check("fund wallet (Monnify mock)", okf, detail)
    bal = float(j(post("/api/wallet_balance/", token=tok))["wallet"])
    check("balance reflects funding", bal >= 50000, f"balance={bal}")

    # KYC first (raises limits for the rest of the run)
    for step, body in (("bvn", {"bvn": "12345678901"}), ("nin", {"nin": "12345678901"}), ("face", {"selfie": "data:image/png;base64,aGk="})):
        r = post(f"/api/kyc/{step}/", body, token=tok)
        check(f"kyc/{step} verifies (mock Prembly)", j(r).get("success") is True, f"{r.status_code} {j(r)}")
    r = post("/api/kyc/status/", token=tok)
    check("kyc/status tier=3", j(r).get("tier") == 3, str(j(r)))

    idem = f"e2e-{uuid.uuid4().hex[:12]}"
    r = post("/api/utility/buyairtime/", {"network": "1", "phone": phone_a, "amount": "200",
                                          "transaction_pin": "1234", "idempotency_key": idem}, token=tok)
    check("buy airtime", j(r).get("success") is True, f"{r.status_code} {j(r)}")
    ref1 = j(r).get("reference")
    r = post("/api/utility/buyairtime/", {"network": "1", "phone": phone_a, "amount": "200",
                                          "transaction_pin": "1234", "idempotency_key": idem}, token=tok)
    check("idempotency replay (no double debit)", j(r).get("duplicate") is True and j(r).get("reference") == ref1, str(j(r)))

    # The app sends numeric codes: network "1".."4", plan type "1" (SME) / "3" (gifting).
    r = post("/api/utility/get_data_plans/", {"datanetwork": "1", "selectedPlanType": "1"})
    plans = j(r).get("data_plans") or []
    if check("data plans list", bool(plans), str(j(r))[:120]):
        r = post("/api/utility/buydata/", {"datanetwork": "1", "selectedDataPlan": plans[0]["plan_code"],
                                           "phone": phone_a, "transaction_pin": "1234",
                                           "idempotency_key": f"e2e-{uuid.uuid4().hex[:12]}"}, token=tok)
        check("buy data", j(r).get("success") is True, f"{r.status_code} {j(r)}")

    # The app sends a numeric disco id ("1" = Ikeja); the view maps it to the
    # provider slug. Authenticated — the app routes it through apiPost. The app
    # branches on HTTP status and reads customer_name/name (no success field).
    r = post("/api/utility/validate_meter/", {"meter": "04123456789", "disco": "1", "meter_type": "prepaid"}, token=tok)
    check("validate meter (mock)", r.status_code == 200 and (j(r).get("customer_name") or j(r).get("name")), f"{r.status_code} {j(r)}")
    r = post("/api/utility/buyelectricity/", {"disco": "1", "meter_type": "prepaid", "meter": "04123456789",
                                              "amount": "1000", "transaction_pin": "1234",
                                              "idempotency_key": f"e2e-{uuid.uuid4().hex[:12]}"}, token=tok)
    check("buy electricity", j(r).get("success") is True, f"{r.status_code} {j(r)}")

    r = post("/api/utility/get_cable_plans/", {"cablenetwork": "1"})
    cplans = j(r).get("cable_plans") or []
    if check("cable plans list", bool(cplans), str(j(r))[:120]):
        code = cplans[0]["cable_plan_code"]
        r = post("/api/utility/get_cable_plans_price/", {"cable_plan_code": code})
        check("cable plan price", j(r).get("cable_plans_price") is not None, str(j(r)))
        r = post("/api/utility/validate_iuc/", {"iuc": "70231234567", "cablenetwork": "1"}, token=tok)
        check("validate IUC (mock)", r.status_code == 200 and (j(r).get("customer_name") or j(r).get("name")), f"{r.status_code} {j(r)}")
        r = post("/api/utility/buycable/", {"iuc": "70231234567", "cablenetwork": "1", "selectedcablePlan": code,
                                            "transaction_pin": "1234",
                                            "idempotency_key": f"e2e-{uuid.uuid4().hex[:12]}"}, token=tok)
        check("buy cable", j(r).get("success") is True, f"{r.status_code} {j(r)}")

    # second user for an internal transfer
    tok_b, err = signup(phone_b, f"bola{suffix}@e2e.test", first="Bola", last="Peer")
    check("second user signup", bool(tok_b), err or "")
    r = post("/api/transfer/resolve/", {"identifier": phone_b}, token=tok)
    check("zitch transfer resolve", j(r).get("success") is True and j(r).get("name"), str(j(r)))
    r = post("/api/transfer/send/", {"identifier": phone_b, "amount": "1500", "transaction_pin": "1234",
                                     "note": "e2e", "idempotency_key": f"e2e-{uuid.uuid4().hex[:12]}"}, token=tok)
    check("zitch transfer send", j(r).get("success") is True, f"{r.status_code} {j(r)}")
    bal_b = float(j(post("/api/wallet_balance/", token=tok_b))["wallet"])
    check("recipient credited", bal_b == 1500, f"recipient balance={bal_b}")

    r = post("/api/transfers/banks/")
    banks = j(r).get("banks") or []
    check("bank list", bool(banks), str(j(r))[:120])
    r = post("/api/transfers/resolve/", {"account_number": "0123456789", "bank": banks[0]["code"]}, token=tok)
    name = j(r).get("name")
    check("bank account resolve (mock)", j(r).get("success") is True and name, str(j(r)))
    r = post("/api/transfers/send/", {"account_number": "0123456789", "bank": banks[0]["code"], "name": name,
                                      "amount": "2000", "transaction_pin": "1234",
                                      "idempotency_key": f"e2e-{uuid.uuid4().hex[:12]}"}, token=tok)
    check("bank transfer send", j(r).get("success") is True or j(r).get("pending") is True, f"{r.status_code} {j(r)}")

    r = post("/api/savings/rates/")
    check("savings rates", bool(j(r).get("rates")), str(j(r))[:120])
    r = post("/api/savings/create/", {"amount": "5000", "days": 30, "transaction_pin": "1234",
                                      "idempotency_key": f"e2e-{uuid.uuid4().hex[:12]}"}, token=tok)
    check("savings create", j(r).get("success") is True, f"{r.status_code} {j(r)}")
    r = post("/api/savings/list/", token=tok)
    check("savings list", len(j(r).get("plans") or []) == 1, str(j(r))[:160])

    r = post("/api/loans/status/", token=tok)
    check("loan status", "available" in j(r), str(j(r))[:160])
    r = post("/api/loans/request/", {"amount": "10000", "tenure_days": 30, "transaction_pin": "1234"}, token=tok)
    check("loan request (instant disbursal)", j(r).get("success") is True, f"{r.status_code} {j(r)}")
    r = post("/api/loans/repay/", {"amount": "1000", "transaction_pin": "1234",
                                   "idempotency_key": f"e2e-{uuid.uuid4().hex[:12]}"}, token=tok)
    check("loan repay", j(r).get("success") is True, f"{r.status_code} {j(r)}")

    r = post("/api/cards/create/", token=tok)
    check("card create", j(r).get("success") is True, f"{r.status_code} {j(r)}")
    r = post("/api/cards/fund/", {"amount": "1000", "transaction_pin": "1234",
                                  "idempotency_key": f"e2e-{uuid.uuid4().hex[:12]}"}, token=tok)
    check("card fund", j(r).get("success") is True, f"{r.status_code} {j(r)}")
    r = post("/api/cards/freeze/", token=tok)
    check("card freeze toggle", j(r).get("success") is True, str(j(r)))
    r = post("/api/cards/details/", {"transaction_pin": "1234"}, token=tok)
    check("card details (PIN-gated reveal)", j(r).get("success") is True and j(r).get("pan"), str(j(r))[:120])

    # The converter proxies a keyless external API (open.er-api.com); when that
    # host is unreachable (sandboxed/offline envs) the endpoint degrades to a
    # clean failure message, which the app surfaces. Accept either outcome.
    r = post("/api/convert/fx/", token=tok)
    fx_body = j(r)
    fx_ok = fx_body.get("success") is True and fx_body.get("currencies")
    fx_offline = r.status_code in (502, 503) and "rates" in str(fx_body.get("message", "")).lower()
    check("convert fx rates (live or clean offline degrade)", bool(fx_ok or fx_offline),
          f"{r.status_code} {fx_body}")

    r = post("/api/exams/list/")
    exams = j(r).get("exams") or []
    if check("exams list", bool(exams), str(j(r))[:120]):
        r = post("/api/exams/buy/", {"exam": exams[0]["code"], "quantity": 1, "phone": phone_a,
                                     "transaction_pin": "1234",
                                     "idempotency_key": f"e2e-{uuid.uuid4().hex[:12]}"}, token=tok)
        check("buy exam PIN", j(r).get("success") is True, f"{r.status_code} {j(r)}")
    r = post("/api/betting/list/")
    platforms = j(r).get("platforms") or []
    if check("betting platforms", bool(platforms), str(j(r))[:120]):
        r = post("/api/betting/fund/", {"platform": platforms[0]["code"], "user_id": "E2E-9001",
                                        "amount": "500", "transaction_pin": "1234",
                                        "idempotency_key": f"e2e-{uuid.uuid4().hex[:12]}"}, token=tok)
        # Baxi's betting API isn't wired yet: live mode fails-safe (fail+refund),
        # mock mode succeeds. Accept either documented outcome.
        check("betting fund (mock or fail-safe refund)",
              j(r).get("success") is True or r.status_code == 502, f"{r.status_code} {j(r)}")

    r = post("/api/user-transaction-history/", token=tok)
    hist = j(r).get("all_site_transactions") or []
    check("transaction history populated", len(hist) >= 6, f"{len(hist)} rows")

    wrong = post("/api/transfer/send/", {"identifier": phone_b, "amount": "10", "transaction_pin": "9999",
                                         "idempotency_key": f"e2e-{uuid.uuid4().hex[:12]}"}, token=tok)
    check("wrong PIN rejected", wrong.status_code == 403 and "pin" in str(j(wrong)).lower(), f"{wrong.status_code} {j(wrong)}")

    # =================================================================== B
    print("\n[B] Ops portal API (/api/ops/)")
    r = post("/api/ops/login/", {"identifier": "dapo", "password": "Operator#1"})
    fin = j(r).get("token")
    check("ops login (finance)", bool(fin) and j(r).get("role") == "finance", str(j(r))[:160])
    for path in ("summary", "users", "transactions", "fx", "products", "kyc-queue",
                 "broadcasts", "ai", "audit", "recon", "settings", "inbox"):
        r = post(f"/api/ops/{path}/", {}, token=fin)
        check(f"ops {path}", r.status_code == 200, f"{r.status_code} {str(j(r))[:100]}")
    r = post("/api/ops/fx-margin/", {"bps": 75}, token=fin)
    check("ops fx-margin set", j(r).get("margin") == 75, str(j(r)))
    r = post("/api/ops/fx-corridor/", {"currency": "USD", "enabled": False}, token=fin)
    check("ops fx-corridor pause", j(r).get("success") is True, str(j(r)))
    fxr = j(post("/api/ops/fx/", {}, token=fin))
    usd = next(x for x in fxr["rates"] if x["pair"] == "NGN/USD")
    check("ops fx reflects paused corridor", usd["settle"] is False, str(usd))
    post("/api/ops/fx-corridor/", {"currency": "USD", "enabled": True}, token=fin)
    r = post("/api/ops/run-maturities/", {}, token=fin)
    check("ops run-maturities", "paid_out" in j(r), str(j(r)))
    r = post("/api/ops/run-recon/", {}, token=fin)
    check("ops run-recon", "settled" in j(r), str(j(r)))
    uid_b = User.objects.get(phone=phone_b).id
    r = post("/api/ops/user-action/", {"user_id": uid_b, "action": "unlock_pin"}, token=fin)
    check("ops user-action unlock_pin", j(r).get("success") is True, str(j(r)))
    r = post("/api/ops/login/", {"identifier": "funmi", "password": "Operator#1"})
    sup = j(r).get("token")
    check("ops login (support)", bool(sup) and j(r).get("role") == "support", str(j(r))[:160])
    r = post("/api/ops/fx-margin/", {"bps": 60}, token=sup)
    check("ops RBAC: support blocked from fx-margin", r.status_code == 403, f"{r.status_code}")
    r = post("/api/ops/ai-global/", {"enabled": True}, token=fin)
    check("ops RBAC: finance blocked from ai-global", r.status_code == 403, f"{r.status_code}")

    # =================================================================== D (whatsapp ops while we have tokens)
    print("\n[D] WhatsApp ops + webhook")
    msisdn = "234" + phone_a[1:]
    r = post("/api/whatsapp/ops/handover/", {"msisdn": msisdn}, token=sup)
    check("wa ops handover (support)", j(r).get("status") == "human", f"{r.status_code} {j(r)}")
    r = post("/api/whatsapp/ops/reply/", {"msisdn": msisdn, "text": "Agent here — how can we help?"}, token=sup)
    check("wa ops reply (support)", j(r).get("success") is True, f"{r.status_code} {j(r)}")
    r = post("/api/whatsapp/ops/return-to-bot/", {"msisdn": msisdn}, token=sup)
    check("wa ops return-to-bot", j(r).get("status") == "bot", str(j(r)))
    r = post("/api/whatsapp/ops/broadcast/", {"template_name": "e2e_check", "category": "utility"}, token=sup)
    check("wa ops broadcast (support)", j(r).get("success") is True, f"{r.status_code} {j(r)}")
    r = post("/api/whatsapp/ops/broadcast/", {"template_name": "e2e_check"}, token=fin)
    check("wa ops RBAC: finance blocked from broadcast", r.status_code == 403, f"{r.status_code}")
    event = {"entry": [{"changes": [{"value": {"messages": [
        {"from": msisdn, "id": f"e2e-{uuid.uuid4().hex}", "type": "text", "text": {"body": "menu"}}]}}]}]}
    r = requests.post(BASE + "/webhooks/whatsapp", json=event, timeout=30)
    check("wa webhook inbound accepted", r.status_code == 200, f"{r.status_code} {r.text[:80]}")

    # =================================================================== C
    print("\n[C] Console admin API (/api/admin/)")
    r = post("/api/admin/login", {"username": "amara", "password": "Operator#1"})
    adm = j(r).get("token")
    check("admin login (super_admin)", bool(adm) and j(r).get("role") == "super_admin", str(j(r))[:160])
    r = post("/api/admin/login", {"username": "dapo", "password": "Operator#1"})
    afin = j(r).get("token")
    r = post("/api/admin/login", {"username": "funmi", "password": "Operator#1"})
    asup = j(r).get("token")
    r = post("/api/admin/login", {"username": "ada", "password": "Operator#1"})
    aro = j(r).get("token")
    check("admin logins (finance/support/read_only)", all([afin, asup, aro]), "")

    r = get("/api/admin/me", token=asup)
    check("admin me caps", "wa" in (j(r).get("can") or []), str(j(r)))
    boot = j(get("/api/admin/bootstrap", token=aro))
    need = {"users", "txns", "convos", "broadcasts", "audit", "rates", "float", "providers",
            "volume_14d", "loans", "savings", "cards", "kycq", "team", "perms", "settings", "kpis"}
    check("admin bootstrap shape", need <= set(boot), str(sorted(set(boot)))[:200])
    check("admin bootstrap rates carry provider+customer", all("provider" in x and "customer" in x for x in boot["rates"]), str(boot["rates"])[:160])
    check("admin bootstrap kpis incl. wa_optin/matured_due", "wa_optin" in boot["kpis"] and "matured_due" in boot["kpis"], str(boot["kpis"]))
    check("admin bootstrap cards carry cid", all("cid" in c for c in boot["cards"]), str(boot["cards"])[:160])

    r = post("/api/admin/users/status", {"uid": uid_b, "status": "frozen"}, token=afin)
    check("admin users/status freeze", j(r).get("status") == "frozen", f"{r.status_code} {j(r)}")
    r = post("/api/admin/users/status", {"uid": uid_b, "status": "active"}, token=afin)
    check("admin users/status unfreeze", j(r).get("status") == "active", str(j(r)))
    r = post("/api/admin/users/pin_unlock", {"uid": uid_b}, token=afin)
    check("admin users/pin_unlock", j(r).get("success") is True, f"{r.status_code} {j(r)}")
    r = post("/api/admin/kyc/review", {"uid": uid_b, "decision": "approve", "type": "bvn"}, token=afin)
    check("admin kyc/review approve", j(r).get("success") is True and j(r).get("tier", 0) >= 1, f"{r.status_code} {j(r)}")

    ref = boot["txns"][0]["id"] if boot["txns"] else None
    r = post("/api/admin/txn/flag", {"ref": ref, "flagged": True}, token=afin)
    check("admin txn/flag", j(r).get("flagged") is True, f"{r.status_code} {j(r)}")
    r = post("/api/admin/txn/flag", {"ref": ref, "flagged": False}, token=afin)
    check("admin txn release returns display status", j(r).get("flagged") is False and j(r).get("status"), str(j(r)))
    r = post("/api/admin/txn/requery", {"ref": ref}, token=afin)
    check("admin txn/requery guards settled rows", r.status_code == 409, f"{r.status_code} {j(r)}")

    r = post("/api/admin/fx/margin", {"bps": 70}, token=afin)
    check("admin fx/margin", j(r).get("margin") == 70, str(j(r)))
    r = post("/api/admin/fx/corridor", {"currency": "GBP", "enabled": False}, token=afin)
    check("admin fx/corridor pause", j(r).get("success") is True, str(j(r)))
    boot2 = j(get("/api/admin/bootstrap", token=afin))
    gbp = next(x for x in boot2["rates"] if x["pair"] == "NGN/GBP")
    check("admin bootstrap reflects paused corridor", gbp["settle"] is False, str(gbp))
    post("/api/admin/fx/corridor", {"currency": "GBP", "enabled": True}, token=afin)
    r = post("/api/admin/fx/corridor", {"currency": "CNY", "enabled": True}, token=afin)
    check("admin fx/corridor CNY locked", r.status_code == 400, f"{r.status_code}")

    loan_ref = next((l["ref"] for l in boot["loans"] if l.get("ref")), None)
    if loan_ref:
        r = post("/api/admin/loans/remind", {"ref": loan_ref}, token=afin)
        check("admin loans/remind (409 without WA link is correct)",
              r.status_code in (200, 409), f"{r.status_code} {j(r)}")
    r = post("/api/admin/ops/maturities", {}, token=afin)
    check("admin ops/maturities", "paid_out" in j(r), str(j(r)))
    r = post("/api/admin/ops/recon", {}, token=afin)
    check("admin ops/recon", "settled" in j(r), str(j(r)))

    cid = boot["cards"][0]["id"] if boot["cards"] else None  # prefixed form on purpose
    if cid:
        r = post("/api/admin/cards/freeze", {"card_id": cid, "status": "frozen"}, token=afin)
        check("admin cards/freeze accepts cd_ id", j(r).get("status") == "frozen", f"{r.status_code} {j(r)}")
        post("/api/admin/cards/freeze", {"card_id": cid, "status": "active"}, token=afin)

    r = post("/api/admin/wa/handover", {"msisdn": msisdn, "mode": "human"}, token=asup)
    check("admin wa/handover", j(r).get("status") == "human", f"{r.status_code} {j(r)}")
    r = post("/api/admin/wa/reply", {"msisdn": msisdn, "text": "Console agent reply"}, token=asup)
    check("admin wa/reply", j(r).get("success") is True, f"{r.status_code} {j(r)}")
    r = post("/api/admin/wa/conv_ai", {"msisdn": msisdn, "enabled": True}, token=asup)
    check("admin wa/conv_ai", j(r).get("enabled") is True, str(j(r)))
    r = post("/api/admin/wa/handover", {"msisdn": msisdn, "mode": "bot"}, token=asup)
    check("admin wa/handover back to bot", j(r).get("status") == "bot", str(j(r)))
    r = post("/api/admin/wa/broadcast", {"template_name": "e2e_console", "category": "utility"}, token=asup)
    check("admin wa/broadcast returns row", (j(r).get("broadcast") or {}).get("template") == "e2e_console", f"{r.status_code} {j(r)}")

    r = post("/api/admin/settings/update", {"key": "ai_enabled_global", "value": "true"}, token=adm)
    check("admin settings/update (super_admin)", j(r).get("success") is True, str(j(r)))

    # --- feature endpoints: customer 360, server search, broadcast detail,
    # webhook/recon history, manual credit ---
    uid_a = User.objects.get(phone=phone_a).id
    r = post("/api/admin/users/detail", {"uid": uid_a}, token=aro)
    d = j(r)
    check("admin users/detail 360", d.get("user", {}).get("uid") == uid_a and len(d.get("txns", [])) > 0
          and "loans" in d and "cards" in d and "pin_locked" in d, f"{r.status_code} {str(d)[:160]}")
    r = post("/api/admin/users/search", {"q": phone_b}, token=aro)
    check("admin users/search by phone", any(u["uid"] == uid_b for u in j(r).get("rows", [])), str(j(r))[:160])
    r = post("/api/admin/txn/search", {"type": "airtime"}, token=aro)
    check("admin txn/search type filter", len(j(r).get("rows", [])) >= 1
          and all(t["type"] == "airtime" for t in j(r)["rows"]), str(j(r))[:160])
    r = post("/api/admin/txn/search", {"q": phone_a}, token=aro)
    check("admin txn/search by user", len(j(r).get("rows", [])) >= 3, str(j(r))[:120])
    r = post("/api/admin/audit/search", {"q": "wallet.manual_credit"}, token=aro)
    audit_before = len(j(r).get("rows", []))
    check("admin audit/search reachable", r.status_code == 200, f"{r.status_code}")

    bid = j(post("/api/admin/wa/broadcast", {"template_name": "e2e_detail", "category": "utility"}, token=asup))["broadcast"]["id"]
    r = post("/api/admin/wa/broadcast_detail", {"id": bid}, token=aro)
    det = j(r)
    check("admin wa/broadcast_detail", det.get("broadcast", {}).get("template") == "e2e_detail"
          and isinstance(det.get("recipients"), list), f"{r.status_code} {str(det)[:160]}")

    boot3 = j(get("/api/admin/bootstrap", token=aro))
    check("admin bootstrap recons populated after runs",
          any(rr["run"] in ("zitch-reconcile-vtu", "zitch-maturities") for rr in boot3.get("recons", [])),
          str(boot3.get("recons"))[:160])

    bal_before = float(j(post("/api/wallet_balance/", token=tok_b))["wallet"])
    mc_key = f"e2e-mc-{uuid.uuid4().hex[:10]}"
    r = post("/api/admin/wallet/credit", {"uid": uid_b, "amount": "750",
                                          "reason": "E2E goodwill credit", "idempotency_key": mc_key}, token=afin)
    check("admin wallet/credit", j(r).get("success") is True and j(r).get("reference"), f"{r.status_code} {j(r)}")
    bal_after = float(j(post("/api/wallet_balance/", token=tok_b))["wallet"])
    check("manual credit reflected in user's app balance", bal_after == bal_before + 750,
          f"{bal_before} -> {bal_after}")
    r = post("/api/admin/wallet/credit", {"uid": uid_b, "amount": "750",
                                          "reason": "E2E goodwill credit", "idempotency_key": mc_key}, token=afin)
    check("manual credit idempotent replay", j(r).get("duplicate") is True, str(j(r)))
    bal_dup = float(j(post("/api/wallet_balance/", token=tok_b))["wallet"])
    check("replay did not double-credit", bal_dup == bal_after, f"{bal_after} -> {bal_dup}")
    r = post("/api/admin/wallet/credit", {"uid": uid_b, "amount": "100", "reason": "ok"}, token=afin)
    check("manual credit requires a real reason", r.status_code == 400, f"{r.status_code}")
    r = post("/api/admin/wallet/credit", {"uid": uid_b, "amount": "100",
                                          "reason": "support cannot do this"}, token=asup)
    check("admin RBAC: support blocked from wallet/credit", r.status_code == 403, f"{r.status_code}")
    r = post("/api/admin/audit/search", {"q": "wallet.manual_credit"}, token=aro)
    check("manual credit audited", len(j(r).get("rows", [])) == audit_before + 1, str(j(r))[:120])

    for nm, tk, path, body in (
        ("support blocked from fx/margin", asup, "/api/admin/fx/margin", {"bps": 50}),
        ("finance blocked from wa/reply", afin, "/api/admin/wa/reply", {"msisdn": msisdn, "text": "x"}),
        ("finance blocked from settings", afin, "/api/admin/settings/update", {"key": "ai_enabled_global", "value": "false"}),
        ("read_only blocked from writes", aro, "/api/admin/users/status", {"uid": uid_b, "status": "frozen"}),
    ):
        r = post(path, body, token=tk)
        check(f"admin RBAC: {nm}", r.status_code == 403, f"{r.status_code} {j(r)}")
    r = get("/api/admin/bootstrap")
    check("admin bootstrap rejects anonymous", r.status_code == 401, f"{r.status_code}")

    # =================================================================== E
    print("\n[E] Web surfaces")
    for path, marker in (("/", "Zitch"), ("/portal/", "html"), ("/prototype/", "html"),
                         ("/console/", "Zitch"), ("/console/app/", "html"), ("/console/portal/", "root"),
                         ("/healthz", "zitch-api"), ("/readyz", "db")):
        r = get(path)
        check(f"GET {path}", r.status_code == 200 and marker.lower() in r.text.lower(),
              f"{r.status_code} len={len(r.text)}")
    r = get("/static/console/portal/views-a.jsx")
    check("console portal static served", r.status_code == 200 and "doAct" in r.text, f"{r.status_code}")
    r = get("/static/portal/admin/api.js")
    check("ops portal static served", r.status_code == 200, f"{r.status_code}")

    report()


def report():
    print(f"\n{'=' * 60}\nE2E RESULT: {len(PASSED)} passed, {len(FAILED)} failed")
    for f in FAILED:
        print(f"  FAILED: {f}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
