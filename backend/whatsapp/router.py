"""Deterministic WhatsApp router (slice 1).

No LLM here — keyword + numbered-menu + slot-filling that drives the same money
services the app uses (balance, NGN bank transfer with name-enquiry, confirm,
PIN, idempotency). The LLM intent layer (later) sits *in front* of this and
hands it the same structured actions, so money never depends on the AI being up.
"""
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from common.http import daily_limit_error, evaluate_transaction_pin, send_limit_error
from transfers.models import Bank
from transfers.services import PayoutError, execute_payout
from utility.models import CablePlan, DataPlan
from utility.providers import payout_resolve_account, vtu_purchase, vtu_verify_customer
from utility.views import CABLE_NAMES, DISCO_NAMES, NETWORK_NAMES
from wallet.forex import FxError, all_balances, create_fx_quote, currency_balance, execute_fx
from wallet.services import (
    DuplicateTransaction,
    InsufficientFunds,
    ensure_reserved_account,
    get_or_create_wallet,
    run_provider_purchase,
)

from . import ai
from .models import ConversationState, PendingAction, SystemSetting, WaMessageLog, WaOnboarding, WhatsAppLink
from .providers import send_image, send_text

User = get_user_model()

FLOW_TTL = timedelta(minutes=5)        # idle window for an in-progress flow
PIN_FLOW_ATTEMPTS = 2                   # 1 retry then cancel (spec §7)

MENU = (
    "💚 *Zitch* — what would you like to do?\n\n"
    "1️⃣  💰 Check balance\n"
    "2️⃣  💸 Send money\n"
    "3️⃣  📱 Airtime / Data\n"
    "4️⃣  💡 Pay a bill\n"
    "5️⃣  💱 Convert currency\n"
    "6️⃣  🏦 Add money\n\n"
    "Or just type it, e.g. \"send 5k\". Reply \"cancel\" anytime."
)
UNLINKED = (
    "👋 Welcome to *Zitch* — banking right here on WhatsApp.\n\n"
    "Reply *1* to create a new account, or *2* if you already have one."
)
ONBOARD_TTL = timedelta(minutes=15)  # window to finish a WhatsApp signup


def _local_phone(msisdn: str) -> str:
    """Normalise a WhatsApp MSISDN (234XXXXXXXXXX) to the local form (0XXXXXXXXXX)
    the app stores, so a WhatsApp-created account is consistent with app login,
    OTP and password reset."""
    d = re.sub(r"\D", "", msisdn or "")
    if d.startswith("234"):
        d = "0" + d[3:]
    return d

# Public biller logo URLs (served by the marketing site on Cloudflare). Meta
# fetches these when we send an image message. Function-level prompts use emoji
# icons; once a *specific* biller is chosen we show its real logo on the confirm
# screen and the receipt. Billers without a logo asset (electricity discos) and
# transfers send plain text — the Zitch brand shows as the WhatsApp Business
# profile picture in the chat header, not as a substitute logo in messages.
PROVIDER_LOGOS = {
    "mtn": "https://zitch.ng/assets/providers/mtn.png",
    "glo": "https://zitch.ng/assets/providers/glo.png",
    "airtel": "https://zitch.ng/assets/providers/airtel.png",
    "9mobile": "https://zitch.ng/assets/providers/9mobile.png",
    "gotv": "https://zitch.ng/assets/providers/gotv.png",
    "dstv": "https://zitch.ng/assets/providers/dstv.png",
    "startimes": "https://zitch.ng/assets/providers/startimes.png",
}


def provider_logo(name: str) -> str | None:
    """Map a biller display name (e.g. 'MTN', 'GOtv') to its public logo URL."""
    return PROVIDER_LOGOS.get(re.sub(r"\s", "", (name or "").lower()))


# --------------------------------------------------------------------------- #
# messaging + small parsers
# --------------------------------------------------------------------------- #
def reply(msisdn: str, text: str) -> None:
    """Send a message and record it (the OUT audit row; never contains a PIN)."""
    send_text(msisdn, text)
    WaMessageLog.objects.create(msisdn=msisdn, direction=WaMessageLog.OUT, text=text)


def reply_image(msisdn: str, image_url: str | None, caption: str) -> None:
    """Send a logo image with a text caption (recording the caption as the OUT
    row). With no image_url — or if the media send fails — it sends plain text, so
    a reply is never lost when a logo is missing or briefly unreachable."""
    sent = bool(image_url) and send_image(msisdn, image_url, caption).get("success", False)
    if not sent:
        send_text(msisdn, caption)
    WaMessageLog.objects.create(msisdn=msisdn, direction=WaMessageLog.OUT, text=caption)


def active_link_for(msisdn: str) -> WhatsAppLink | None:
    return WhatsAppLink.objects.filter(wa_msisdn=msisdn, status=WhatsAppLink.ACTIVE).first()


def is_awaiting_pin(msisdn: str) -> bool:
    """True if the current flow expects a PIN next — so the webhook masks it.
    Covers an in-progress money flow AND account onboarding (where the user sets
    a PIN in chat), so neither PIN is ever written to the message log in clear."""
    pa = _current_action(msisdn)
    if pa and pa.state == "pin":
        return True
    ob = _current_onboarding(msisdn)
    return bool(ob and ob.step in ("pin", "pin_confirm"))


def is_awaiting_bvn(msisdn: str) -> bool:
    """True if the current flow expects a BVN next (the in-chat virtual-account
    onboarding) — so the webhook masks it and the BVN never reaches the message
    log in clear, the same protection PINs get."""
    pa = _current_action(msisdn)
    return bool(pa and pa.action_type == "add_account" and pa.state == "bvn")


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

    # Honor marketing opt-out regardless of state (hard-rule #8).
    if low in ("stop", "unsubscribe", "stop promotions"):
        if link.marketing_opt_in:
            link.marketing_opt_in = False
            link.save(update_fields=["marketing_opt_in"])
        return reply(msisdn, "Done — you're unsubscribed from Zitch promotions. Reply \"menu\" to keep banking.")

    # Human handover: the bot stays silent; the agent replies from the console.
    convo = ConversationState.for_msisdn(msisdn)
    if convo.status == ConversationState.HUMAN:
        return

    if low in ("cancel", "quit"):
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
    if low in ("6", "add money", "fund", "fund wallet", "fund account", "deposit",
               "account", "account number", "add cash", "top up", "topup"):
        return _do_add_money(user, msisdn)
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
        return _start_convert(user, msisdn)

    # Try a one-line paste: "0123456789 GTBank John Doe 5000".
    if _start_transfer_from_paste(user, msisdn, text):
        return
    # Free-form text: let the AI route it (when active) — but the deterministic
    # paths above always win, so core flows never depend on the AI being up.
    if ai_active(link, convo):
        intent = ai.extract_intent(text)
        if intent:
            _record_intent(msisdn, intent)
            if intent.get("name") != "clarify" and dispatch_intent(user, msisdn, intent):
                return
    return reply(msisdn, "Sorry, I didn't get that.\n\n" + MENU)


# --------------------------------------------------------------------------- #
# linking
# --------------------------------------------------------------------------- #
def _current_onboarding(msisdn: str) -> WaOnboarding | None:
    WaOnboarding.objects.filter(msisdn=msisdn, expires_at__lt=timezone.now()).delete()
    return WaOnboarding.objects.filter(msisdn=msisdn).first()


def _clear_onboarding(msisdn: str) -> None:
    WaOnboarding.objects.filter(msisdn=msisdn).delete()


def _handle_unlinked(msisdn: str, text: str) -> None:
    # 1. Continue an in-progress WhatsApp signup.
    ob = _current_onboarding(msisdn)
    if ob is not None:
        return _advance_onboarding(ob, msisdn, text)

    raw = text.strip()
    low = raw.lower()

    # 2. Existing account: bind via the app-issued LINK code. Bind only if the
    # code arrives from the number on the user's Zitch account — the code is shown
    # in plaintext in the app, so without this a leaked/shoulder-surfed code lets
    # an attacker's WhatsApp claim the victim's account (SIM-swap protection).
    # Compare on the national significant number (last 10 digits) so local (080…)
    # and international (23480…) forms match.
    code = re.sub(r"[^A-Z0-9]", "", raw.upper().replace("LINK ", "", 1))
    link = (
        WhatsAppLink.objects.filter(
            status=WhatsAppLink.PENDING, link_code=code, expires_at__gt=timezone.now()
        ).first()
        if code else None
    )
    if link is not None:
        registered = re.sub(r"\D", "", (link.user.phone or ""))
        sender = re.sub(r"\D", "", msisdn)
        if registered and registered[-10:] != sender[-10:]:
            return reply(msisdn, "For your security, send this code from the phone number on your Zitch account.")
        link.wa_msisdn = msisdn
        link.status = WhatsAppLink.ACTIVE
        link.link_code = ""
        link.linked_at = timezone.now()
        link.save(update_fields=["wa_msisdn", "status", "link_code", "linked_at"])
        name = (link.user.first_name or "there").strip()
        return reply(msisdn, f"✅ *Linked!* Hi {name}, your WhatsApp is now connected to Zitch.\n\n" + MENU)

    # 3. Brand-new number: offer to create an account or link an existing one.
    if low in ("1", "create", "create account", "sign up", "signup", "register", "open account", "new", "get started"):
        return _start_onboarding(msisdn)
    if low in ("2", "link", "link account", "i have an account", "sign in", "login", "log in"):
        return reply(msisdn, "To connect an existing account, open the Zitch app → *Settings → Link WhatsApp*, get your code, and send it here.")

    # 4. Default welcome (with the create/link choices).
    return reply(msisdn, UNLINKED)


# --------------------------------------------------------------------------- #
# onboarding (create a Zitch account from WhatsApp) — phone-only Tier 1; BVN in
# the app unlocks sending. The PIN is set in chat (masked in the log) and stored
# hashed, never in clear.
# --------------------------------------------------------------------------- #
def _start_onboarding(msisdn: str) -> None:
    if User.objects.filter(phone=_local_phone(msisdn)).exists():
        return reply(msisdn, "This number already has a Zitch account. Open the app → *Settings → Link WhatsApp* to connect it here.")
    WaOnboarding.objects.update_or_create(
        msisdn=msisdn,
        defaults={"step": "first_name", "payload": {}, "expires_at": timezone.now() + ONBOARD_TTL},
    )
    reply(msisdn, "Let's set up your Zitch account \U0001f389\n\nWhat's your *first name*?")


def _onboard_to(ob: WaOnboarding, step: str) -> None:
    ob.step = step
    ob.expires_at = timezone.now() + ONBOARD_TTL
    ob.save(update_fields=["step", "payload", "expires_at"])


def _advance_onboarding(ob: WaOnboarding, msisdn: str, text: str) -> None:
    val = text.strip()
    if val.lower() in ("cancel", "quit", "stop"):
        _clear_onboarding(msisdn)
        return reply(msisdn, "No problem — signup cancelled. Reply *1* to start again anytime.")
    if ob.step == "first_name":
        if len(val) < 2:
            return reply(msisdn, "Please enter your first name.")
        ob.payload["first_name"] = val[:40]
        _onboard_to(ob, "last_name")
        return reply(msisdn, f"Nice to meet you, {val.split()[0].title()}! What's your *last name*?")
    if ob.step == "last_name":
        if len(val) < 2:
            return reply(msisdn, "Please enter your last name.")
        ob.payload["last_name"] = val[:40]
        _onboard_to(ob, "pin")
        return reply(msisdn, "Create a *4-digit PIN* to authorise payments (any 4 digits — keep it secret).")
    if ob.step == "pin":
        if not re.fullmatch(r"\d{4}", val):
            return reply(msisdn, "Your PIN must be exactly 4 digits. Try again.")
        ob.payload["pin_hash"] = make_password(val)  # never store the raw PIN
        _onboard_to(ob, "pin_confirm")
        return reply(msisdn, "Great — re-enter your *4-digit PIN* to confirm.")
    if ob.step == "pin_confirm":
        if not re.fullmatch(r"\d{4}", val) or not check_password(val, ob.payload.get("pin_hash", "")):
            ob.payload["pin_hash"] = ""
            _onboard_to(ob, "pin")
            return reply(msisdn, "Those didn't match. Let's set it again — create your *4-digit PIN*.")
        return _finish_onboarding(ob, msisdn, val)
    _clear_onboarding(msisdn)
    return reply(msisdn, UNLINKED)


def _finish_onboarding(ob: WaOnboarding, msisdn: str, pin: str) -> None:
    local = _local_phone(msisdn)
    fn = (ob.payload.get("first_name") or "").strip()
    ln = (ob.payload.get("last_name") or "").strip()
    if User.objects.filter(phone=local).exists():  # raced with the app / another signup
        _clear_onboarding(msisdn)
        return reply(msisdn, "This number already has a Zitch account — open the app to link it.")
    # WhatsApp onboarding -> Tier 2 caps (₦1,000,000/day transfers, ₦100,000/day
    # bills); full KYC in the app raises to Tier 3. The caps are enforced by the
    # shared daily-limit checks in the money flows, identically to the app.
    user = User.objects.create(username=local, phone=local, first_name=fn, last_name=ln, tier=2)
    user.set_unusable_password()       # no app password yet; "Forgot password" sets one
    user.set_transaction_pin(pin)
    user.save()
    get_or_create_wallet(user)
    WhatsAppLink.objects.create(
        user=user, wa_msisdn=msisdn, status=WhatsAppLink.ACTIVE, linked_at=timezone.now(),
    )
    _clear_onboarding(msisdn)
    reply(
        msisdn,
        f"✅ *Welcome to Zitch, {fn.title() or 'there'}!* Your account is ready.\n\n"
        "You can *send money up to ₦1,000,000/day*, pay bills, buy airtime & data, "
        "and check your balance — right here. Complete full KYC in the Zitch app to "
        "raise your limits.\n\n" + MENU,
    )


# --------------------------------------------------------------------------- #
# balance
# --------------------------------------------------------------------------- #
def _do_balance(user, msisdn: str) -> None:
    bals = all_balances(user)
    if len(bals) == 1:
        return reply(msisdn, f"💰 Your Zitch balance is {_money(bals['NGN'])}.")
    lines = [(_money(bal) if ccy == "NGN" else f"{ccy} {bal:,.2f}") for ccy, bal in bals.items()]
    reply(msisdn, "💰 Your balances:\n" + "\n".join(lines))


# --------------------------------------------------------------------------- #
# add money — the user's dedicated (reserved) account for bank-transfer funding
# --------------------------------------------------------------------------- #
def _send_account_details(msisdn: str, wallet, intro: str = "🏦 *Add money to your wallet*") -> None:
    accts = wallet.bank_accounts or []
    if len(accts) > 1:
        body = "\n".join(f"🔢 *{a.get('account_number')}* — {a.get('bank_name')}" for a in accts)
    else:
        body = f"🔢 *{wallet.account_number}*\n🏛️ {wallet.bank_name}"
    reply(
        msisdn,
        f"{intro}\n\n"
        "Transfer to your dedicated Zitch account from any bank — your wallet is "
        "credited automatically, usually within seconds:\n\n"
        f"{body}\n"
        f"👤 {wallet.account_name}",
    )


def _do_add_money(user, msisdn: str) -> None:
    """Show the user's dedicated Zitch account for bank-transfer funding (credited
    automatically by the Kora pay-in webhook). If they don't have one yet, onboard
    them right here on WhatsApp via Kora — collect the BVN and mint the account."""
    wallet = get_or_create_wallet(user)
    if not wallet.account_number and (user.bvn_verified or user.nin_verified):
        wallet = ensure_reserved_account(user)

    if wallet.account_number:
        return _send_account_details(msisdn, wallet)

    # No account yet — start the Kora onboarding in-chat by collecting the BVN.
    _new_flow(user, msisdn, "add_account", "bvn")
    return reply(
        msisdn,
        "🏦 *Add money*\n\nTo get your dedicated Zitch account for funding by bank "
        "transfer, reply with your *11-digit BVN*. We verify it securely with our "
        "licensed bank partner and issue your account instantly.\n\n"
        "_Don't know your BVN? Dial *565*0# on your registered line._\n"
        'Reply "cancel" to stop.',
    )


BVN_MAX_ATTEMPTS = 3


def _advance_add_account(pa: PendingAction, user, msisdn: str, text: str) -> None:
    """Slot-filling step for in-chat virtual-account onboarding: receive the BVN,
    hand it to Kora (which verifies it and issues the virtual account), show it.

    Verification attempts are capped (like the PIN flow): each BVN Kora rejects
    counts toward BVN_MAX_ATTEMPTS, after which the flow aborts — so a linked
    number can't brute-force BVNs against Kora's identity check."""
    bvn = re.sub(r"\D", "", text)
    if len(bvn) != 11:
        # Malformed input is guidance, not a verification attempt — don't count it.
        return reply(msisdn, 'That doesn\'t look like an 11-digit BVN. Please send your '
                             '*11-digit BVN*, or reply "cancel".')
    wallet = ensure_reserved_account(user, bvn=bvn)
    if wallet.account_number:
        _clear_actions(msisdn)
        return _send_account_details(msisdn, wallet, intro="✅ *Your Zitch account is ready!*")

    attempts = int(pa.payload.get("bvn_attempts", 0)) + 1
    if attempts >= BVN_MAX_ATTEMPTS:
        _clear_actions(msisdn)
        return reply(msisdn, "We still couldn't create your account. Double-check your BVN and "
                             'try again later from the menu, or contact support. Reply "menu" for options.')
    pa.payload["bvn_attempts"] = attempts
    _touch(pa, payload=pa.payload)
    return reply(msisdn, "Hmm, we couldn't create your account with that BVN. Check it's "
                         'correct and matches your name, then send it again — or reply "cancel".')



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
        "convert": _advance_convert,
        "add_account": _advance_add_account,
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
        limit_msg = send_limit_error(user, amount) or daily_limit_error(user, amount, "transfer")
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
    res = payout_resolve_account(acct, bank.bank_code)
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
        f"✅ *Sent* {_money(amount)} to {pa.payload['name'].upper()} ({pa.payload['bank_name']}) 🎉\n"
        f"🧾 Ref {txn.reference}\n💰 New balance: {_money(wallet.balance)}",
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
    limit_msg = send_limit_error(user, amount) or daily_limit_error(user, amount, "transfer")
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
             provider_call, success_line, logo_url: str | None = None) -> None:
    """Debit -> provider -> settle via the shared run_provider_purchase, then
    reply with the receipt / processing / failure line. On success the receipt
    carries the biller logo (or the Zitch mark) when `logo_url` is given."""
    bill_limit_msg = daily_limit_error(user, amount, "bill")
    if bill_limit_msg:
        _clear_actions(msisdn)
        return reply(msisdn, bill_limit_msg)
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
        line = success_line(txn, result)
        if logo_url:
            return reply_image(msisdn, logo_url, line)
        return reply(msisdn, line)
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
        limit_msg = send_limit_error(user, amount) or daily_limit_error(user, amount, "transfer")
        if limit_msg:
            _clear_actions(msisdn)
            return reply(msisdn, limit_msg)
        net = NETWORK_NAMES[pa.payload["net"]]
        pa.payload["amount"] = str(amount)
        pa.payload["meta"] = {"phone": pa.payload["phone"], "network": pa.payload["net"]}
        _touch(pa, state="pin", payload=pa.payload)
        return reply_image(
            msisdn, provider_logo(net),
            f"📱 *Confirm airtime*\n{_money(amount)} {net} → {pa.payload['phone']}\n\n"
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
            lambda txn, res: f"✅ {_money(amount)} {net} airtime sent to {phone} 🎉\nRef {txn.reference}.",
            logo_url=provider_logo(net),
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
        limit_msg = send_limit_error(user, price)
        if limit_msg:
            _clear_actions(msisdn)
            return reply(msisdn, limit_msg)
        net = NETWORK_NAMES[pa.payload["net"]]
        pa.payload["phone"] = phone
        pa.payload["meta"] = {"phone": phone, "network": pa.payload["net"], "plan_code": pa.payload["plan_code"]}
        _touch(pa, state="pin", payload=pa.payload)
        return reply_image(
            msisdn, provider_logo(net),
            f"🌐 *Confirm data*\n{pa.payload['plan_name']} ({net}) → {phone}\n{_money(price)}\n\n"
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
            lambda txn, res: f"✅ {pa.payload['plan_name']} ({net}) sent to {phone} 🎉\nRef {txn.reference}.",
            logo_url=provider_logo(net),
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
        limit_msg = send_limit_error(user, amount) or daily_limit_error(user, amount, "transfer")
        if limit_msg:
            _clear_actions(msisdn)
            return reply(msisdn, limit_msg)
        disco_name = DISCO_NAMES[pa.payload["disco"]]
        pa.payload["amount"] = str(amount)
        pa.payload["meta"] = {"meter": pa.payload["meter"], "disco": pa.payload["disco"],
                              "meter_type": pa.payload["meter_type"]}
        _touch(pa, state="pin", payload=pa.payload)
        cust = pa.payload.get("customer") or "—"
        return reply(
            msisdn,
            f"💡 *Confirm electricity*\n{disco_name} ({pa.payload['meter_type']}) • "
            f"Meter {pa.payload['meter']}\nCustomer: {cust} • {_money(amount)}\n\n"
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
            extra = f"\n🔌 Token: {token}." if token else ""
            return f"✅ {_money(amount)} {disco_name} on meter {meter} 🎉{extra}\nRef {txn.reference}."

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
        limit_msg = send_limit_error(user, price)
        if limit_msg:
            _clear_actions(msisdn)
            return reply(msisdn, limit_msg)
        prov_name = CABLE_NAMES[pa.payload["prov"]]
        cust = res.get("customer_name", "")
        pa.payload.update({"iuc": iuc, "customer": cust})
        pa.payload["meta"] = {"iuc": iuc, "provider": pa.payload["prov"], "plan_code": pa.payload["plan_code"]}
        _touch(pa, state="pin", payload=pa.payload)
        cust = cust or "—"
        return reply_image(
            msisdn, provider_logo(prov_name),
            f"📺 *Confirm cable*\n{prov_name} • {pa.payload['plan_name']}\n"
            f"Card {iuc} • {cust} • {_money(price)}\n\n"
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
            lambda txn, res: f"✅ {prov_name} {pa.payload['plan_name']} activated on {iuc} 🎉\nRef {txn.reference}.",
            logo_url=provider_logo(prov_name),
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


def ai_active(link: WhatsAppLink, convo: ConversationState) -> bool:
    """AI runs only if all scopes are on: an LLM key is set, the global kill
    switch is on, this user's AI is enabled, and this conversation's AI is on
    (handover turns the conversation scope off)."""
    return (ai.llm_available()
            and SystemSetting.get_bool("ai_enabled_global", True)
            and link.ai_enabled
            and convo.ai_enabled)


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
    if name == "add_money":
        _do_add_money(user, msisdn)
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
        _start_convert(user, msisdn)
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
                  {"pin_attempts": 0, "net": netid, "phone": ph, "amount": str(amt.quantize(Decimal("0.01"))),
                   "meta": {"phone": ph, "network": netid}})
        reply(msisdn, f"Confirm airtime\n{_money(amt)} {net} → {ph}\n"
                      "Reply with your PIN to confirm, or \"cancel\".")
        return True
    _start_airtime(user, msisdn)
    return True


# --------------------------------------------------------------------------- #
# currency conversion (FX) — quote -> PIN-within-TTL -> settle (Fincra rail)
# --------------------------------------------------------------------------- #
CONVERT_CCYS = ["NGN", "USD", "GBP", "CAD"]  # settle-able; CNY is quote-only (blocked)


def _start_convert(user, msisdn: str) -> None:
    _new_flow(user, msisdn, "convert", "from")
    reply(msisdn, "Convert currency.\nWhich currency are you selling? (NGN, USD, GBP, CAD)")


def _advance_convert(pa: PendingAction, user, msisdn: str, text: str) -> None:
    st = pa.state
    if st == "from":
        c = text.strip().upper()
        if c not in CONVERT_CCYS:
            return reply(msisdn, "Reply a currency code: NGN, USD, GBP or CAD.")
        pa.payload["from"] = c
        _touch(pa, state="to", payload=pa.payload)
        return reply(msisdn, "Which currency do you want to receive?")
    if st == "to":
        c = text.strip().upper()
        if c not in CONVERT_CCYS:
            return reply(msisdn, "Reply a currency code: NGN, USD, GBP or CAD.")
        if c == pa.payload["from"]:
            return reply(msisdn, "Pick a different currency to receive.")
        pa.payload["to"] = c
        _touch(pa, state="amount", payload=pa.payload)
        return reply(msisdn, f"How much {pa.payload['from']} do you want to sell?")
    if st == "amount":
        amount = parse_amount(text)
        if amount is None or amount <= 0:
            return reply(msisdn, "Enter a valid amount.")
        try:
            quote = create_fx_quote(user, pa.payload["from"], pa.payload["to"], amount)
        except FxError as exc:
            _clear_actions(msisdn)
            return reply(msisdn, exc.message)
        pa.payload["quote_ref"] = quote.quote_ref
        _touch(pa, state="pin", payload=pa.payload)
        secs = max(1, int((quote.expires_at - timezone.now()).total_seconds()))
        return reply(
            msisdn,
            "Confirm conversion\n"
            f"Sell {quote.sell_amount:,.2f} {quote.from_currency} → "
            f"Receive {quote.receive_amount:,.2f} {quote.to_currency}\n"
            f"Rate {quote.rate:.4f} • expires in {secs}s\n"
            "Reply with your PIN now to lock this rate.",
        )
    if st == "pin":
        if not _flow_pin_ok(pa, user, msisdn, text):
            return
        try:
            quote = execute_fx(user, pa.payload["quote_ref"], idempotency_key=f"wa-fx-{pa.id}")
        except FxError as exc:
            _clear_actions(msisdn)
            return reply(msisdn, exc.message)
        _clear_actions(msisdn)
        new_bal = currency_balance(user, quote.to_currency)
        return reply(
            msisdn,
            f"✅ Converted. -{quote.sell_amount:,.2f} {quote.from_currency} / "
            f"+{quote.receive_amount:,.2f} {quote.to_currency}. "
            f"New {quote.to_currency} balance: {new_bal:,.2f}.",
        )
    _clear_actions(msisdn)
    return reply(msisdn, MENU)
