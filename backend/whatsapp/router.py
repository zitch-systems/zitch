"""Deterministic WhatsApp router (slice 1).

No LLM here — keyword + numbered-menu + slot-filling that drives the same money
services the app uses (balance, NGN bank transfer with name-enquiry, confirm,
PIN, idempotency). The LLM intent layer (later) sits *in front* of this and
hands it the same structured actions, so money never depends on the AI being up.
"""
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from common.http import evaluate_transaction_pin, send_limit_error
from transfers.models import Bank
from transfers.services import PayoutError, execute_payout
from utility.models import CablePlan, DataPlan
from utility.providers import disbursement_resolve_account, vtu_purchase, vtu_verify_customer
from utility.views import CABLE_NAMES, DISCO_NAMES, NETWORK_NAMES
from wallet.services import (
    DuplicateTransaction,
    InsufficientFunds,
    get_or_create_wallet,
    run_provider_purchase,
)

from . import ai
from .models import PendingAction, SystemSetting, WaMessageLog, WhatsAppLink
from .providers import send_text

FLOW_TTL = timedelta(minutes=5)        # idle window for an in-progress flow
PIN_FLOW_ATTEMPTS = 2                   # 1 retry then cancel (spec §7)

MENU = (
    "Zitch • Reply with a number:\n"
    "1  Check balance\n"
    "2  Send money\n"
    "3  Airtime / Data\n"
    "4  Pay a bill\n"
    "5  Convert currency\n\n"
    "Or just type, e.g. \"send 5k\". Reply \"cancel\" anytime."
)
UNLINKED = (
    "I couldn't match this number to a Zitch account. Open the Zitch app → "
    "Settings → Link WhatsApp to get a code, then send it here."
)


# --------------------------------------------------------------------------- #
# messaging + small parsers
# --------------------------------------------------------------------------- #
def reply(msisdn: str, text: str) -> None:
    """Send a message and record it (the OUT audit row; never contains a PIN)."""
    send_text(msisdn, text)
    WaMessageLog.objects.create(msisdn=msisdn, direction=WaMessageLog.OUT, text=text)


def active_link_for(msisdn: str) -> WhatsAppLink | None:
    return WhatsAppLink.objects.filter(wa_msisdn=msisdn, status=WhatsAppLink.ACTIVE).first()


def is_awaiting_pin(msisdn: str) -> bool:
    """True if the current flow expects a PIN next — so the webhook masks it."""
    pa = _current_action(msisdn)
    return bool(pa and pa.state == "pin")


def parse_amount(text: str) -> Decimal | None:
    """Nigerian shorthand → amount. '5k'→5000, '2m'→2_000_000, '1,500'→1500."""
    t = text.strip().lower().replace(",", "").replace("₦", "").replace("ngn", "").strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([km])?", t)
    if not m:
        return None
    try:
        val = Decimal(m.group(1))
    except InvalidOperation:
        return None
    val *= {"k": 1000, "m": 1_000_000}.get(m.group(2), 1)
    return val if val > 0 else None


def _money(amount: Decimal) -> str:
    return f"₦{amount:,.2f}"


# --------------------------------------------------------------------------- #
# pending-action helpers (one in-progress flow per number)
# --------------------------------------------------------------------------- #
def _current_action(msisdn: str) -> PendingAction | None:
    PendingAction.objects.filter(msisdn=msisdn, expires_at__lt=timezone.now()).delete()
    return PendingAction.objects.filter(msisdn=msisdn).order_by("-created").first()


def _clear_actions(msisdn: str) -> None:
    PendingAction.objects.filter(msisdn=msisdn).delete()


def _touch(pa: PendingAction, **fields) -> None:
    pa.expires_at = timezone.now() + FLOW_TTL
    for k, v in fields.items():
        setattr(pa, k, v)
    pa.save()


# --------------------------------------------------------------------------- #
# entry point (called by the webhook, after dedupe)
# --------------------------------------------------------------------------- #
def handle_inbound(msisdn: str, text: str) -> None:
    text = (text or "").strip()
    link = active_link_for(msisdn)
    if link is None:
        return _handle_unlinked(msisdn, text)

    user = link.user
    low = text.lower()

    if low in ("cancel", "stop", "quit"):
        _clear_actions(msisdn)
        return reply(msisdn, "Okay, cancelled. Reply \"menu\" for options.")

    # An in-progress flow consumes the message before any fresh command —
    # except an explicit menu/help reset.
    if low in ("menu", "hi", "hello", "start", "help"):
        _clear_actions(msisdn)
        return reply(msisdn, MENU)

    pa = _current_action(msisdn)
    if pa is not None:
        return _advance(pa, user, msisdn, text)

    # Fresh command (keyword or menu number).
    if low in ("balance", "bal", "1"):
        return _do_balance(user, msisdn)
    if low in ("2", "transfer", "send", "send money"):
        return _start_transfer(user, msisdn)
    if low == "airtime":
        return _start_airtime(user, msisdn)
    if low == "data":
        return _start_data(user, msisdn)
    if low == "3":
        return reply(msisdn, "Reply \"airtime\" or \"data\".")
    if low in ("electricity", "light", "nepa", "power"):
        return _start_electricity(user, msisdn)
    if low in ("cable", "tv", "dstv", "gotv", "startimes"):
        return _start_cable(user, msisdn)
    if low in ("4", "bill", "bills", "pay bill"):
        return reply(msisdn, "Reply \"electricity\" or \"cable\".")
    if low in ("5", "convert", "conversion"):
        return reply(msisdn, "Currency conversion on WhatsApp is coming soon. Use the Zitch app for now.")

    # Try a one-line paste: "0123456789 GTBank John Doe 5000".
    if _start_transfer_from_paste(user, msisdn, text):
        return
    # Free-form text: let the AI route it (when active) — but the deterministic
    # paths above always win, so core flows never depend on the AI being up.
    if ai_active(link):
        intent = ai.extract_intent(text)
        if intent:
            _record_intent(msisdn, intent)
            if intent.get("name") != "clarify" and dispatch_intent(user, msisdn, intent):
                return
    return reply(msisdn, "Sorry, I didn't get that.\n\n" + MENU)


# --------------------------------------------------------------------------- #
# linking
# --------------------------------------------------------------------------- #
def _handle_unlinked(msisdn: str, text: str) -> None:
    code = text.strip().upper()
    if code.startswith("LINK "):
        code = code[5:]
    code = re.sub(r"[^A-Z0-9]", "", code)
    link = (
        WhatsAppLink.objects.filter(
            status=WhatsAppLink.PENDING, link_code=code, expires_at__gt=timezone.now()
        ).first()
        if code else None
    )
    if link is None:
        return reply(msisdn, UNLINKED)
    link.wa_msisdn = msisdn
    link.status = WhatsAppLink.ACTIVE
    link.link_code = ""
    link.linked_at = timezone.now()
    link.save(update_fields=["wa_msisdn", "status", "link_code", "linked_at"])
    name = (link.user.first_name or "there").strip()
    reply(msisdn, f"✅ Linked! Hi {name}, your WhatsApp is now connected to Zitch.\n\n" + MENU)


# --------------------------------------------------------------------------- #
# balance
# --------------------------------------------------------------------------- #
def _do_balance(user, msisdn: str) -> None:
    wallet = get_or_create_wallet(user)
    reply(msisdn, f"💰 Your Zitch balance is {_money(wallet.balance)}.")


# --------------------------------------------------------------------------- #
# transfer (slot-filling state machine)
# --------------------------------------------------------------------------- #
def _start_transfer(user, msisdn: str) -> None:
    _clear_actions(msisdn)
    PendingAction.objects.create(
        user=user, msisdn=msisdn, action_type="transfer", state="amount",
        payload={"pin_attempts": 0}, expires_at=timezone.now() + FLOW_TTL,
    )
    reply(msisdn, "How much would you like to send? (e.g. 5000 or 5k)")


def _advance(pa: PendingAction, user, msisdn: str, text: str) -> None:
    handler = {
        "transfer": _advance_transfer,
        "airtime": _advance_airtime,
        "data": _advance_data,
        "electricity": _advance_electricity,
        "cable": _advance_cable,
    }.get(pa.action_type)
    if handler is None:
        _clear_actions(msisdn)
        return reply(msisdn, MENU)
    return handler(pa, user, msisdn, text)


def _advance_transfer(pa: PendingAction, user, msisdn: str, text: str) -> None:
    state = pa.state

    if state == "amount":
        amount = parse_amount(text)
        if amount is None or amount < 10:
            return reply(msisdn, "Please enter a valid amount, at least ₦10 (e.g. 5000 or 5k).")
        limit_msg = send_limit_error(user, amount)
        if limit_msg:
            _clear_actions(msisdn)
            return reply(msisdn, limit_msg)
        if get_or_create_wallet(user).balance < amount:
            return reply(msisdn, f"Insufficient balance. You have {_money(get_or_create_wallet(user).balance)}. "
                                 "Enter a lower amount, or \"cancel\".")
        pa.payload["amount"] = str(amount)
        _touch(pa, state="account", payload=pa.payload)
        return reply(msisdn, "Enter the recipient's 10-digit account number.")

    if state == "account":
        acct = re.sub(r"\D", "", text)
        if len(acct) != 10:
            return reply(msisdn, "That doesn't look like a 10-digit account number. Please try again.")
        pa.payload["account"] = acct
        _touch(pa, state="bank", payload=pa.payload)
        return reply(msisdn, "Which bank? Type the bank name (e.g. GTBank, Access, Opay).")

    if state == "bank":
        matches = _match_banks(text)
        if not matches:
            return reply(msisdn, "I couldn't find that bank. Type the name again, or \"cancel\".")
        if len(matches) > 1:
            pa.payload["bank_choices"] = [b.code for b in matches[:6]]
            _touch(pa, state="bank_pick", payload=pa.payload)
            lines = "\n".join(f"{i+1}  {b.name}" for i, b in enumerate(matches[:6]))
            return reply(msisdn, "I found a few — reply with the number:\n" + lines)
        return _resolve_and_confirm(pa, user, msisdn, matches[0])

    if state == "bank_pick":
        choices = pa.payload.get("bank_choices", [])
        try:
            idx = int(text.strip()) - 1
        except ValueError:
            return reply(msisdn, "Reply with the number of the bank from the list, or \"cancel\".")
        if not (0 <= idx < len(choices)):
            return reply(msisdn, "That number isn't on the list. Try again, or \"cancel\".")
        bank = Bank.objects.filter(code=choices[idx]).first()
        if bank is None:
            _clear_actions(msisdn)
            return reply(msisdn, "Something went wrong picking that bank. Reply \"menu\" to start over.")
        return _resolve_and_confirm(pa, user, msisdn, bank)

    if state == "pin":
        return _try_pin(pa, user, msisdn, text)

    _clear_actions(msisdn)
    return reply(msisdn, MENU)


def _match_banks(text: str) -> list:
    t = text.strip().lower()
    banks = list(Bank.objects.filter(active=True))
    exact = [b for b in banks if b.name.lower() == t]
    if exact:
        return exact
    return [b for b in banks if t and (t in b.name.lower() or b.name.lower() in t)]


def _resolve_and_confirm(pa: PendingAction, user, msisdn: str, bank) -> None:
    """Name-enquiry against the bank, then show the confirm card and await PIN."""
    acct = pa.payload["account"]
    res = disbursement_resolve_account(acct, bank.bank_code)
    if not res.get("success"):
        _touch(pa, state="account")
        return reply(msisdn, f"Couldn't verify that account at {bank.name}. "
                             "Re-enter the 10-digit account number, or \"cancel\".")
    name = (res.get("name") or "").strip() or "Bank recipient"
    amount = Decimal(pa.payload["amount"])
    pa.payload.update({"bank_code": bank.bank_code, "bank_name": bank.name, "name": name})
    _touch(pa, state="pin", payload=pa.payload)
    reply(
        msisdn,
        "Confirm transfer\n"
        f"{_money(amount)} → {name.upper()}\n"
        f"{bank.name} • {acct}\n"
        "Reply with your PIN to confirm, or \"cancel\".",
    )


def _flow_pin_ok(pa: PendingAction, user, msisdn: str, text: str) -> bool:
    """Shared PIN gate for every money flow: True if correct (caller proceeds);
    otherwise it sends the right message (locked / retry / cancel) and returns
    False. Brute-force lockout is enforced inside evaluate_transaction_pin."""
    ok, code, message = evaluate_transaction_pin(user, text)
    if ok:
        return True
    if code == "pin_locked":
        _clear_actions(msisdn)
        reply(msisdn, message)
        return False
    attempts = int(pa.payload.get("pin_attempts", 0)) + 1
    if attempts >= PIN_FLOW_ATTEMPTS:
        _clear_actions(msisdn)
        reply(msisdn, "Too many wrong PIN attempts. Cancelled — reply \"menu\" to start over.")
        return False
    pa.payload["pin_attempts"] = attempts
    _touch(pa, payload=pa.payload)
    reply(msisdn, f"{message} Reply with your PIN, or \"cancel\".")
    return False


def _try_pin(pa: PendingAction, user, msisdn: str, text: str) -> None:
    if not _flow_pin_ok(pa, user, msisdn, text):
        return
    amount = Decimal(pa.payload["amount"])
    bank = Bank.objects.filter(bank_code=pa.payload["bank_code"]).first()
    if bank is None:
        _clear_actions(msisdn)
        return reply(msisdn, "Something went wrong. Reply \"menu\" to start over.")
    try:
        # Stable key per flow: a re-sent "pin" message can't double-pay.
        txn = execute_payout(
            user, amount, pa.payload["account"], bank, pa.payload["name"],
            idempotency_key=f"wa-{pa.id}",
        )
    except PayoutError as exc:
        _clear_actions(msisdn)
        if exc.kind == "insufficient":
            return reply(msisdn, "Insufficient balance — transfer cancelled.")
        if exc.kind == "duplicate":
            return reply(msisdn, "That transfer was already processed.")
        return reply(msisdn, f"Transfer failed: {exc.message}")

    _clear_actions(msisdn)
    wallet = get_or_create_wallet(user)
    reply(
        msisdn,
        f"✅ Sent {_money(amount)} to {pa.payload['name'].upper()} ({pa.payload['bank_name']}).\n"
        f"Ref {txn.reference}. New balance: {_money(wallet.balance)}.",
    )


def _start_transfer_from_paste(user, msisdn: str, text: str) -> bool:
    """Parse "0123456789 GTBank John Doe 5000" → jump straight to name-enquiry.
    Returns True if handled as a transfer paste, else False."""
    tokens = text.split()
    acct = next((re.sub(r"\D", "", t) for t in tokens if len(re.sub(r"\D", "", t)) == 10), None)
    amount = None
    for t in reversed(tokens):
        if len(re.sub(r"\D", "", t)) >= 10:  # skip account- / phone-length tokens
            continue
        amount = parse_amount(t)
        if amount is not None:
            break
    if not acct or amount is None:
        return False
    return _begin_bank_transfer(user, msisdn, amount, acct, text)


def _begin_bank_transfer(user, msisdn: str, amount: Decimal, acct: str, bank_query: str) -> bool:
    """Validate then open a transfer at the bank step — shared by the paste path
    and the LLM. Returns False only when the bank can't be matched (caller decides)."""
    matches = _match_banks(bank_query)
    if not matches:
        return False
    if amount < 10:
        reply(msisdn, "Minimum transfer is ₦10.")
        return True
    limit_msg = send_limit_error(user, amount)
    if limit_msg:
        reply(msisdn, limit_msg)
        return True
    if _insufficient(user, amount):
        reply(msisdn, f"Insufficient balance. You have {_money(get_or_create_wallet(user).balance)}.")
        return True
    pa = _new_flow(user, msisdn, "transfer", "bank",
                   {"amount": str(amount), "account": acct, "pin_attempts": 0})
    if len(matches) == 1:
        _resolve_and_confirm(pa, user, msisdn, matches[0])
    else:
        pa.payload["bank_choices"] = [b.code for b in matches[:6]]
        _touch(pa, state="bank_pick", payload=pa.payload)
        lines = "\n".join(f"{i+1}  {b.name}" for i, b in enumerate(matches[:6]))
        reply(msisdn, "Which bank? Reply with the number:\n" + lines)
    return True


# --------------------------------------------------------------------------- #
# VTU + bills (airtime / data / electricity / cable) — reuse run_provider_purchase
# --------------------------------------------------------------------------- #
NETWORK_PROMPT = "Which network?\n" + "\n".join(f"{k}  {v}" for k, v in NETWORK_NAMES.items())
DISCO_PROMPT = "Which disco?\n" + "\n".join(f"{k}  {v}" for k, v in DISCO_NAMES.items())
CABLE_PROMPT = "Which provider?\n" + "\n".join(f"{k}  {v}" for k, v in CABLE_NAMES.items())


def _new_flow(user, msisdn: str, action_type: str, state: str, payload: dict | None = None) -> PendingAction:
    _clear_actions(msisdn)
    return PendingAction.objects.create(
        user=user, msisdn=msisdn, action_type=action_type, state=state,
        payload=payload or {"pin_attempts": 0}, expires_at=timezone.now() + FLOW_TTL,
    )


def _phone_from(text: str, user) -> str | None:
    """'me' -> the user's own number; else the digits typed (>= 10)."""
    if text.strip().lower() in ("me", "self", "mine"):
        return (user.phone or "").lstrip("+")
    digits = re.sub(r"\D", "", text)
    return digits if len(digits) >= 10 else None


def _insufficient(user, amount: Decimal) -> bool:
    return get_or_create_wallet(user).balance < amount


def _run_vtu(pa: PendingAction, user, msisdn: str, amount: Decimal, label: str,
             provider_call, success_line) -> None:
    """Debit -> provider -> settle via the shared run_provider_purchase, then
    reply with the receipt / processing / failure line."""
    try:
        status, txn, result = run_provider_purchase(
            user, amount, label, pa.payload.get("meta", {}), provider_call,
            idempotency_key=f"wa-{pa.id}",
        )
    except InsufficientFunds:
        _clear_actions(msisdn)
        return reply(msisdn, "Insufficient balance — cancelled.")
    except DuplicateTransaction:
        _clear_actions(msisdn)
        return reply(msisdn, "That request was already processed.")
    _clear_actions(msisdn)
    if status == "success":
        return reply(msisdn, success_line(txn, result))
    if status == "pending":
        return reply(msisdn, f"⏳ Your {label} is processing — we'll confirm shortly. Ref {txn.reference}.")
    return reply(msisdn, f"❌ {label} failed: {result.get('message', 'please try again')}. You were not charged.")


# ---- airtime ----
def _start_airtime(user, msisdn: str) -> None:
    _new_flow(user, msisdn, "airtime", "network")
    reply(msisdn, NETWORK_PROMPT)


def _advance_airtime(pa: PendingAction, user, msisdn: str, text: str) -> None:
    st = pa.state
    if st == "network":
        net = text.strip()
        if net not in NETWORK_NAMES:
            return reply(msisdn, "Reply with the network number.\n" + NETWORK_PROMPT)
        pa.payload["net"] = net
        _touch(pa, state="phone", payload=pa.payload)
        return reply(msisdn, "What phone number? Reply \"me\" to use your own.")
    if st == "phone":
        phone = _phone_from(text, user)
        if not phone:
            return reply(msisdn, "Enter a valid phone number (or \"me\").")
        pa.payload["phone"] = phone
        _touch(pa, state="amount", payload=pa.payload)
        return reply(msisdn, "How much airtime? (e.g. 200)")
    if st == "amount":
        amount = parse_amount(text)
        if amount is None or amount < 50:
            return reply(msisdn, "Enter a valid amount, at least ₦50.")
        if _insufficient(user, amount):
            return reply(msisdn, f"Insufficient balance ({_money(get_or_create_wallet(user).balance)}).")
        net = NETWORK_NAMES[pa.payload["net"]]
        pa.payload["amount"] = str(amount)
        pa.payload["meta"] = {"phone": pa.payload["phone"], "network": pa.payload["net"]}
        _touch(pa, state="pin", payload=pa.payload)
        return reply(msisdn, f"Confirm airtime\n{_money(amount)} {net} → {pa.payload['phone']}\n"
                             "Reply with your PIN to confirm, or \"cancel\".")
    if st == "pin":
        if not _flow_pin_ok(pa, user, msisdn, text):
            return
        amount = Decimal(pa.payload["amount"])
        net = NETWORK_NAMES[pa.payload["net"]]
        phone = pa.payload["phone"]
        return _run_vtu(
            pa, user, msisdn, amount, f"Airtime — {net}",
            lambda ref: vtu_purchase(f"{net.lower()}-airtime",
                                     {"amount": str(amount), "phone": phone}, reference=ref),
            lambda txn, res: f"✅ {_money(amount)} {net} airtime sent to {phone}. Ref {txn.reference}.",
        )
    _clear_actions(msisdn)
    return reply(msisdn, MENU)


# ---- data ----
def _start_data(user, msisdn: str) -> None:
    _new_flow(user, msisdn, "data", "network")
    reply(msisdn, NETWORK_PROMPT)


def _advance_data(pa: PendingAction, user, msisdn: str, text: str) -> None:
    st = pa.state
    if st == "network":
        net = text.strip()
        if net not in NETWORK_NAMES:
            return reply(msisdn, "Reply with the network number.\n" + NETWORK_PROMPT)
        plans = list(DataPlan.objects.filter(network=net, active=True)[:8])
        if not plans:
            _clear_actions(msisdn)
            return reply(msisdn, "No data plans available for that network right now.")
        pa.payload["net"] = net
        pa.payload["plan_choices"] = [p.plan_code for p in plans]
        _touch(pa, state="plan", payload=pa.payload)
        lines = "\n".join(f"{i+1}  {p.name} • {p.validity} • {_money(p.price)}" for i, p in enumerate(plans))
        return reply(msisdn, "Choose a plan:\n" + lines)
    if st == "plan":
        plan = _pick(text, pa.payload.get("plan_choices", []), lambda c: DataPlan.objects.filter(plan_code=c).first())
        if plan is None:
            return reply(msisdn, "Reply with a plan number from the list, or \"cancel\".")
        pa.payload.update({"plan_code": plan.plan_code, "price": str(plan.price), "plan_name": plan.name})
        _touch(pa, state="phone", payload=pa.payload)
        return reply(msisdn, "What phone number? Reply \"me\" to use your own.")
    if st == "phone":
        phone = _phone_from(text, user)
        if not phone:
            return reply(msisdn, "Enter a valid phone number (or \"me\").")
        price = Decimal(pa.payload["price"])
        if _insufficient(user, price):
            _clear_actions(msisdn)
            return reply(msisdn, f"Insufficient balance ({_money(get_or_create_wallet(user).balance)}).")
        net = NETWORK_NAMES[pa.payload["net"]]
        pa.payload["phone"] = phone
        pa.payload["meta"] = {"phone": phone, "network": pa.payload["net"], "plan_code": pa.payload["plan_code"]}
        _touch(pa, state="pin", payload=pa.payload)
        return reply(msisdn, f"Confirm data\n{pa.payload['plan_name']} ({net}) → {phone}\n{_money(price)}\n"
                             "Reply with your PIN to confirm, or \"cancel\".")
    if st == "pin":
        if not _flow_pin_ok(pa, user, msisdn, text):
            return
        net = NETWORK_NAMES[pa.payload["net"]]
        phone, plan_code, price = pa.payload["phone"], pa.payload["plan_code"], Decimal(pa.payload["price"])
        return _run_vtu(
            pa, user, msisdn, price, f"Data — {net} {pa.payload['plan_name']}",
            lambda ref: vtu_purchase(f"{net.lower()}-data",
                                     {"billersCode": phone, "variation_code": plan_code, "phone": phone}, reference=ref),
            lambda txn, res: f"✅ {pa.payload['plan_name']} ({net}) sent to {phone}. Ref {txn.reference}.",
        )
    _clear_actions(msisdn)
    return reply(msisdn, MENU)


# ---- electricity ----
def _start_electricity(user, msisdn: str) -> None:
    _new_flow(user, msisdn, "electricity", "disco")
    reply(msisdn, DISCO_PROMPT)


def _advance_electricity(pa: PendingAction, user, msisdn: str, text: str) -> None:
    st = pa.state
    if st == "disco":
        d = text.strip()
        if d not in DISCO_NAMES:
            return reply(msisdn, "Reply with the disco number.\n" + DISCO_PROMPT)
        pa.payload["disco"] = d
        _touch(pa, state="meter_type", payload=pa.payload)
        return reply(msisdn, "Prepaid or postpaid? Reply 1 Prepaid or 2 Postpaid.")
    if st == "meter_type":
        mt = {"1": "prepaid", "prepaid": "prepaid", "2": "postpaid", "postpaid": "postpaid"}.get(text.strip().lower())
        if not mt:
            return reply(msisdn, "Reply 1 Prepaid or 2 Postpaid.")
        pa.payload["meter_type"] = mt
        _touch(pa, state="meter", payload=pa.payload)
        return reply(msisdn, "Enter the meter number.")
    if st == "meter":
        meter = re.sub(r"\s", "", text)
        if len(meter) < 6:
            return reply(msisdn, "Enter a valid meter number.")
        disco = DISCO_NAMES[pa.payload["disco"]].lower()
        res = vtu_verify_customer(f"{disco}-electric", meter, pa.payload["meter_type"])
        if not res.get("success"):
            return reply(msisdn, "Couldn't validate that meter. Check the number and try again, or \"cancel\".")
        cust = res.get("customer_name", "")
        pa.payload.update({"meter": meter, "customer": cust})
        _touch(pa, state="amount", payload=pa.payload)
        who = f" ({cust})" if cust else ""
        return reply(msisdn, f"Meter verified{who}. How much do you want to buy? (e.g. 5000)")
    if st == "amount":
        amount = parse_amount(text)
        if amount is None or amount < 100:
            return reply(msisdn, "Enter a valid amount, at least ₦100.")
        if _insufficient(user, amount):
            return reply(msisdn, f"Insufficient balance ({_money(get_or_create_wallet(user).balance)}).")
        disco_name = DISCO_NAMES[pa.payload["disco"]]
        pa.payload["amount"] = str(amount)
        pa.payload["meta"] = {"meter": pa.payload["meter"], "disco": pa.payload["disco"],
                              "meter_type": pa.payload["meter_type"]}
        _touch(pa, state="pin", payload=pa.payload)
        cust = pa.payload.get("customer") or "—"
        return reply(msisdn, f"Confirm electricity\n{disco_name} ({pa.payload['meter_type']}) • "
                             f"Meter {pa.payload['meter']}\nCustomer: {cust} • {_money(amount)}\n"
                             "Reply with your PIN to confirm, or \"cancel\".")
    if st == "pin":
        if not _flow_pin_ok(pa, user, msisdn, text):
            return
        amount = Decimal(pa.payload["amount"])
        disco = DISCO_NAMES[pa.payload["disco"]].lower()
        disco_name = DISCO_NAMES[pa.payload["disco"]]
        meter, mt = pa.payload["meter"], pa.payload["meter_type"]

        def _line(txn, res):
            token = res.get("token") or res.get("provider_reference", "")
            extra = f" Token: {token}." if token else ""
            return f"✅ {_money(amount)} {disco_name} on meter {meter}.{extra} Ref {txn.reference}."

        return _run_vtu(
            pa, user, msisdn, amount, f"Electricity — {disco_name}",
            lambda ref: vtu_purchase(f"{disco}-electric",
                                     {"billersCode": meter, "variation_code": mt, "amount": str(amount)}, reference=ref),
            _line,
        )
    _clear_actions(msisdn)
    return reply(msisdn, MENU)


# ---- cable ----
def _start_cable(user, msisdn: str) -> None:
    _new_flow(user, msisdn, "cable", "provider")
    reply(msisdn, CABLE_PROMPT)


def _advance_cable(pa: PendingAction, user, msisdn: str, text: str) -> None:
    st = pa.state
    if st == "provider":
        p = text.strip()
        if p not in CABLE_NAMES:
            return reply(msisdn, "Reply with the provider number.\n" + CABLE_PROMPT)
        plans = list(CablePlan.objects.filter(provider=p, active=True)[:8])
        if not plans:
            _clear_actions(msisdn)
            return reply(msisdn, "No packages available for that provider right now.")
        pa.payload["prov"] = p
        pa.payload["plan_choices"] = [pl.cable_plan_code for pl in plans]
        _touch(pa, state="plan", payload=pa.payload)
        lines = "\n".join(f"{i+1}  {pl.name} • {_money(pl.price)}" for i, pl in enumerate(plans))
        return reply(msisdn, "Choose a package:\n" + lines)
    if st == "plan":
        plan = _pick(text, pa.payload.get("plan_choices", []),
                     lambda c: CablePlan.objects.filter(cable_plan_code=c).first())
        if plan is None:
            return reply(msisdn, "Reply with a package number from the list, or \"cancel\".")
        pa.payload.update({"plan_code": plan.cable_plan_code, "price": str(plan.price), "plan_name": plan.name})
        _touch(pa, state="iuc", payload=pa.payload)
        return reply(msisdn, "Enter your smartcard / IUC number.")
    if st == "iuc":
        iuc = re.sub(r"\s", "", text)
        if len(iuc) < 6:
            return reply(msisdn, "Enter a valid smartcard / IUC number.")
        prov = CABLE_NAMES[pa.payload["prov"]].lower()
        res = vtu_verify_customer(prov, iuc)
        if not res.get("success"):
            return reply(msisdn, "Couldn't validate that smartcard. Check the number and try again, or \"cancel\".")
        price = Decimal(pa.payload["price"])
        if _insufficient(user, price):
            _clear_actions(msisdn)
            return reply(msisdn, f"Insufficient balance ({_money(get_or_create_wallet(user).balance)}).")
        prov_name = CABLE_NAMES[pa.payload["prov"]]
        cust = res.get("customer_name", "")
        pa.payload.update({"iuc": iuc, "customer": cust})
        pa.payload["meta"] = {"iuc": iuc, "provider": pa.payload["prov"], "plan_code": pa.payload["plan_code"]}
        _touch(pa, state="pin", payload=pa.payload)
        cust = cust or "—"
        return reply(msisdn, f"Confirm cable\n{prov_name} • {pa.payload['plan_name']}\n"
                             f"Card {iuc} • {cust} • {_money(price)}\n"
                             "Reply with your PIN to confirm, or \"cancel\".")
    if st == "pin":
        if not _flow_pin_ok(pa, user, msisdn, text):
            return
        prov = CABLE_NAMES[pa.payload["prov"]].lower()
        prov_name = CABLE_NAMES[pa.payload["prov"]]
        iuc, plan_code, price = pa.payload["iuc"], pa.payload["plan_code"], Decimal(pa.payload["price"])
        return _run_vtu(
            pa, user, msisdn, price, f"Cable — {prov_name} {pa.payload['plan_name']}",
            lambda ref: vtu_purchase(prov, {"billersCode": iuc, "variation_code": plan_code}, reference=ref),
            lambda txn, res: f"✅ {prov_name} {pa.payload['plan_name']} activated on {iuc}. Ref {txn.reference}.",
        )
    _clear_actions(msisdn)
    return reply(msisdn, MENU)


def _pick(text: str, choices: list, fetch):
    """Map a '1'-based reply to an item via `fetch(code)`; None if out of range."""
    try:
        idx = int(text.strip()) - 1
    except ValueError:
        return None
    if not (0 <= idx < len(choices)):
        return None
    return fetch(choices[idx])


# --------------------------------------------------------------------------- #
# AI intent layer — the LLM proposes; these map its intent to the SAME flows
# --------------------------------------------------------------------------- #
NET_BY_NAME = {v.lower(): k for k, v in NETWORK_NAMES.items()}  # "mtn" -> "1"


def ai_active(link: WhatsAppLink) -> bool:
    """AI runs only if all scopes are on: an LLM key is set, the global kill
    switch is on, and this user's AI is enabled. (Per-conversation comes with
    the handover slice.)"""
    return ai.llm_available() and SystemSetting.get_bool("ai_enabled_global", True) and link.ai_enabled


def _record_intent(msisdn: str, intent: dict) -> None:
    """Attach the parsed intent to the inbound row (for QA / the monitor)."""
    row = WaMessageLog.objects.filter(msisdn=msisdn, direction=WaMessageLog.IN).order_by("-created").first()
    if row is not None:
        row.intent_json = intent
        row.save(update_fields=["intent_json"])


def _network_id(network) -> str | None:
    return NET_BY_NAME.get(str(network).strip().lower()) if network else None


def dispatch_intent(user, msisdn: str, intent: dict) -> bool:
    """Map one LLM tool call to a deterministic flow. Returns False for
    clarify/unknown so the caller shows the menu. Money still requires the
    flow's confirm + PIN — the LLM only routes here."""
    name = intent.get("name")
    p = intent.get("input", {}) or {}

    if name == "check_balance":
        _do_balance(user, msisdn)
        return True
    if name == "transfer":
        amt, acct, bank = p.get("amount"), p.get("account_number"), p.get("bank_name")
        if amt and acct and bank:
            try:
                if _begin_bank_transfer(user, msisdn, Decimal(str(amt)), re.sub(r"\D", "", str(acct)), str(bank)):
                    return True
            except (InvalidOperation, TypeError):
                pass
        _start_transfer(user, msisdn)  # partial details -> guided flow
        return True
    if name == "buy_airtime":
        return _begin_airtime(user, msisdn, p.get("amount"), p.get("phone"), p.get("network"))
    if name == "buy_data":
        _start_data(user, msisdn)
        return True
    if name == "pay_bill":
        cat = (p.get("category") or "").lower()
        if "electric" in cat:
            _start_electricity(user, msisdn)
        elif "cable" in cat or "tv" in cat:
            _start_cable(user, msisdn)
        else:
            reply(msisdn, "Reply \"electricity\" or \"cable\".")
        return True
    if name == "convert_currency":
        reply(msisdn, "Currency conversion on WhatsApp is coming soon. Use the Zitch app for now.")
        return True
    return False  # clarify / unknown


def _begin_airtime(user, msisdn: str, amount, phone, network) -> bool:
    """LLM airtime: if amount + network + phone are all known, jump to confirm;
    otherwise start the guided flow."""
    netid = _network_id(network)
    ph = _phone_from(str(phone), user) if phone else None
    try:
        amt = Decimal(str(amount)) if amount is not None else None
    except (InvalidOperation, TypeError):
        amt = None
    if amt and amt >= 50 and netid and ph:
        if _insufficient(user, amt):
            reply(msisdn, f"Insufficient balance ({_money(get_or_create_wallet(user).balance)}).")
            return True
        net = NETWORK_NAMES[netid]
        _new_flow(user, msisdn, "airtime", "pin",
                  {"pin_attempts": 0, "net": netid, "phone": ph, "amount": str(int(amt)),
                   "meta": {"phone": ph, "network": netid}})
        reply(msisdn, f"Confirm airtime\n{_money(amt)} {net} → {ph}\n"
                      "Reply with your PIN to confirm, or \"cancel\".")
        return True
    _start_airtime(user, msisdn)
    return True
