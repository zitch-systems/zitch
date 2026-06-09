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
from utility.providers import disbursement_resolve_account
from wallet.services import get_or_create_wallet

from .models import PendingAction, WaMessageLog, WhatsAppLink
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
    if low in ("3", "airtime", "data"):
        return reply(msisdn, "Airtime & data on WhatsApp are coming soon. Use the Zitch app for now.")
    if low in ("4", "bill", "bills", "pay bill"):
        return reply(msisdn, "Bill payments on WhatsApp are coming soon. Use the Zitch app for now.")
    if low in ("5", "convert", "conversion"):
        return reply(msisdn, "Currency conversion on WhatsApp are coming soon. Use the Zitch app for now.")

    # Try a one-line paste: "0123456789 GTBank John Doe 5000".
    if _start_transfer_from_paste(user, msisdn, text):
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
    if pa.action_type != "transfer":
        _clear_actions(msisdn)
        return reply(msisdn, MENU)
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


def _try_pin(pa: PendingAction, user, msisdn: str, text: str) -> None:
    ok, code, message = evaluate_transaction_pin(user, text)
    if not ok:
        if code == "pin_locked":
            _clear_actions(msisdn)
            return reply(msisdn, message)
        attempts = int(pa.payload.get("pin_attempts", 0)) + 1
        if attempts >= PIN_FLOW_ATTEMPTS:
            _clear_actions(msisdn)
            return reply(msisdn, "Too many wrong PIN attempts. Transfer cancelled — reply \"menu\" to start over.")
        pa.payload["pin_attempts"] = attempts
        _touch(pa, payload=pa.payload)
        return reply(msisdn, f"{message} Reply with your PIN, or \"cancel\".")

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

    Returns True if it looked like a paste (and was handled), else False.
    """
    tokens = text.split()
    acct = next((re.sub(r"\D", "", t) for t in tokens if len(re.sub(r"\D", "", t)) == 10), None)
    amount = None
    for t in reversed(tokens):
        amount = parse_amount(t)
        if amount is not None:
            break
    if not acct or amount is None:
        return False
    matches = _match_banks(text)
    if not matches:
        return False
    if amount < 10:
        reply(msisdn, "Minimum transfer is ₦10.")
        return True
    limit_msg = send_limit_error(user, amount)
    if limit_msg:
        reply(msisdn, limit_msg)
        return True
    if get_or_create_wallet(user).balance < amount:
        reply(msisdn, f"Insufficient balance. You have {_money(get_or_create_wallet(user).balance)}.")
        return True
    _clear_actions(msisdn)
    pa = PendingAction.objects.create(
        user=user, msisdn=msisdn, action_type="transfer", state="bank",
        payload={"amount": str(amount), "account": acct, "pin_attempts": 0},
        expires_at=timezone.now() + FLOW_TTL,
    )
    if len(matches) == 1:
        _resolve_and_confirm(pa, user, msisdn, matches[0])
    else:
        pa.payload["bank_choices"] = [b.code for b in matches[:6]]
        _touch(pa, state="bank_pick", payload=pa.payload)
        lines = "\n".join(f"{i+1}  {b.name}" for i, b in enumerate(matches[:6]))
        reply(msisdn, "Which bank? Reply with the number:\n" + lines)
    return True
