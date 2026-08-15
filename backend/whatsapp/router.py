"""Deterministic WhatsApp router (slice 1).

No LLM here — keyword + numbered-menu + slot-filling that drives the same money
services the app uses (balance, NGN bank transfer with name-enquiry, confirm,
PIN, idempotency). The LLM intent layer (later) sits *in front* of this and
hands it the same structured actions, so money never depends on the AI being up.
"""
import contextvars
import hashlib
import logging
import re
import secrets
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction as db_transaction
from django.utils import timezone

from common.http import (MIN_AIRTIME, MIN_ELECTRICITY, MIN_TRANSFER, daily_limit_error,
                         evaluate_transaction_pin, mask_pii, send_limit_error,
                         velocity_exceeded)
from transfers.models import Bank
from transfers.views import _names_match
from transfers.services import PayoutError, execute_payout
from utility.models import CablePlan, DataPlan
from utility.providers import (email_live, payout_resolve_account, send_email, send_sms,
                               sms_live, verify_bvn, verify_nin, vtu_purchase,
                               vtu_verify_customer)
from utility.views import CABLE_NAMES, DISCO_NAMES, NETWORK_NAMES
from utility import wema as wema_provider
from wallet import views as wallet_views
from wallet.forex import FxError, all_balances, create_fx_quote, currency_balance, execute_fx
from wallet.services import (
    DuplicateTransaction,
    InsufficientFunds,
    LimitExceeded,
    get_or_create_wallet,
    run_provider_purchase,
)

from . import ai
from .flows import (ACCOUNT_OTP, CODE_SCREEN, EMAIL_SCREEN, FLOW_EMAIL_CODE_STATE,
                    FLOW_FORM_STATE, IDENTITY_CHAIN,
                    FLOW_ID_STATE, FLOW_PHONE_CODE_STATE, FLOW_PHONE_STATE, FLOW_PIN_STATE,
                    FLOW_SIGNUP_STATE, FLOW_VTU_STATE, IDENTITY_SCREEN, SIGNUP_SCREEN,
                    TRANSFER_FORM, VTU_SCREEN,
                    PIN_SCREEN,
                    sign_approve_token, sign_flow_token, sign_identity_token,
                    sign_onboarding_token)
from .models import ConversationState, PendingAction, SystemSetting, WaMessageLog, WaOnboarding, WhatsAppLink
from .providers import flows_live, send_buttons, send_flow, send_image, send_list, send_text

User = get_user_model()
log = logging.getLogger("whatsapp")

FLOW_TTL = timedelta(minutes=5)        # idle window for an in-progress flow
PIN_TTL = timedelta(minutes=2)         # ...and once it is armed and waiting for the PIN
#: How long an AUTHORISED payment may take to settle. Not a customer-facing
#: window at all — it is the room the queue has to retry a slow rail before the
#: row is considered abandoned, so it is generous where the other two are tight.
EXECUTION_TTL = timedelta(minutes=30)
#: State of an action whose PIN passed and whose money is now moving in the
#: worker. Deliberately not one of _AWAITING_PIN_STATES: nothing more is being
#: asked of the customer.
EXECUTING_STATE = "executing"
PIN_FLOW_ATTEMPTS = 2                   # 1 retry then cancel (spec §7)

def _links() -> dict:
    return getattr(settings, "ZITCH_LINKS", {}) or {}


def _support_wa_link() -> str:
    """wa.me deep link for customer care. Falls back to the business number, so
    the link always resolves once the channel is configured; blank when neither
    is set, in which case the caller omits the line rather than print a dead one."""
    num = (_links().get("SUPPORT_WA") or "").strip()
    if not num:
        num = ((getattr(settings, "WHATSAPP", {}) or {}).get("BUSINESS_NUMBER") or "").strip()
    digits = re.sub(r"\D", "", num)
    return f"https://wa.me/{digits}" if digits else ""


def _more_info_block() -> str:
    """The footer shown under the menu and by \"help\" — website, app, support.
    Each line is omitted when unconfigured, so the block never shows a dead link."""
    L = _links()
    lines = []
    if L.get("WEBSITE"):
        lines.append(f"🌐 More information: {L['WEBSITE']}")
    if L.get("APP"):
        lines.append(f"📲 Get the Zitch app: {L['APP']}")
    wa = _support_wa_link()
    if wa:
        lines.append(f"💬 Customer care: {wa}")
    if L.get("SUPPORT_EMAIL"):
        lines.append(f"✉️ {L['SUPPORT_EMAIL']}")
    return "\n".join(lines)


def _chat_lock_tip() -> str:
    """How to put WhatsApp's OWN fingerprint/Face ID lock on this conversation.

    This is the only real "biometric on WhatsApp" that exists. Flows has no
    biometric component and the Cloud API has no way to request or verify a
    scan — Meta keeps biometrics entirely on-device, so a business never learns
    that one happened. Chat Lock is therefore something we can TEACH but never
    require, check, or treat as a control: it protects the thread from someone
    holding an unlocked phone, and nothing in the money path may depend on it.

    The payment itself is still authorised by the PIN (in the encrypted Flow) or
    by a verified biometric through the app hand-off, both of which we can prove.
    """
    return ("🔒 *Lock this chat with your fingerprint*\n"
            "WhatsApp can require your fingerprint or Face ID before this "
            "conversation will even open:\n"
            "• Tap our name at the top → *Chat lock* → turn it on.\n\n"
            "That protects your Zitch chat if someone gets hold of your unlocked "
            "phone. Payments still need your PIN or a fingerprint check in the "
            "Zitch app.")


def _upgrade_block(user) -> str:
    """What to do about a cap you just hit, appended to every limit refusal.

    Which advice is right depends on where the customer already is:

    * Below Tier 1 — the ladder is not the problem, unfinished verification is.
      Reply 8 walks the same phone/email/BVN/NIN steps right here in the chat.
    * Tier 1 or 2 — the next rungs need document and liveness capture, which a
      chat cannot do. That referral goes to the app, with the links to get it.
    * Tier 3 — there is no higher tier to sell. Saying "upgrade in the app" to
      someone already at the top is the kind of advice that sends a customer to
      do something that cannot work, so they get support instead.
    """
    if user.tier >= 3:
        tail = ("You're on our highest tier. For a larger one-off payment, "
                "talk to us.")
    elif _kyc_outstanding(user):
        tail = ("Reply *8* to finish verifying your identity — it raises your "
                "limit straight away, right here.")
    else:
        top = user.TIER_LIMITS[3]
        tail = (f"To send more, upgrade in the Zitch app. *Tier 3* takes you up to "
                f"₦{top:,.0f} per transaction — it needs a document and selfie "
                f"check, which we can't do over chat.")
    block = _more_info_block()
    return tail + (f"\n\n{block}" if block else "")


def _limit_reply(msisdn: str, user, msg: str) -> None:
    """Send a limit refusal together with the way out of it. A bare "you've hit
    your limit" leaves the customer with nowhere to go, which is how a cap reads
    as a dead end rather than a step."""
    reply(msisdn, f"{msg}\n\n{_upgrade_block(user)}")


MENU_BODY = (
    "💚 *Zitch* — what would you like to do?\n\n"
    "1️⃣  💰 Check balance\n"
    "2️⃣  💸 Send money\n"
    "3️⃣  📱 Airtime / Data\n"
    "4️⃣  💡 Pay a bill\n"
    "5️⃣  💱 Convert currency\n"
    "6️⃣  🏦 Add money\n"
    "7️⃣  🧾 My account details\n"
    "8️⃣  ✅ Verify my identity\n"
    "9️⃣  🧾 Transaction history\n\n"
    # "just type it" was a promise the channel could not keep: free-form routing
    # needs the customer's own AI consent, which defaults off and which nobody
    # guesses the phrase for. Name the phrase where the promise is made.
    "Or just type what you want — \"send 5k to Ada\", \"2k airtime\".\n"
    "Reply \"cancel\" anytime, or *ai off* to stick to the menu."
)


def menu_text() -> str:
    """The menu plus the links footer. Built per call, not frozen at import, so
    the links follow settings (which deployments and tests both override)."""
    block = _more_info_block()
    return MENU_BODY + (f"\n\n{block}" if block else "")
UNLINKED = (
    "👋 Welcome to *Zitch* — banking right here on WhatsApp.\n\n"
    "Reply *1* to create a new account, or *2* if you already have one."
)
UNLINKED_APP_ONLY = (
    "👋 Welcome to *Zitch*. For your security, create your account and payment PIN "
    "in the Zitch app, then open *Settings → Link WhatsApp* to connect it here."
)
ONBOARD_TTL = timedelta(minutes=15)  # window to finish a WhatsApp signup


def _chat_signup_allowed() -> bool:
    """Whether a brand-new number may open its account here. On unless a deploy
    turns it off — the PIN is kept out of the thread by `_pin_in_chat_allowed()`,
    which is a separate guard, so this switch is about where signup happens, not
    about whether a secret can land in the transcript."""
    cfg = getattr(settings, "WHATSAPP", {}) or {}
    return bool(cfg.get("ALLOW_CHAT_SIGNUP", True))


# What someone types when they mean "open an account". The menu answer is *1*,
# but almost nobody replies with a digit to a greeting — they say what they want
# ("i want to open account here"), and matching only an exact phrase list sent
# every one of those back the same welcome, which reads as the bot refusing.
# A verb near an account word in either order, plus the standalone asks.
_ACCOUNT_NOUN = r"(?:account|acct|wallet|profile)"
_CREATE_VERB = (r"(?:create|creating|open|opening|start|register|registration|new|make"
                r"|set\s*up|sign\s*up|signup|join|want|need)")
CREATE_INTENT = re.compile(
    rf"\b{_CREATE_VERB}\b[^.?!]{{0,30}}\b{_ACCOUNT_NOUN}\b"
    rf"|\b{_ACCOUNT_NOUN}\b[^.?!]{{0,20}}\b{_CREATE_VERB}\b"
    # Verbs that need no object to be unambiguous — "let me register", "how do
    # I sign up". LINK_INTENT is tried first, so "i have registered" still goes
    # to linking (and the past tense misses this \b-bounded match anyway).
    rf"|\b(?:sign\s*up|signup|register|get\s+started|onboard)\b",
    re.I,
)
# Checked FIRST, so "i already have an account" and "link my account" keep going
# to the linking answer rather than starting a signup they don't need.
LINK_INTENT = re.compile(
    r"\b(?:link|connect|attach)\b[^.?!]{0,30}\b(?:account|whatsapp|zitch|number)\b"
    r"|\b(?:i|we)\s+(?:already\s+)?have\s+(?:an?\s+|my\s+)?(?:zitch\s+)?(?:account|acct|wallet)\b"
    r"|\b(?:existing|old)\s+(?:zitch\s+)?(?:account|acct|wallet)\b"
    r"|\b(?:i|we)\s+(?:have\s+)?(?:already\s+)?(?:registered|signed\s*up)\b"
    r"|^\s*(?:log\s*in|login|sign\s*in|signin)\s*[.!]?\s*$",
    re.I,
)


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
# The network logos use wide "-wa" banner variants: WhatsApp upscales any image
# to the chat-bubble width, so a square logo renders as a big square. The banners
# are short/wide (~2.6:1) so they render as a compact strip. Cable logos are
# already wide banners, so they keep their originals.
PROVIDER_LOGOS = {
    "mtn": "https://zitch.ng/assets/providers/mtn-wa.png",
    "glo": "https://zitch.ng/assets/providers/glo-wa.png",
    "airtel": "https://zitch.ng/assets/providers/airtel-wa.png",
    "9mobile": "https://zitch.ng/assets/providers/9mobile-wa.png",
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
def reply(msisdn: str, text: str) -> dict:
    """Send a message and record it (the OUT audit row; never contains a PIN).

    A failed Graph call does not raise — reply() always returns normally, so the
    inbound job that called us marks the message "processed" either way. The OUT
    row is therefore the only place a failed *send* (as opposed to a failed
    *process*) can be found; without recording it here, `whatsapp_diagnostics`
    would have no failed sends to count."""
    result = send_text(msisdn, text)
    _log_out(msisdn, text, result)
    return result


def _log_out(msisdn: str, text: str, result: dict | None) -> None:
    """The OUT audit row, recording whether Meta ACCEPTED the send.

    Shared by every sender, because the ones that fall back had the same gap
    reply() did: they attempted a send, dropped the result, and wrote a row that
    said "replied" either way. That mattered most on the paths a new user hits
    first — the menu goes out through reply_list/reply_buttons, so a dead token
    produced a clean outbound log for exactly the first reply anyone would miss.

    `result` is whichever attempt decided the outcome: for the falling-back
    senders that is the fallback, since it is the one that either reached the
    user or did not. None means the caller never got that far.
    """
    ok = bool((result or {}).get("success"))
    error = "" if ok else str((result or {}).get("error_code") or "send_failed")[:64]
    WaMessageLog.objects.create(msisdn=msisdn, direction=WaMessageLog.OUT, text=text,
                                processing_error=error)


def reply_image(msisdn: str, image_url: str | None, caption: str) -> None:
    """Send a logo image with a text caption (recording the caption as the OUT
    row). With no image_url — or if the media send fails — it sends plain text, so
    a reply is never lost when a logo is missing or briefly unreachable."""
    sent = bool(image_url) and send_image(msisdn, image_url, caption).get("success", False)
    result = {"success": True} if sent else send_text(msisdn, caption)
    _log_out(msisdn, caption, result)


def _sender_rows(user) -> list:
    """Who the money came FROM, on every receipt. A receipt is forwarded as proof
    of payment, so the payer has to be on the artifact itself — a screenshot with
    only a recipient proves nothing about who sent it."""
    if user is None:
        return []
    name = (user.get_full_name() or user.first_name or "").strip().upper()
    phone = (user.phone or "").strip()
    rows = []
    if name:
        rows.append(("From", name))
    if phone:
        # Last four only: the receipt is designed to be forwarded, and a full
        # number on a shared artifact is an invitation to impersonation.
        rows.append(("Sender", f"•••••••{phone[-4:]}"))
    return rows


def reply_receipt(msisdn: str, title: str, rows: list, *, ref: str,
                  user=None, balance_after=None) -> str:
    """Send the transaction receipt as a branded JPEG rendered server-side, with the
    readable text as the caption.

    Sender details are prepended for every receipt. The BALANCE is deliberately
    never a receipt row: receipts get screenshotted and forwarded to the person
    who was paid, and a balance is not theirs to see. When `balance_after` is
    given it is sent as a separate message afterwards, which the customer can
    keep or delete independently of the receipt they share.

    It goes out as an IMAGE, not a document: an image renders in the thread where the
    user can read it without tapping, forward it in one gesture, and save it to their
    gallery — which is the whole point of a receipt. A document arrives as a grey file
    card nobody opens. If the image send is refused we still try the document (a
    receipt on file beats no receipt), and text is the last resort so one is never
    lost. Returns the text form, also used as the Flow success screen message and the
    OUT log row.

    Every fall-back is logged. A silent downgrade to text is exactly the kind of
    failure that looks like a cosmetic choice and hides a broken media pipeline.
    """
    rows = _sender_rows(user) + list(rows)
    text = _receipt(title, rows)
    from .providers import send_document, send_image_media, upload_media, wa_live

    delivered = ""
    if wa_live():
        try:
            from .receipt import render_receipt

            filename = f"Zitch-Receipt-{ref}.jpg"
            media_id = upload_media(render_receipt(title, rows, ref), "image/jpeg", filename)
            if not media_id:
                log.warning("wa_receipt_upload_failed ref=%s", ref)
            else:
                if send_image_media(msisdn, media_id, caption=text).get("success"):
                    delivered = "image"
                elif send_document(msisdn, media_id, filename, caption=text).get("success"):
                    delivered = "document"
                    log.warning("wa_receipt_image_refused ref=%s fell_back=document", ref)
        except Exception:  # never let receipt rendering break a completed txn
            log.exception("wa_receipt_render_failed ref=%s", ref)
    result = {"success": True} if delivered else None
    if not delivered:
        if wa_live():
            log.warning("wa_receipt_text_fallback ref=%s", ref)
        result = send_text(msisdn, text)
    _log_out(msisdn, text, result)
    if balance_after is not None:
        # Separate message, sent after the receipt — never part of the artifact
        # the customer forwards.
        reply(msisdn, f"💰 Your Zitch balance is now {_money(balance_after)}.")
    return text


def reply_list(msisdn: str, body: str, rows, button_label: str = "Choose") -> None:
    """Interactive list with a numbered-text fallback: live sends a tappable list
    (row ids = the text the router expects), mock/dev sends the equivalent
    numbered text. The OUT log row records the fallback text either way."""
    fallback = body + "\n" + "\n".join(
        f"{rid}  {title}" + (f" — {desc}" if desc else "") for rid, title, desc in rows)
    from .providers import wa_live
    sent = send_list(msisdn, body, rows, button_label=button_label) if wa_live() else {}
    result = sent if sent.get("success") else send_text(msisdn, fallback)
    _log_out(msisdn, fallback, result)


def reply_buttons(msisdn: str, body: str, buttons) -> None:
    """Interactive reply buttons (max 3) with a text fallback, logged like reply()."""
    fallback = body + "\n" + " / ".join(f"\"{bid}\"" for bid, _ in buttons)
    from .providers import wa_live
    sent = send_buttons(msisdn, body, buttons) if wa_live() else {}
    result = sent if sent.get("success") else send_text(msisdn, fallback)
    _log_out(msisdn, fallback, result)


def send_menu(msisdn: str) -> None:
    """The main menu as a plain numbered list — reply with the number (1–6) or
    type the action (e.g. \"send 5k\"). Kept as text rather than a tappable list
    so it reads as the classic numbered menu."""
    reply(msisdn, menu_text())


def _ask_network(msisdn: str) -> None:
    reply_list(msisdn, "Which network?",
               [("1", "MTN", ""), ("2", "GLO", ""), ("3", "Airtel", ""), ("4", "9mobile", "")],
               button_label="Network")


def _receipt(title: str, lines: list) -> str:
    """A structured receipt block — the confirmation artifact users screenshot."""
    body = "\n".join(f"{k}: {v}" for k, v in lines)
    return f"🧾 *{title}*\n━━━━━━━━━━━━\n{body}\n━━━━━━━━━━━━\nStatus: ✅ Successful"


def _flow_summary(pa: PendingAction) -> str:
    """One-line human summary of a pending money action — shown on the secure
    Flow's PIN screen and reused as the Flow message body."""
    p = pa.payload
    at = pa.action_type
    try:
        if at == "transfer":
            return (f"Send {_money(Decimal(p['amount']))} to {p.get('name', 'recipient').upper()}"
                    f" · {p.get('bank_name', '')} {p.get('account', '')}".rstrip())
        if at == "airtime":
            return f"{_money(Decimal(p['amount']))} {NETWORK_NAMES.get(p.get('net', ''), '')} airtime → {p.get('phone', '')}"
        if at == "data":
            return (f"{p.get('plan_name', 'Data')} ({NETWORK_NAMES.get(p.get('net', ''), '')})"
                    f" → {p.get('phone', '')} · {_money(Decimal(p['price']))}")
        if at == "electricity":
            return f"{_money(Decimal(p['amount']))} {DISCO_NAMES.get(p.get('disco', ''), '')} · meter {p.get('meter', '')}"
        if at == "cable":
            return (f"{CABLE_NAMES.get(p.get('prov', ''), '')} {p.get('plan_name', '')}"
                    f" · card {p.get('iuc', '')} · {_money(Decimal(p['price']))}")
        if at == "convert":
            return "Confirm your currency conversion"
        if at == "unlock":
            # Not a payment, and the one confirm that arrives unprompted — so the
            # card has to say WHY it appeared. It used to rely on a chat line
            # beside it, which is exactly the second message this stopped sending.
            return "It's been a while — confirm it's you to continue"
    except (KeyError, InvalidOperation):
        pass
    return "Confirm your payment"


def _narration(pa: PendingAction) -> str:
    """The customer's note for this action, or "" — always optional, never a
    reason to refuse a payment."""
    from .flows import clean_narration

    return clean_narration(pa.payload.get("narration"))


def _with_narration(pa: PendingAction, rows: list) -> list:
    """Put the note on the receipt, under the amount.

    A receipt is the thing customers forward as proof of payment, and "what was
    this for" is the question they are answering when they forward it. Placed
    after the amount rather than appended at the end so it reads with the
    payment, not after the reference and the date — which are for us, not them.

    Absent rather than blank when there is no note: a receipt with an empty
    "Narration —" row looks like something failed to render.
    """
    note = _narration(pa)
    if not note:
        return rows
    at = next((i for i, (k, _v) in enumerate(rows)
               if str(k).strip().lower() == "amount"), len(rows) - 1)
    return [*rows[:at + 1], ("Narration", note), *rows[at + 1:]]


def pin_screen_send_data(amount: str, recipient: str = "", details: str = "",
                         balance: str = "", narration: str = "") -> dict:
    """The `flow_action_payload.data` for a message that OPENS on a PIN screen.

    One builder for every such send, because the send payload is a contract with
    the published screen and hand-written dicts have already broken it once: when
    the three-line confirm landed, the signup PIN send kept passing the retired
    "summary" key, Meta rejected the undeclared property, and every signup PIN
    send failed silently down its fallback rungs the moment the new Flow went
    live. A property added to the screen must reach every sender, and the only
    way to be sure of that is for there to be one.

    The non-payment senders (signup PIN, PIN reset) pass neither balance nor
    narration and get empty strings: they describe no transaction, but the screen
    still declares the properties, and declared-but-absent is the rejection.
    """
    return {"balance": balance, "amount": amount, "recipient": recipient,
            "details": details, "narration": narration, "error": ""}


def _flow_balance_line(pa: PendingAction) -> str:
    """What the customer has, on the screen where they decide to spend it.

    Deciding whether to send ₦50,000 needs the balance in front of you, and this
    is the last screen before the money goes. Without it the only way to check
    was to abandon the payment, ask for the balance, and start over — so the
    screen that most needed the number was the one screen that never showed it.

    Rendered at the TOP of the screen rather than the top-right corner: a Flow
    JSON SingleColumnLayout stacks its children vertically and the version in use
    has no row, column or alignment primitive, so a corner is not expressible
    here. Empty on any failure — a balance we cannot read must not take down the
    confirm screen it is decoration on.
    """
    try:
        return f"Balance {_money(get_or_create_wallet(pa.user).balance)}"
    except Exception:  # noqa: BLE001 — never block a payment to print a number
        log.exception("could not read balance for the confirm screen pa=%s", pa.id)
        return ""


def _flow_fields(pa: PendingAction) -> dict:
    """The confirm screen's lines: the balance, the amount, who/what, the routing
    detail, and the customer's own narration.

    Split rather than one sentence because this is the screen someone checks
    before money leaves. An account number buried mid-sentence is not read; on
    its own line it is. The bank matters most of all — routing is purely by
    {account_number, bank_code}, so the bank is half of where the money goes and
    a customer confirming "JOHN DOE" alone has confirmed the wrong half.

    Every branch is completed by `_with_context` below, so no branch can ship a
    dict missing a property the published screen declares — the mismatch that
    shows "Couldn't load content. Try again later." instead of an ending.
    """
    p = pa.payload
    at = pa.action_type

    def _with_context(fields: dict) -> dict:
        # Through the same cleaner as every other consumer. It is already clean
        # at rest — each entry point cleans on the way in — but this is the one
        # that renders it to a screen, and two spellings of "the narration" is
        # how the two drift.
        note = _narration(pa)
        return {**fields,
                "balance": _flow_balance_line(pa),
                # Prefixed so the line reads as the customer's own note rather
                # than as another routing detail on a screen full of them.
                "narration": f"Note: {note}" if note else ""}

    try:
        if at == "transfer":
            return _with_context({
                "amount": _money(Decimal(p["amount"])),
                "recipient": f"To {p.get('name', 'recipient').upper()}",
                "details": f"{p.get('bank_name', '')} · {p.get('account', '')}".strip(" ·")})
        if at == "airtime":
            return _with_context({
                "amount": _money(Decimal(p["amount"])),
                "recipient": f"{NETWORK_NAMES.get(p.get('net', ''), '')} airtime".strip(),
                "details": f"To {p.get('phone', '')}".strip()})
        if at == "data":
            return _with_context({
                "amount": _money(Decimal(p["price"])),
                "recipient": f"{p.get('plan_name', 'Data')} · "
                             f"{NETWORK_NAMES.get(p.get('net', ''), '')}".strip(" ·"),
                "details": f"To {p.get('phone', '')}".strip()})
        if at == "electricity":
            cust = p.get("customer", "")
            meter_line = f"Meter {p.get('meter', '')}".strip()
            if cust:
                meter_line += f" · {cust}"
            return _with_context({
                "amount": _money(Decimal(p["amount"])),
                "recipient": DISCO_NAMES.get(p.get("disco", ""), "Electricity"),
                "details": meter_line})
        if at == "cable":
            return _with_context({
                "amount": _money(Decimal(p["price"])),
                "recipient": f"{CABLE_NAMES.get(p.get('prov', ''), '')} "
                             f"{p.get('plan_name', '')}".strip(),
                "details": f"Smartcard {p.get('iuc', '')}".strip()})
    except (KeyError, InvalidOperation):
        pass
    # Conversion (and any shape we don't itemise) falls back to the one-line
    # summary in the heading, with the other lines blank rather than absent —
    # the screen declares all of them, so every one must be supplied.
    return _with_context({"amount": _flow_summary(pa), "recipient": "", "details": ""})


def _send_pin_flow(pa: PendingAction, user) -> bool:
    """Send the secure PIN Flow for this action and move it to the flow_pin state.
    Returns True if the Flow was dispatched; False to fall back to SMS/PIN. The
    signed flow_token maps Meta's later data-exchange call back to THIS action."""
    summary = _flow_summary(pa)
    pa.payload["flow_summary"] = summary
    # Persisted so the Flow endpoint can re-render the same screen on a wrong
    # PIN or a BACK without recomputing it from a payload that may have moved on.
    fields = _flow_fields(pa)
    pa.payload["flow_fields"] = fields
    # Opens on the root, not the twin — the same reset every sibling sender does.
    # Without it this was the ONE sender that let a stale flow_screen survive: an
    # action re-armed after the transfer form (which sets flow_screen=PIN_CHAIN)
    # would have INIT/BACK answer PIN_CHAIN against a message opened on
    # PIN_SCREEN, and PIN_SCREEN -> PIN_CHAIN is not a declared route. Meta
    # refuses that navigation on the device, mid-payment.
    pa.payload["flow_screen"] = PIN_SCREEN
    pa.payload["flow_pin_tries"] = 0        # a fresh send is a fresh budget
    _touch(pa, state=FLOW_PIN_STATE, payload=pa.payload)  # persist so the token resolves
    # Hierarchy, not availability: a customer with the app is led to the
    # biometric approval and the Flow's own button becomes the fallback ("Use
    # PIN instead"). Both remain live either way — this only decides which one
    # the message presents as the way to confirm.
    if _has_app_session(user):
        body = f"{summary}\n\n{_approve_link_line(pa, primary=True)}"
        cta = "Use PIN instead"
    else:
        body = summary + _approve_link_line(pa, primary=False)
        cta = ""   # provider default: "Confirm with PIN"
    # "unlock" re-verifies the owner after a lull — no money moves, so the
    # card should never claim to be a payment.
    header = "Confirm identity" if pa.action_type == "unlock" else "Confirm payment"
    res = send_flow(
        pa.msisdn, sign_flow_token(pa),
        header=header, body=body,
        # fields comes from _flow_fields, which completes every branch with the
        # balance and the narration — so this spread stays complete by
        # construction rather than by remembering to update it.
        screen=PIN_SCREEN, screen_data={**fields, "error": ""},
        cta=cta,
    )
    return bool(res.get("success"))


def _send_identity_flow(pa: PendingAction, kind: str, fallback_state: str = "") -> bool:
    """Collect a BVN/NIN in the encrypted Flow rather than the chat. True if the
    Flow was dispatched; False to fall back to asking in the thread.

    Same reasoning as the PIN: WhatsApp has no view-once for text and lets only
    the SENDER delete a message, so anything typed into the thread stays in the
    customer's own history indefinitely. An identity number is exactly what
    should not sit there.

    Unlike the PIN this does NOT fail closed. A PIN in a chat is catastrophic and
    worth refusing service over; a BVN in the customer's own thread is bad but is
    what happens today, and failing closed would block every signup on any deploy
    where Flows are not configured. The fallback says how to remove it instead.
    """
    if not flows_live():
        return False
    pa.payload["id_kind"] = kind
    pa.payload["flow_screen"] = IDENTITY_SCREEN          # opens on the root, not the twin
    # Where this step goes if its answer arrives in the CHAT instead. Recorded at
    # arm time rather than re-derived: the same value already picks the chat
    # state on a send failure below, and _accept_identity_in_chat needs it for a
    # customer who simply typed the number rather than tapping the screen.
    pa.payload["id_fallback_state"] = fallback_state or kind
    _touch(pa, state=FLOW_ID_STATE, payload=pa.payload)   # persist so the token resolves
    which = kind.upper()
    res = send_flow(
        pa.msisdn, sign_identity_token(pa),
        header="Verify your identity", body=f"Enter your {which} privately — it never appears in this chat.",
        screen=IDENTITY_SCREEN,
        screen_data={"summary": f"Enter your 11-digit {which}", "label": which, "error": ""},
        cta="Enter securely",
    )
    if res.get("success"):
        return True
    # Dispatch failed: put the action back where the chat fallback expects it,
    # otherwise it would sit in a Flow state with no Flow open. Account setup
    # parks both ID types in one "bvn" entry state, so it names its own.
    _touch(pa, state=fallback_state or kind, payload=pa.payload)
    log.warning("wa_identity_flow_send_failed kind=%s pa=%s detail=%r", kind, pa.id,
                res.get("error_detail", ""))
    return False


def _send_email_flow(pa: PendingAction, step: str) -> bool:
    """Run the email step in the encrypted Flow, the same way BVN and NIN run.

    Two halves on one open Flow: the address (unmasked — it is not a secret and
    has to be typed correctly), then the 6-digit code (masked). The code is the
    reason this exists: it is a bearer credential for ten minutes, and typing it
    into the thread leaves it in the customer's history long after that.

    Like the identity Flow and unlike the PIN, this does NOT fail closed — a
    deploy without Flows configured still verifies email in the chat.
    """
    if not flows_live():
        return False
    pa.payload["id_kind"] = "email"
    pa.payload["id_step"] = step
    pa.payload["flow_screen"] = EMAIL_SCREEN if step == "address" else CODE_SCREEN
    _touch(pa, state=FLOW_ID_STATE, payload=pa.payload)   # persist so the token resolves
    if step == "address":
        screen, data = EMAIL_SCREEN, {"summary": "What's your email address?",
                                      "label": "Email address", "error": ""}
        body = "Enter your email privately — it never appears in this chat."
    else:
        screen, data = CODE_SCREEN, {
            "summary": f"Enter the 6-digit code we sent to {pa.user.email}",
            "label": "Email code", "error": ""}
        body = "Enter the code privately — it never appears in this chat."
    res = send_flow(
        pa.msisdn, sign_identity_token(pa),
        header="Verify your email", body=body,
        screen=screen, screen_data=data, cta="Enter securely",
    )
    if res.get("success"):
        return True
    # Dispatch failed: put the action back where the chat fallback expects it.
    _touch(pa, state=("email_address" if step == "address" else "email"), payload=pa.payload)
    log.warning("wa_email_flow_send_failed step=%s pa=%s detail=%r", step, pa.id,
                res.get("error_detail", ""))
    return False


def _approve_url(pa: PendingAction) -> str:
    """The https hand-off for approving this action in the app, or "" when no
    public origin is configured. https because WhatsApp renders only http(s) as
    tappable; the page at the other end bounces into `zitch://waapprove`, where
    the app authenticates the customer's biometric on-device and submits the
    SAME transaction PIN the other channels verify.

    The token binds the action to its owner, so a forwarded link redeems for
    nobody else — but it still names an approval, which is why the bounce page
    shows nothing about the action itself.
    """
    base = (_links().get("API_BASE") or "").rstrip("/")
    if not base:
        return ""
    return f"{base}/wa/approve/{sign_approve_token(pa)}"


def _has_app_session(user) -> bool:
    """Has this account ever authenticated from the app? KnownDevice rows are
    written on app sign-in and nowhere else, so their existence separates "has
    the app" from a WhatsApp-only customer. Used ONLY to pick which confirmation
    to lead with — it grants nothing."""
    return user.known_devices.exists()


def _approve_link_line(pa: PendingAction, *, primary: bool) -> str:
    """The biometric-approval line for a confirm message.

    `primary` flips the framing, not the mechanics. Biometric approval is the
    PREFERRED confirmation for anyone who has the app — it proves the account
    owner's finger or face, which a shoulder-surfed PIN cannot — so for them the
    line leads the message and the PIN is offered as the fallback. A customer
    who has never signed into the app is not led to a door they can't open: for
    them the line stays an offer under the PIN instructions, and the bounce
    page's store links do the recruiting.
    """
    url = _approve_url(pa)
    if not url:
        return ""
    if primary:
        return ("📲 *Approve with your fingerprint or Face ID* — fastest and most "
                f"secure:\n{url}")
    return f"\n\n📲 Have the Zitch app? Approve with your fingerprint or Face ID: {url}"


def _arm_confirm(pa: PendingAction, user) -> bool:
    """Move a money flow to its confirm step. Preference, most-secure first:

    1. A WhatsApp Flow (secure PIN pad) when configured — the PIN is typed into a
       native masked field and submitted ENCRYPTED to our endpoint, so the chat
       never carries it at all.
    2. A single-use 6-digit SMS code (live SMS, no Flow) — the chat carries a code
       that's worthless after one use / 5 minutes, never the PIN.
    3. The PIN in chat only in explicit dev/test mode. Production fails closed
       when neither secure channel is available.

    Whichever rung is armed, the deep-link approval (biometric in the app) is
    offered alongside it — see _approve_link_line."""
    # No PIN on the account: arming a confirm produces a screen the customer can
    # never satisfy — which is what "No transaction PIN set on this account"
    # was. Send them to set one instead of into a dead end.
    if not user.transaction_pin:
        _clear_actions(pa.msisdn)
        reply(pa.msisdn, "🔐 You haven't set a transaction PIN yet — it's what authorises "
                         "payments here and in the Zitch app.\n\nReply *reset pin* to set your "
                         "6-digit PIN now, then try again.")
        return False
    if flows_live() and _send_pin_flow(pa, user):
        return True
    # Checked before sending, not after: unkeyed, send_sms succeeds in mock mode, which
    # would arm this rung around a code that never left the building.
    if sms_live():
        code = f"{secrets.randbelow(10**6):06d}"
        sent = send_sms(user.phone or "",
                        f"Zitch: {code} is your confirmation code. It expires in 5 minutes. Never share it.")
        if sent.get("success"):
            pa.payload["otp_hash"] = make_password(code)
            pa.payload["otp_exp"] = (timezone.now() + timedelta(minutes=5)).isoformat()
            _touch(pa, state="pin", payload=pa.payload)
            return True
    if settings.DEBUG or getattr(settings, "TESTING", False):
        _touch(pa, state="pin", payload=pa.payload)
        return True
    # Never ask a production user to disclose their permanent transaction PIN
    # in a chat. Clear the armed action so a later message cannot execute it.
    pa.state = "blocked"
    _clear_actions(pa.msisdn)
    reply(pa.msisdn, _confirm_prompt(pa))
    return False


def _confirm_prompt(pa: PendingAction) -> str:
    if pa.state == "blocked":
        return "Secure confirmation is unavailable right now. Please complete this payment in the Zitch app."
    has_app = _has_app_session(pa.user)
    if pa.payload.get("otp_hash"):
        code_line = ("🔐 Or enter the *6-digit code* we just sent you by SMS, or reply \"cancel\". "
                     "(Never type your PIN here.)") if has_app else \
                    ("🔐 Enter the *6-digit code* we just sent you by SMS, or reply \"cancel\". "
                     "(Never type your PIN here.)")
        if has_app:
            return f"{_approve_link_line(pa, primary=True)}\n\n{code_line}"
        return code_line + _approve_link_line(pa, primary=False)
    if pa.state == FLOW_PIN_STATE:
        # The secure Flow is already open and carries its own confirm button, so
        # this line must NOT restate the ask. It used to fall through to the
        # dev/test text below, which put "Reply with your PIN to confirm" in a
        # production thread — a second, contradictory prompt inviting exactly
        # what the Flow exists to keep out, right beneath the Flow itself.
        cta = (getattr(settings, "WHATSAPP_FLOW", {}) or {}).get("CTA", "Confirm with PIN")
        return (f"🔐 Tap *{cta}* on the secure card above — your PIN stays private "
                "and never appears in this chat. Or reply \"cancel\".")
    # Only reachable in dev/test — production arms a Flow or an SMS code and
    # fails closed rather than ask for a PIN here. The delete advice rides along
    # anyway, so the one prompt that can put a PIN in a thread also says how to
    # get it out: WhatsApp lets the sender delete, and nobody else.
    return ("Reply with your PIN to confirm, or \"cancel\".\n"
            "_Delete your PIN message afterwards (press and hold → Delete → Delete for everyone)._"
            + _approve_link_line(pa, primary=False))


def _send_confirm(pa: PendingAction, msisdn: str, body: str, logo: str = "") -> None:
    """The confirm card, in the CHAT — and nothing at all when the secure Flow
    is already one.

    The Flow message body IS this summary (`_send_pin_flow` sends
    `_flow_summary(pa)`) and it carries the confirm button. Sending this
    afterwards stacked two cards in the thread saying the same thing, one of them
    unactionable, with the real one scrolled above it — and the second card
    invited a tap it could not service. One PIN request, one card.

    The chat card is still the right answer on every other rung: an SMS code and
    the dev/test PIN prompt have no card of their own, so this IS their card.
    """
    if pa.state == FLOW_PIN_STATE:
        return None
    # The note goes with the details, above the "enter your PIN" prompt, so the
    # customer confirms what they wrote alongside what it will cost. Every chat
    # confirm card is built by a different function; this is the one funnel they
    # all pass through, so it is the only place that cannot be forgotten.
    note = _narration(pa)
    if body and note:
        body = f"{body}\n📝 {note}"
    text = f"{body}\n\n{_confirm_prompt(pa)}" if body else _confirm_prompt(pa)
    return reply_image(msisdn, logo, text) if logo else reply(msisdn, text)


def active_link_for(msisdn: str) -> WhatsAppLink | None:
    return WhatsAppLink.objects.filter(wa_msisdn=msisdn, status=WhatsAppLink.ACTIVE).first()


def is_awaiting_pin(msisdn: str) -> bool:
    """True if the current flow expects a PIN next — so the webhook masks it.
    Covers an in-progress money flow AND account onboarding (where the user sets
    a PIN in chat), so neither PIN is ever written to the message log in clear."""
    pa = _current_action(msisdn)
    if pa and pa.state in ("pin", FLOW_PIN_STATE):
        return True
    ob = _current_onboarding(msisdn)
    return bool(ob and ob.step in ("pin", "pin_confirm", FLOW_PIN_STATE))


def is_awaiting_bvn(msisdn: str) -> bool:
    """True if the current flow expects a BVN next (the in-chat virtual-account
    onboarding) — so the webhook masks it and the BVN never reaches the message
    log in clear, the same protection PINs get."""
    pa = _current_action(msisdn)
    if pa is None:
        return False
    if pa.action_type == "add_account" and pa.state == "bvn":
        return True
    # An identity Flow is OPEN. The secure screen is where the number is meant to
    # go, but the chat is where some customers put it anyway — and it is now read
    # rather than refused, so it has to be masked here too. It always should have
    # been: the log wrote the number in clear either way, and refusing to act on
    # it never stopped it arriving.
    #
    # Only the 11-digit identity NUMBER needs adding here: a bare 6-digit code
    # is already masked by the webhook's shape rule, and the email address is
    # not a secret.
    if pa.state == FLOW_ID_STATE:
        return (str(pa.payload.get("id_kind", "")).lower() in ("bvn", "nin")
                and not pa.payload.get("id_otp_hash"))
    # The chat KYC flow collects a BVN/NIN too; the same masking must cover it or
    # an identity number typed there would land in the log in clear.
    return pa.action_type == "kyc" and pa.state in ("bvn", "nin")


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
    """The LIVE flow for this number.

    An expired row is not returned — and deliberately not deleted here either.
    Deleting it on read is what made a timeout invisible: the flow vanished
    mid-payment and the next message was answered as if it had never existed.
    `_announce_timeout` clears it, after saying so.
    """
    return PendingAction.objects.filter(
        msisdn=msisdn, expires_at__gte=timezone.now()).order_by("-created").first()


def _announce_timeout(msisdn: str) -> bool:
    """Tell the customer their flow ran out of time, then clear it. Returns
    whether anything had in fact expired.

    Worth a message of its own because the two windows are short by design: a
    customer who steps away mid-payment comes back to a thread that says what
    happened, rather than to silence and a payment of unknown status.
    """
    # An AUTHORISED payment is never announced here. "Nothing was charged" is
    # the one thing we cannot promise about a row whose money may already be
    # moving in the worker, and deleting it would erase the only record of a
    # payment that has to be reconciled rather than forgotten.
    expired = PendingAction.objects.filter(
        msisdn=msisdn, expires_at__lt=timezone.now()).exclude(state=EXECUTING_STATE)
    stale = expired.order_by("-created").first()
    if stale is None:
        return False
    armed = stale.state in _AWAITING_PIN_STATES
    expired.delete()
    mins = int((PIN_TTL if armed else FLOW_TTL).total_seconds() // 60)
    what = "That payment wasn't confirmed in time" if armed else "That request timed out"
    reply(msisdn, f"⌛ {what} — it expired after {mins} minute{'' if mins == 1 else 's'} "
                  "and nothing was charged.\n\nStart again whenever you're ready.")
    return True


def _clear_actions(msisdn: str) -> None:
    PendingAction.objects.filter(msisdn=msisdn).delete()


#: States in which a payment is armed and waiting for the customer to authorise
#: it — the chat PIN/SMS-code step, and the secure Flow's PIN pad.
_AWAITING_PIN_STATES = {"pin", FLOW_PIN_STATE}


def _flow_deadline(state: str):
    """When a flow in `state` goes stale.

    An armed payment and a half-typed one are not the same risk. Before the PIN
    step the flow holds answers — an amount, a meter number — and the customer
    may reasonably take a minute to find the next one. Once it is ARMED, it is a
    payment that will execute on six digits, and an armed payment left sitting in
    an unattended chat is the thing worth cutting short: whoever picks the phone
    up next should find an expired flow, not a live one.

    The armed window is deliberately not as short as it could be. The production
    path is the secure Flow's PIN pad — tap the card, wait for the native form,
    type six digits — and a window that expires mid-typing does not protect
    anyone, it just makes customers start over and type their PIN twice.
    """
    return timezone.now() + (PIN_TTL if state in _AWAITING_PIN_STATES else FLOW_TTL)


def _touch(pa: PendingAction, **fields) -> None:
    for k, v in fields.items():
        setattr(pa, k, v)
    # Computed from the state the flow is moving TO, so arming the confirm starts
    # the shorter clock in the same save that arms it.
    pa.expires_at = _flow_deadline(pa.state)
    pa.save()


# --------------------------------------------------------------------------- #
# Test-only, no-CLI simulation commands
# --------------------------------------------------------------------------- #
_SIMULATION_SETUP_COMMANDS = {
    "simulate setup", "simulation setup", "test setup", "setup simulation",
}
_SIMULATION_KYC_COMMANDS = {
    "simulate kyc", "simulate identity", "simulate verification",
}
_SIMULATION_DEPOSIT_COMMAND = re.compile(
    r"^(?:simulate|simulation|test)\s+(?:deposit|fund|funding)(?:\s+(.+))?$"
)


def _is_simulation_command(low: str) -> bool:
    return (low in _SIMULATION_SETUP_COMMANDS
            or low in _SIMULATION_KYC_COMMANDS
            or bool(_SIMULATION_DEPOSIT_COMMAND.fullmatch(low)))


def _chat_simulation_allowed() -> bool:
    """Both switches must say this is a deliberate fake-money deployment.

    An active WhatsAppLink authenticates which account is changed, so the
    command needs no shared secret in the chat. Turning WEMA_SIMULATION off
    removes the command's power even if the second switch is accidentally left.
    """
    wema_cfg = getattr(settings, "WEMA", {}) or {}
    deliberate = (getattr(settings, "ALLOW_PRODUCTION_SIMULATION", False)
                  or getattr(settings, "DEBUG", False)
                  or getattr(settings, "TESTING", False))
    return bool(wema_cfg.get("SIMULATION") and deliberate)


def _handle_simulation_command(user, msisdn: str, low: str) -> None:
    """Prepare the linked account for an end-to-end fake-money walkthrough.

    This runs before pending-action dispatch, so it can safely replace the BVN
    Flow currently open in the chat. It is deliberately absent from the menu.
    """
    if not _chat_simulation_allowed():
        return reply(msisdn, "🧪 Simulation commands are disabled on this deployment.")

    _clear_actions(msisdn)
    try:
        if low in _SIMULATION_SETUP_COMMANDS:
            from accounts.views import apply_simulated_kyc

            with db_transaction.atomic():
                account_number, state = apply_simulated_kyc(user, 3)
                wallet = get_or_create_wallet(user)
                target = Decimal("50000")
                gap = target - wallet.balance
                # Idempotent setup: bring a fresh account up to ₦50k, but do not
                # add another ₦50k when the command is repeated.
                if gap >= Decimal("100"):
                    wallet, _ = wallet_views.apply_simulated_deposit(user, gap)
            account_line = account_number or "mock account provisioning pending"
            return reply(
                msisdn,
                "🧪 *Simulation ready*\n\n"
                f"✅ Identity: Tier {state['tier']} (BVN/NIN simulated)\n"
                f"✅ Funding account: {account_line}\n"
                f"✅ Mock balance: {_money(wallet.balance)}\n\n"
                "No real BVN, Wema deposit or bank transfer was used. "
                "Reply *reset pin* next, then test balance and a small payment.",
            )

        if low in _SIMULATION_KYC_COMMANDS:
            # This is the realistic walkthrough: contact ownership is proved by
            # genuinely delivered Termii/Resend codes, while the two government-ID
            # lookups remain local mocks. Refuse to start if either real contact
            # rail is absent (or a fixed test OTP would short-circuit delivery).
            if _kyc_test_code(user):
                return reply(
                    msisdn,
                    "⚠️ A fixed test OTP is still enabled for this account. Disable it "
                    "before testing real SMS and email delivery.",
                )
            missing = []
            if not sms_live():
                missing.append("Termii SMS")
            if not email_live():
                missing.append("Resend email")
            if missing:
                return reply(
                    msisdn,
                    "⚠️ Interactive verification needs real " + " and ".join(missing)
                    + " delivery configured first.",
                )

            with db_transaction.atomic():
                user.phone_verified = False
                user.email_verified = False
                user.bvn_verified = False
                user.nin_verified = False
                # Erase earlier simulated identity markers. The digits entered in
                # the coming Flow are never stored; successful mock steps receive
                # fresh per-user simulation hashes in _kyc_submit_identity.
                user.bvn_hash = ""
                user.bvn_last4 = ""
                user.nin_hash = ""
                user.nin_last4 = ""
                user.recompute_tier()
                user.save(update_fields=[
                    "phone_verified", "email_verified", "bvn_verified", "nin_verified",
                    "bvn_hash", "bvn_last4", "nin_hash", "nin_last4", "tier",
                ])

            reply(
                msisdn,
                "🧪 *Interactive verification ready*\n\n"
                "📲 Phone code: real delivery through Termii\n"
                "📧 Email code: real delivery through Resend\n"
                "🪪 BVN and NIN: enter test 11-digit values in the private Flow; "
                "verification is simulated and no identity provider is contacted.",
            )
            return _start_kyc(user, msisdn)

        match = _SIMULATION_DEPOSIT_COMMAND.fullmatch(low)
        raw_amount = (match.group(1) if match else "") or "50000"
        amount = parse_amount(raw_amount)
        wallet, reference = wallet_views.apply_simulated_deposit(user, amount)
        return reply(msisdn, f"🧪 Simulated deposit credited: {_money(amount)}\n"
                      f"Balance: {_money(wallet.balance)}\nReference: {reference}")
    except ValueError as exc:
        return reply(msisdn, f"⚠️ {exc}")
    except Exception:
        log.exception("wa_simulation_command_failed user=%s", user.pk)
        return reply(msisdn, "⚠️ The simulation setup couldn't finish. Please try again shortly.")


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

    # A frozen/suspended account is blocked on WhatsApp too. The app/admin gate
    # frozen users at the token layer, but WhatsApp auth is link-bound (not token),
    # so without this a frozen fraud account could keep transacting over chat —
    # freeze is the primary incident-response lever and must cover every surface.
    if not user.is_active:
        _clear_actions(msisdn)
        return reply(msisdn, "Your Zitch account is currently suspended. Please contact support.")

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
        return send_menu(msisdn)

    # A test setup command must be able to escape the BVN/PIN Flow that is
    # currently open; every other message still belongs to the pending action.
    if _is_simulation_command(low):
        return _handle_simulation_command(user, msisdn, low)

    pa = _current_action(msisdn)
    if pa is not None:
        return _advance(pa, user, msisdn, text)
    # Nothing live — but if something just ran out, say so before treating this
    # message as the start of something new. The message itself is still handled
    # below, so "balance" after a timeout still answers with the balance.
    timed_out = _announce_timeout(msisdn)
    if timed_out and re.fullmatch(r"\d{4,6}", low.strip()):
        # ...except a PIN or code, which was plainly the answer to the flow that
        # just died. The stray-PIN warning below is true but not the point here,
        # and it would be the last thing they read.
        return None

    # Idle re-auth. WhatsApp's Chat Lock guards the window and we can neither
    # require nor verify it, so this guards the thing we can: what the bot will
    # reveal. Only reads are gated — every action already authenticates at the
    # point money moves, and prompting twice would be friction, not security.
    # Skip re-auth when we just announced a timeout — the expired payment's
    # "Confirm with PIN" card is still visible in the chat and a second card
    # right after the timeout message is confusing. The next command will
    # re-auth if still needed.
    if not timed_out and _needs_reauth(convo) and _is_sensitive_read(low):
        return _send_unlock(user, msisdn, text)

    # A bare 4-6 digit message with nothing expecting one is very often a PIN
    # typed out of habit. It is already masked in our log, but it is still in
    # the customer's own thread and only they can remove it — WhatsApp gives a
    # business no way to delete or expire a message it received.
    # \d{4,6}, the same shape the webhook masks as [PIN] — it was \d{4}|\d{6},
    # which let a stray 5-digit code fall past this branch into the AI layer
    # while the log called it a PIN. The two rules should not disagree on what
    # a PIN looks like.
    if re.fullmatch(r"\d{4,6}", low):
        # The disappearing-messages tip is WhatsApp's ONE real expiry lever, and
        # it is the customer's to pull, not ours: a business cannot enable it by
        # API, delete a received message, or send view-once text. Everything
        # else we do (Flows, masking) keeps secrets out of the thread — this
        # tip is for the ones the customer puts there themselves.
        return reply(msisdn, "⚠️ That looks like a *PIN or code*, and nothing here was waiting for one.\n\n"
                             "We never ask for your PIN in this chat — please delete that message "
                             "(press and hold → Delete → *Delete for everyone*).\n\n"
                             "💡 Tip: turn on *disappearing messages* for this chat (tap our name "
                             "→ Disappearing messages → 24 hours) so anything sent here expires "
                             "on its own.\n\n"
                             "Reply \"menu\" for options.")

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
        return _start_vtu(user, msisdn)
    if low in ("electricity", "light", "nepa", "power"):
        return _start_electricity(user, msisdn)
    if low in ("cable", "tv", "dstv", "gotv", "startimes"):
        return _start_cable(user, msisdn)
    if low in ("4", "bill", "bills", "pay bill"):
        return _start_service_menu(user, msisdn, "bill")
    if low in ("5", "convert", "conversion"):
        return _start_convert(user, msisdn)
    if low in ("7", "account", "my account", "account details", "my details", "details"):
        return _do_account_details(user, msisdn)
    if low in ("support", "customer care", "care", "contact", "contact us", "info", "more info"):
        return _do_support(msisdn)
    if low in ("lock", "lock chat", "chat lock", "fingerprint", "face id", "biometric",
               "biometrics", "secure chat"):
        return reply(msisdn, _chat_lock_tip())
    if low in ("9", "history", "transactions", "my transactions", "recent"):
        return _do_history(user, msisdn)
    # A statement is the one history request that explicitly wants the FILE.
    if low in ("statement", "download statement", "bank statement", "account statement", "pdf"):
        return _do_history(user, msisdn, as_document=True)
    # Escalation without the AI: the deterministic path must reach a human too,
    # or turning smart replies off would silently remove the ability to complain.
    if low in ("report a problem", "report problem", "escalate", "complain", "complaint",
               "dispute", "raise a case", "report"):
        return _start_problem_report(user, msisdn)
    if low in ("reset pin", "change pin", "forgot pin", "new pin", "set pin", "pin"):
        return _start_pin_reset(user, msisdn)
    if low in ("8", "verify", "verify me", "verify identity", "kyc", "upgrade", "limits"):
        return _start_kyc(user, msisdn)
    if low in ("ai on", "enable ai", "ai off", "disable ai", "ai"):
        return _do_ai_consent(link, msisdn, low)

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
            # The model knowing WHY it could not act is the useful part, and we
            # were discarding it for a generic menu. "Sorry, I didn't get that"
            # under a request the assistant understood perfectly well — and could
            # explain — reads as broken rather than as a limit.
            # Sanitised in ai.extract_intent before it ever reached here — this
            # is the only free-form model text a customer reads, so it is
            # re-checked rather than trusted twice. An empty result means the
            # text was refused, and the menu below is the answer.
            reason = ai.safe_reason((intent.get("input") or {}).get("reason") or "")
            if reason:
                return reply(msisdn, f"🤔 {reason}\n\n" + menu_text())
    return reply(msisdn, "Sorry, I didn't get that.\n\n" + menu_text())


# --------------------------------------------------------------------------- #
# linking
# --------------------------------------------------------------------------- #
#: Commands that reveal money or identity to whoever is holding the phone.
#: Deliberately reads only: a transfer already needs biometrics or the PIN to
#: complete, so gating its FIRST message would prompt twice for one movement.
#: EVERY synonym the dispatcher accepts for a gated read must appear here.
#: "details"/"my details" route to _do_account_details exactly as "7" and
#: "account details" do, so omitting them let the same PII (full name, phone,
#: email, tier, account number) out without the idle challenge the other
#: synonyms trigger — the gate is only as strong as its least-covered alias.
_SENSITIVE_READS = {
    "1", "balance", "bal", "my balance", "check balance",
    "7", "account", "account details", "my account", "account number",
    "details", "my details",
    "statement", "history", "transactions", "my transactions", "9", "recent",
}
_REAUTH_SETTING = "wa_reauth_idle_minutes"


def _reauth_window() -> timedelta:
    """How long a proven identity stays good for. 0 disables the gate."""
    fallback = getattr(settings, "WA_REAUTH_IDLE_MINUTES", 15)
    try:
        minutes = int(SystemSetting.get(_REAUTH_SETTING, "") or fallback)
    except (TypeError, ValueError):
        minutes = fallback
    return timedelta(minutes=max(0, minutes))


def _is_sensitive_read(low: str) -> bool:
    return low.strip() in _SENSITIVE_READS


def _needs_reauth(convo: ConversationState) -> bool:
    window = _reauth_window()
    if not window:
        return False
    return convo.last_verified is None or timezone.now() - convo.last_verified > window


def _mark_verified(msisdn: str) -> None:
    """Called the moment an identity is proven, whichever way it was proven."""
    convo = ConversationState.for_msisdn(msisdn)
    convo.last_verified = timezone.now()
    convo.save(update_fields=["last_verified"])


def _send_unlock(user, msisdn: str, resume: str) -> None:
    """Ask the customer to prove it is them before revealing anything, then run
    what they originally asked for.

    Reuses the confirm machinery unchanged, so unlocking offers the same
    biometric-first hand-off into the app with the encrypted PIN Flow behind it —
    the two things we can actually prove happened.
    """
    pa = _new_flow(user, msisdn, "unlock", "pin", {"pin_attempts": 0, "resume": resume[:64]})
    if not _arm_confirm(pa, user):
        return
    _send_confirm(pa, msisdn, "🔒 *Welcome back.* It's been a while, so confirm it's you "
                              "before I show your account.")


def _exec_unlock(pa: PendingAction, user, msisdn: str) -> str:
    """Identity proven: start the window and run the command that triggered it."""
    resume = str(pa.payload.get("resume") or "").strip()
    _clear_actions(msisdn)
    _mark_verified(msisdn)
    if resume:
        handle_inbound(msisdn, resume)
    return "Unlocked ✅ — see the chat."


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
        # Fail CLOSED: `phone` is nullable, so an account with no number on file has
        # nothing to match against — binding anyway would let a leaked code attach an
        # attacker's WhatsApp to that account. Require a registered number that matches.
        if not registered or registered[-10:] != sender[-10:]:
            # Burn it. A code arriving from a number that is not the account's is
            # the exact shape of a leaked or shoulder-surfed code being tried from
            # an attacker's WhatsApp; leaving it live would let them keep trying
            # from other numbers. The owner can mint a fresh one in the app.
            link.link_code = ""
            link.save(update_fields=["link_code"])
            return reply(msisdn, "For your security, send this code from the phone number on your Zitch account. "
                                 "That code has now expired — generate a new one in the Zitch app.")
        link.wa_msisdn = msisdn
        link.status = WhatsAppLink.ACTIVE
        link.link_code = ""
        link.linked_at = timezone.now()
        link.save(update_fields=["wa_msisdn", "status", "link_code", "linked_at"])
        name = (link.user.first_name or "there").strip()
        # The moment this thread becomes a banking channel is the moment the
        # chat-lock tip is worth reading, so it rides on the link confirmation
        # rather than waiting for someone to go looking for it.
        reply(msisdn, f"✅ *Linked!* Hi {name}, your WhatsApp is now connected to Zitch.\n\n"
                      + _chat_lock_tip())
        return send_menu(msisdn)

    # 3. Brand-new number: offer to create an account or link an existing one.
    # Link is tested first — "i already have an account" names an account but is
    # asking for the opposite of a signup.
    if low in ("2", "link", "link account", "i have an account", "sign in", "login", "log in") \
            or LINK_INTENT.search(low):
        return reply(msisdn, "To connect an existing account, open the Zitch app → *Settings → Link WhatsApp*, get your code, and send it here.")
    if low in ("1", "create", "create account", "sign up", "signup", "register", "open account", "new", "get started") \
            or CREATE_INTENT.search(low):
        return _start_onboarding(msisdn)

    # 4. Default welcome (with the create/link choices).
    intro = UNLINKED if _chat_signup_allowed() else UNLINKED_APP_ONLY
    block = _more_info_block()
    return reply(msisdn, intro + (f"\n\n{block}" if block else ""))


# --------------------------------------------------------------------------- #
# onboarding (create a Zitch account from WhatsApp) — phone-only Tier 1; BVN in
# the app unlocks sending. The PIN is set in chat (masked in the log) and stored
# hashed, never in clear.
# --------------------------------------------------------------------------- #
def _start_onboarding(msisdn: str) -> None:
    if not _chat_signup_allowed():
        _clear_onboarding(msisdn)
        return reply(msisdn, UNLINKED_APP_ONLY)
    if User.objects.filter(phone=_local_phone(msisdn)).exists():
        return reply(msisdn, "This number already has a Zitch account. Open the app → *Settings → Link WhatsApp* to connect it here.")
    # One private form for names + email, chained into the PIN pair on the same
    # open Flow — the whole signup with zero chat round-trips. Names and an
    # email address are not secrets, so unlike the PIN this falls back to the
    # chat question-by-question path when Flows are unavailable.
    if flows_live():
        ob, _ = WaOnboarding.objects.update_or_create(
            msisdn=msisdn,
            defaults={"step": FLOW_SIGNUP_STATE, "payload": {},
                      "expires_at": timezone.now() + ONBOARD_TTL},
        )
        res = send_flow(
            msisdn, sign_onboarding_token(ob),
            header="Create your Zitch account",
            body="Your details go into a private form — they never appear in this chat.",
            screen=SIGNUP_SCREEN, screen_data={"error": ""}, cta="Create account",
        )
        if res.get("success"):
            return reply(msisdn, "🎉 Tap *Create account* on the secure form above to get started.")
        log.warning("wa_signup_flow_send_failed msisdn=%s detail=%r",
                    mask_pii(msisdn), res.get("error_detail", ""))
    WaOnboarding.objects.update_or_create(
        msisdn=msisdn,
        defaults={"step": "first_name", "payload": {}, "expires_at": timezone.now() + ONBOARD_TTL},
    )
    reply(msisdn, "Let's set up your Zitch account \U0001f389\n\nWhat's your *first name*?")


def _pin_in_chat_allowed() -> bool:
    """A PIN may only be typed into the chat in dev/test. Production never asks:
    WhatsApp has no delete-or-expire for a message a business received, so a PIN
    sent as chat text stays in the customer's history for good."""
    return bool(getattr(settings, "DEBUG", False) or getattr(settings, "TESTING", False))


def _arm_onboarding_pin(ob: WaOnboarding, msisdn: str) -> None:
    """Collect the signup PIN over the most private channel available, in the same
    order the money flows use:

    1. The secure Flow — a native masked field, submitted encrypted. The PIN is
       never a chat message, so there is nothing left in the thread afterwards.
       WhatsApp has no way to delete or expire a message once sent, so not sending
       one is the only thing that actually keeps a PIN out of the history.
    2. Dev/test only: the chat, masked in our log.
    3. Production without Flows: no PIN in chat, ever. The account is created
       without one and the PIN is set in the app, where it belongs.
    """
    if flows_live():
        # Opens on the root, not the twin, and with a fresh screen budget — the
        # signup ladder parks flow_screen on PIN_CHAIN (and PIN_RETRY after a
        # refusal), and a stale value here would answer a screen this message did
        # not open on.
        ob.payload["flow_screen"] = PIN_SCREEN
        ob.payload["pin_policy_tries"] = 0
        ob.payload["pin_confirm_tries"] = 0
        ob.save(update_fields=["payload"])
        _onboard_to(ob, FLOW_PIN_STATE)
        res = send_flow(
            msisdn, sign_onboarding_token(ob),
            header="Set your PIN", body="Choose the 6-digit PIN you'll use to authorise payments.",
            screen=PIN_SCREEN,
            # Built by the shared builder, never inline. This send is where the
            # drift bug bit before: PIN_SCREEN's schema changed under it and the
            # hand-written dict kept sending the retired "summary" key, which
            # Meta rejects as an undeclared property — so every signup PIN send
            # failed the moment the new Flow was published, and signup silently
            # fell down its fallback rungs.
            screen_data=pin_screen_send_data(
                "Create a 6-digit PIN", details="You'll enter it again to confirm"),
        )
        if res.get("success"):
            return reply(msisdn, "🔐 Tap the secure screen above to set your *6-digit PIN*. "
                                 "It's typed privately and never appears in this chat.")
        log.warning("wa_onboarding_pin_flow_failed msisdn=%s detail=%r", msisdn,
                    res.get("error_detail", ""))
    if _pin_in_chat_allowed():
        _onboard_to(ob, "pin")
        return reply(msisdn, "Create a *6-digit PIN* to authorise payments (any 6 digits — keep it secret).")
    # No secure channel: finish the signup without a PIN rather than ask for one
    # in a chat that keeps it forever. Everything that spends money already
    # requires a PIN, so the account is simply not spendable until it is set.
    return _finish_onboarding(ob, msisdn, "")


def _onboard_to(ob: WaOnboarding, step: str) -> None:
    ob.step = step
    ob.expires_at = timezone.now() + ONBOARD_TTL
    ob.save(update_fields=["step", "payload", "expires_at"])


def _advance_onboarding(ob: WaOnboarding, msisdn: str, text: str) -> None:
    if not _chat_signup_allowed():
        _clear_onboarding(msisdn)
        return reply(msisdn, UNLINKED_APP_ONLY)
    val = text.strip()
    if val.lower() in ("cancel", "quit", "stop"):
        _clear_onboarding(msisdn)
        return reply(msisdn, "No problem — signup cancelled. Reply *1* to start again anytime.")
    if ob.step == FLOW_SIGNUP_STATE:
        return reply(msisdn, "📝 Please fill the secure *Create account* form above — "
                             "or reply \"cancel\" to start over.")
    if ob.step == FLOW_EMAIL_CODE_STATE:
        # The code is a bearer credential for 15 minutes; typed here it sits in
        # the customer's own history. Same advice as a chat-typed PIN.
        if re.fullmatch(r"\d{4,8}", val):
            return reply(msisdn, "📧 Please enter the code on the *secure screen* above — not in "
                                 "the chat. Delete the message you just sent (press and hold → "
                                 "Delete → *Delete for everyone*), then tap the secure screen.")
        return reply(msisdn, "📧 Tap the *secure screen* above to enter your email code, "
                             "or reply \"cancel\".")
    if ob.step == FLOW_PHONE_STATE:
        return reply(msisdn, "📱 Please enter your phone number on the *secure screen* above — "
                             "or reply \"cancel\" to start over.")
    if ob.step == FLOW_PHONE_CODE_STATE:
        if re.fullmatch(r"\d{4,8}", val):
            return reply(msisdn, "📲 Please enter the code on the *secure screen* above — not in "
                                 "the chat. Delete the message you just sent (press and hold → "
                                 "Delete → *Delete for everyone*), then tap the secure screen.")
        return reply(msisdn, "📲 Tap the *secure screen* above to enter the SMS code, "
                             "or reply \"cancel\".")
    if ob.step == FLOW_PIN_STATE:
        # The PIN belongs in the secure screen, never here. If they typed one
        # anyway it is already masked in our log — but it is still sitting in
        # their own chat, and only they can remove it.
        if re.fullmatch(r"\d{4,6}", val):
            return reply(msisdn, "🔐 Please set your PIN on the *secure screen* above — not in the chat. "
                                 "Delete the message you just sent (press and hold → Delete → "
                                 "*Delete for everyone*), then tap the secure screen.")
        return reply(msisdn, "🔐 Tap the *secure screen* above to set your PIN, or reply \"cancel\".")
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
        _onboard_to(ob, "email")
        return reply(msisdn, "What's your *email address*? You'll confirm it in the Zitch app when you verify your identity.")
    if ob.step == "email":
        email = val.lower()
        if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return reply(msisdn, "That doesn't look like an email address. Please enter it like *name@example.com*.")
        if User.objects.filter(email__iexact=email).exists():
            # Recovery looks accounts up by email, so two accounts sharing one
            # address would make reset codes ambiguous. Refuse here, at entry.
            return reply(msisdn, "That email is already on a Zitch account. Enter a different email address.")
        ob.payload["email"] = email
        return _arm_onboarding_pin(ob, msisdn)
    if ob.step == "pin":
        if not re.fullmatch(r"\d{6}", val):
            return reply(msisdn, "Your PIN must be exactly 6 digits. Try again.")
        ob.payload["pin_hash"] = make_password(val)  # never store the raw PIN
        _onboard_to(ob, "pin_confirm")
        return reply(msisdn, "Great — re-enter your *6-digit PIN* to confirm.")
    if ob.step == "pin_confirm":
        if not re.fullmatch(r"\d{6}", val) or not check_password(val, ob.payload.get("pin_hash", "")):
            ob.payload["pin_hash"] = ""
            _onboard_to(ob, "pin")
            return reply(msisdn, "Those didn't match. Let's set it again — create your *6-digit PIN*.")
        return _finish_onboarding(ob, msisdn, val)
    _clear_onboarding(msisdn)
    return reply(msisdn, UNLINKED)


def _finish_onboarding(ob: WaOnboarding, msisdn: str, pin: str) -> None:
    wa_local = _local_phone(msisdn)
    # The account phone is the one TYPED on the signup form when there is one —
    # a customer may bank on a different line than they chat on. Falls back to
    # the WhatsApp number for the chat-question path, which never asks.
    local = (ob.payload.get("phone") or "").strip() or wa_local
    fn = (ob.payload.get("first_name") or "").strip()
    ln = (ob.payload.get("last_name") or "").strip()
    if User.objects.filter(phone=local).exists():  # raced with the app / another signup
        _clear_onboarding(msisdn)
        return reply(msisdn, "This number already has a Zitch account — open the app to link it.")
    # WhatsApp onboarding creates an UNVERIFIED account at Tier 0, identically to
    # the app: only name + PIN are collected here (no BVN/NIN), and the app's tier
    # ladder (recompute_tier) requires BVN + NIN for Tier 1. The user raises their
    # tier by verifying their identity in the app.
    user = User.objects.create(
        username=local, phone=local, first_name=fn, last_name=ln, tier=0,
        email=(ob.payload.get("email") or "").strip().lower(),
        onboarded_via_whatsapp=True,   # gates KYC on in-app email re-verification
        # Verified when the code round-trip happened INSIDE the signup flow;
        # otherwise unverified until the KYC ladder's OTP.
        email_verified=bool(ob.payload.get("email_verified_flow")),
        # Typing the number you are chatting from proves possession — the chat
        # session IS the phone. A different number is stored unverified and
        # gets the SMS round-trip in the ladder.
        phone_verified=(local == wa_local) or bool(ob.payload.get("phone_verified_flow")),
    )
    user.set_unusable_password()       # no app password yet; "Forgot password" sets one
    if pin:
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
        f"Your current transfer limit is *₦{user.daily_transfer_limit:,.0f}/day*. "
        "You can pay bills, buy airtime & data, and check your balance here.\n\n"
        # Said out loud, because an empty wallet is the state EVERY new account
        # starts in and nothing else in this message mentions it. A customer who
        # finishes signup and goes straight to "send 5k" meets an insufficient-
        # balance refusal as their first real interaction — which reads as the
        # account not working, rather than as the one step nobody told them
        # about. The account number this points at is minted just below.
        "💰 *Next: add money.* Your wallet starts at ₦0 — reply *6* any time for "
        "your Zitch account number and transfer to it from any bank.\n\n"
        + ("" if pin else
           "🔐 Set your *transaction PIN* in the Zitch app before you send money — "
           "we never collect a PIN in this chat.\n\n")
        # Verification runs now, not "later": account setup below collects the ID
        # and the SMS code, then rolls into whatever is left. Only when the bank
        # integration is off is there nothing to roll into, so only then is the
        # customer told to start it themselves.
        + ("" if wallet_views._wema_funding_enabled() else
           "To verify your identity, reply *8* — we'll do your phone, email, BVN "
           "and NIN right here.\n\n")
        + "🔒 *Tip:* lock this chat with your fingerprint — tap our name above → "
          "*Chat lock*. Reply *lock* for the steps.\n\n"
        + menu_text()
        + "\n\n📋 *Note:* verify your *BVN and NIN* — reply *8* — before your "
          "personal Zitch account number can be created.",
    )
    # Roll straight into minting their funding NUBAN — a wallet you can't pay
    # into isn't much of an account. Skipped quietly when the bank integration
    # is off; option 6 offers the same setup any time.
    if wallet_views._wema_funding_enabled():
        _start_add_account(user, msisdn, after_signup=True)


def send_onboarding_email_code(ob: WaOnboarding) -> bool:
    """Mint, arm and email the signup confirmation code. False when this deploy
    cannot actually deliver one — send_email silent-succeeds unkeyed, so the
    rail is checked first, exactly like the KYC ladder's sends."""
    local = _local_phone(ob.msisdn)
    test_code = (settings.TEST_OTP_CODE
                 if getattr(settings, "TEST_OTP_PHONE", "") == local else "")
    if not email_live() and not test_code:
        return False
    code = test_code or f"{secrets.randbelow(10**6):06d}"
    from accounts.views import _branded_email

    if not test_code:
        sent = send_email(ob.payload.get("email", ""), "Confirm your email for Zitch",
                          f"Your Zitch email confirmation code is {code}",
                          html=_branded_email(
                              "Confirm your email",
                              "Enter this code on the secure WhatsApp screen to finish "
                              "creating your Zitch account.",
                              code=code,
                              note="This code expires in 15 minutes. If you didn't request "
                                   "it, you can ignore this email — no account is created "
                                   "without it."))
        if not sent.get("success"):
            return False
    ob.payload.update({"email_code_hash": make_password(code),
                       "email_code_exp": (timezone.now() + timedelta(minutes=15)).isoformat(),
                       "email_code_attempts": 0})
    return True


def send_onboarding_phone_code(ob: WaOnboarding) -> bool:
    """Mint, arm and SMS the phone confirmation code — for a typed number that
    is NOT the one they are chatting from (that one is proven by the session).
    False when this deploy cannot deliver an SMS, so the ladder skips rather
    than dead-ends; the KYC ladder re-verifies later."""
    typed = ob.payload.get("phone", "")
    test_code = (settings.TEST_OTP_CODE
                 if getattr(settings, "TEST_OTP_PHONE", "") == typed else "")
    if not sms_live() and not test_code:
        return False
    code = test_code or f"{secrets.randbelow(10**6):06d}"
    if not test_code:
        sent = send_sms(typed, f"Zitch: {code} is your phone confirmation code. "
                               "It expires in 15 minutes. Never share it.")
        if not sent.get("success"):
            return False
    ob.payload.update({"phone_code_hash": make_password(code),
                       "phone_code_exp": (timezone.now() + timedelta(minutes=15)).isoformat(),
                       "phone_code_attempts": 0})
    return True


def check_onboarding_phone_code(ob: WaOnboarding, code: str):
    """("ok", "") verified · ("retry", why) · ("unverified", note) move on."""
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    if len(digits) != 6:
        return "retry", "The code is exactly 6 digits — check the SMS and try again."
    exp = ob.payload.get("phone_code_exp", "")
    if not ob.payload.get("phone_code_hash") or (
            exp and timezone.now() > timezone.datetime.fromisoformat(exp)):
        return "unverified", "That code expired — we'll verify your number later (reply 8)."
    if not check_password(digits, ob.payload["phone_code_hash"]):
        attempts = int(ob.payload.get("phone_code_attempts") or 0) + 1
        ob.payload["phone_code_attempts"] = attempts
        ob.save(update_fields=["payload"])
        if attempts >= 3:
            return "unverified", ("That's 3 incorrect codes — we'll verify your number "
                                  "later (reply 8).")
        return "retry", f"That code isn't right. {3 - attempts} attempt(s) left."
    ob.payload["phone_verified_flow"] = True
    for key in ("phone_code_hash", "phone_code_exp", "phone_code_attempts"):
        ob.payload.pop(key, None)
    ob.save(update_fields=["payload"])
    return "ok", ""


def check_onboarding_email_code(ob: WaOnboarding, code: str):
    """("ok", "") verified · ("retry", why) ask again · ("unverified", note)
    move on without verification — three wrong codes or an expired code must
    not dead-end a signup; the KYC ladder re-verifies email later."""
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    if len(digits) != 6:
        return "retry", "The code is exactly 6 digits — check the email and try again."
    exp = ob.payload.get("email_code_exp", "")
    if not ob.payload.get("email_code_hash") or (
            exp and timezone.now() > timezone.datetime.fromisoformat(exp)):
        return "unverified", "That code expired — we'll verify your email later (reply 8)."
    if not check_password(digits, ob.payload["email_code_hash"]):
        attempts = int(ob.payload.get("email_code_attempts") or 0) + 1
        ob.payload["email_code_attempts"] = attempts
        ob.save(update_fields=["payload"])
        if attempts >= 3:
            return "unverified", ("That's 3 incorrect codes — we'll verify your email "
                                  "later (reply 8).")
        return "retry", f"That code isn't right. {3 - attempts} attempt(s) left."
    ob.payload["email_verified_flow"] = True
    for key in ("email_code_hash", "email_code_exp", "email_code_attempts"):
        ob.payload.pop(key, None)
    ob.save(update_fields=["payload"])
    return "ok", ""


def finish_onboarding_from_flow(ob: WaOnboarding, pin: str) -> str:
    """Complete a signup whose PIN was set in the secure Flow. Returns the terminal
    message for the Flow's success screen; the chat welcome + account setup are
    sent by _finish_onboarding as usual."""
    msisdn = ob.msisdn
    _finish_onboarding(ob, msisdn, pin)
    return "✅ PIN set — your Zitch account is ready. Head back to the chat."


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
        f"👤 {wallet.account_name}\n\n"
        # The number on its own is not an instruction. This is the screen a new
        # customer reaches at the end of signup, so it should close on what to do
        # next and how they will know it worked, rather than leaving them to
        # infer both from an account number.
        "Send money to it whenever you're ready, then reply *1* to see your balance.",
    )


def _do_add_money(user, msisdn: str) -> None:
    """Show the user's dedicated Zitch account for bank-transfer funding (credited
    automatically by the reconcile_wema poller) — or, if it hasn't been minted
    yet, run the identity + OTP round-trip right here in the chat."""
    wallet = get_or_create_wallet(user)
    if wallet.account_number:
        return _send_account_details(msisdn, wallet)
    return _start_add_account(user, msisdn)


#: Proving who you are before replacing the credential that authorises payments.
#: Deliberately the contact channels AND an identity number: a SIM swap defeats
#: the phone alone, and a mailbox breach defeats the email alone.
_PIN_RESET_CHECKS = (("email_verified", "email address"),
                     ("phone_verified", "phone number"),
                     ("bvn_verified", "BVN"))


def _start_pin_reset(user, msisdn: str) -> None:
    """Set or replace the transaction PIN, in the encrypted Flow.

    Anyone holding this chat can reach this, and a PIN reset hands over the one
    credential that moves money — so the bar is the verified identity itself,
    not possession of the thread. An unverified account is sent to verification
    rather than being told to contact support, because that is the actual next
    step.
    """
    missing = [label for field, label in _PIN_RESET_CHECKS if not getattr(user, field, False)]
    if missing:
        listed = ", ".join(missing[:-1]) + " and " + missing[-1] if len(missing) > 1 else missing[0]
        _clear_actions(msisdn)
        return reply(msisdn, f"🔐 To set a new PIN we first need to verify your {listed} — "
                             "your PIN authorises payments, so we confirm it's really you.\n\n"
                             "Reply *8* to verify now.")
    _clear_actions(msisdn)
    # The verified flags prove who the account belongs to; they do not prove who
    # is HOLDING this chat today. A messenger session outlives a SIM swap, and a
    # PIN reset hands over the credential that moves money — so the reset opens
    # on a live SMS code to the account phone, and only the code advances to the
    # PIN pair (same flow session, next page). Dev/test deploys skip the code:
    # they have no SMS rail and the suite exercises the pair directly.
    payload = {"pin_attempts": 0}
    skip_otp = getattr(settings, "TESTING", False) or settings.DEBUG
    if not skip_otp:
        code = _kyc_test_code(user) or f"{secrets.randbelow(10**6):06d}"
        if not sms_live() and not _kyc_test_code(user):
            # "Must" means must: no deliverable code, no chat reset. The app has
            # its own authentication and stays available.
            return reply(msisdn, "🔐 We couldn't send the confirmation SMS just now, so the "
                                 "PIN can't be reset here. Please try again shortly, or set "
                                 "your PIN in the Zitch app (Me → Security).")
        if not _kyc_test_code(user):
            sent = send_sms(user.phone or "",
                            f"Zitch: {code} is your PIN reset code. It expires in 10 minutes. "
                            "Never share it.")
            if not sent.get("success"):
                return reply(msisdn, "🔐 We couldn't send the confirmation SMS just now, so the "
                                     "PIN can't be reset here. Please try again shortly, or set "
                                     "your PIN in the Zitch app (Me → Security).")
        payload.update({
            "pin_reset_otp_hash": make_password(code),
            "pin_reset_otp_exp": (timezone.now() + timedelta(minutes=10)).isoformat(),
            "pin_reset_otp_attempts": 0,
            "flow_screen": CODE_SCREEN,
        })
    pa = _new_flow(user, msisdn, "setpin", FLOW_PIN_STATE, payload)
    if payload.get("pin_reset_otp_hash"):
        masked = f"•••••{(user.phone or '')[-4:]}"
        if flows_live() and send_flow(
                msisdn, sign_flow_token(pa),
                header="Reset your PIN", body="Confirm it's you, then choose your new PIN — "
                                             "all on the secure screen.",
                screen=CODE_SCREEN,
                screen_data={"summary": f"Enter the code we sent by SMS to {masked}",
                             "label": "PIN reset code", "error": ""},
                cta="Reset PIN").get("success"):
            return reply(msisdn, "🔐 We sent a code by SMS. Enter it on the secure screen above, "
                                 "then choose your new *6-digit PIN*.")
        _clear_actions(msisdn)
        return reply(msisdn, "🔐 Secure PIN entry isn't available right now. "
                             "Please set your PIN in the Zitch app (Me → Security).")
    if flows_live() and send_flow(
            msisdn, sign_flow_token(pa),
            header="Set your PIN", body="Choose the 6-digit PIN you'll use to authorise payments.",
            screen=PIN_SCREEN,
            screen_data=pin_screen_send_data(
                "Create a 6-digit PIN", details="You'll enter it again to confirm"),
            cta="Set PIN").get("success"):
        return reply(msisdn, "🔐 Tap the secure screen above to set your new *6-digit PIN*. "
                             "It never appears in this chat.")
    # No fail-open here: unlike a BVN, a PIN typed into a thread is the
    # credential itself sitting in the customer's history forever.
    _clear_actions(msisdn)
    return reply(msisdn, "🔐 Secure PIN entry isn't available right now. "
                         "Please set your PIN in the Zitch app (Me → Security).")


def _do_ai_consent(link: WhatsAppLink, msisdn: str, low: str) -> None:
    """Turn the AI intent layer on or off for THIS customer.

    The consent is the customer's — their free-form messages are what would be
    sent to a third-party model — so they grant it themselves. It defaults off
    and previously had no way to be turned on at all, which left the whole AI
    layer unreachable however the operator configured it.
    """
    if low in ("ai on", "enable ai"):
        if not link.ai_enabled:
            link.ai_enabled = True
            link.save(update_fields=["ai_enabled"])
        return reply(msisdn, "🤖 *Smart replies are on.* You can now type naturally — "
                             "\"send 5k to my brother\", \"buy 1000 airtime\".\n\n"
                             "Your messages are read by an AI assistant to work out what you want. "
                             "Account numbers and PINs are never sent to it, and every payment still "
                             "needs your confirmation and PIN.\n\nReply *ai off* to turn this off.")
    if low in ("ai off", "disable ai"):
        if link.ai_enabled:
            link.ai_enabled = False
            link.save(update_fields=["ai_enabled"])
        return reply(msisdn, "🤖 *Smart replies are off.* The menu and keywords work as always.")
    state = "on" if link.ai_enabled else "off"
    return reply(msisdn, f"🤖 Smart replies are currently *{state}*.\n\n"
                         "With them on you can type naturally instead of using the menu. "
                         "Reply *ai on* or *ai off*.")


_STATUS_LABEL = {"success": "successful ✅", "pending": "still pending ⏳", "failed": "not successful ❌"}

#: How a customer's word for a transaction type maps onto the ledger's `service`
#: text. Matched against the service label because that is what the row actually
#: carries — there is no type column to filter on.
_KIND_PATTERNS = {
    "transfer": r"transfer|sent|withdraw",
    "airtime": r"airtime",
    "data": r"data",
    "bill": r"electric|cable|tv|bill|disco|water",
    "funding": r"fund|deposit|top.?up|credit",
}

#: A day either side of the day the customer named. People say "2 days ago" for
#: something that happened 58 hours back, and a window that took them literally
#: would answer "I can't find it" about a payment sitting right there.
_DAY_FUZZ = 1

#: Naira either side of a stated amount, so "5k" still matches ₦5,000 sent with a
#: ₦10.75 fee folded in, or a ₦4,950 transfer the customer rounded when retelling.
_AMOUNT_FUZZ = Decimal("100")


def _status_of(t) -> str:
    from wallet.models import Transaction

    if t.transaction_status == Transaction.SUCCESS:
        return "success"
    if t.transaction_status == Transaction.FAILED:
        return "failed"
    return "pending"


def _find_txns(user, *, amount=None, days_ago=None, kind=None, recipient=None,
               status=None, reference=None, limit=6) -> list:
    """The customer's own description of a transaction -> the matching ledger rows.

    Every filter is optional and narrows independently, because a message gives
    whatever it gives ("the 5k from Tuesday", "that transfer to Ada", "ZT-1234").
    Deliberately fuzzy on amount and date — see the constants above. Newest first,
    so a tie between two matching rows is broken toward the one most likely to be
    on the customer's mind.
    """
    # Imported locally and aliased: this module already binds `time` to the
    # stdlib module (time.monotonic in the settle poll), so datetime.time cannot
    # take that name at module scope.
    from datetime import datetime as _datetime, time as _time

    from wallet.models import Transaction

    qs = Transaction.objects.filter(user=user)
    if reference:
        # An exact reference is the customer quoting our own receipt back at us:
        # it identifies one row, so nothing else may narrow it further.
        return list(qs.filter(reference__iexact=str(reference).strip())[:1])
    if amount is not None:
        try:
            target = Decimal(str(amount))
            qs = qs.filter(amount__gte=target - _AMOUNT_FUZZ, amount__lte=target + _AMOUNT_FUZZ)
        except (InvalidOperation, TypeError):
            pass
    if days_ago is not None:
        try:
            day = timezone.localdate() - timedelta(days=max(0, int(days_ago)))
            start = timezone.make_aware(_datetime.combine(day - timedelta(days=_DAY_FUZZ), _time.min))
            end = timezone.make_aware(_datetime.combine(day + timedelta(days=_DAY_FUZZ), _time.max))
            qs = qs.filter(created__gte=start, created__lte=end)
        except (TypeError, ValueError):
            pass
    if kind and _KIND_PATTERNS.get(kind):
        qs = qs.filter(service__iregex=_KIND_PATTERNS[kind])
    if recipient:
        # Names reach the ledger via the service label ("Transfer to ADA OKON"),
        # so a partial, case-insensitive match is the only kind available.
        qs = qs.filter(service__icontains=str(recipient).strip()[:40])
    if status:
        wanted = {"failed": Transaction.FAILED, "pending": Transaction.PENDING,
                  "successful": Transaction.SUCCESS, "success": Transaction.SUCCESS}.get(status)
        if wanted:
            qs = qs.filter(transaction_status=wanted)
    return list(qs.order_by("-created")[:limit])


def _txn_line(t) -> str:
    """One transaction as the customer should read it: outcome first."""
    from wallet.models import Transaction

    sign = "＋" if t.direction == Transaction.IN else "－"
    label = (t.service or "").strip() or ("Credit" if t.direction == Transaction.IN else "Debit")
    icon = {"success": "✅", "pending": "⏳", "failed": "❌"}[_status_of(t)]
    return f"{icon} {sign}{_money(t.amount)}  ·  {label}\n     _{t.created:%d %b, %I:%M %p}_"


def _do_history(user, msisdn: str, count=None, *, amount=None, days_ago=None,
                kind=None, recipient=None, status=None, as_document=None) -> None:
    """Answer a question about past activity.

    Two different questions wear the same clothes here, and answering the wrong
    one is what made the assistant feel deaf: "send me a statement" wants a
    LIST (and a file), while "I sent 5k to someone 2 days ago, help me check"
    wants ONE answer about ONE payment. When the message carries any identifying
    detail this is a lookup and replies in the chat about what it found; only a
    bare history request — or an explicit ask for a statement — attaches the PDF.

    A statement is a heavy thing to send: it is 250KB, it lands as a file card,
    and sending one in answer to "did my transfer arrive?" makes the customer do
    the work of finding their own answer inside it.
    """
    lookup = any(x is not None for x in (amount, days_ago, kind, recipient, status))

    if lookup:
        rows = _find_txns(user, amount=amount, days_ago=days_ago, kind=kind,
                          recipient=recipient, status=status)
        if not rows:
            said = _describe_query(amount=amount, days_ago=days_ago, kind=kind,
                                   recipient=recipient, status=status)
            return reply(
                msisdn,
                f"🔍 I couldn't find {said} on your account.\n\n"
                "It may have been sent from another app or account. Reply *9* to see your "
                "recent transactions, or tell me more about it and I'll take another look.")
        if len(rows) == 1:
            t = rows[0]
            head = f"Your {_money(t.amount)} {(t.service or 'transaction').strip()} was {_STATUS_LABEL[_status_of(t)]}"
            body = (f"{head}\n\n"
                    f"🗓️ {t.created:%d %b %Y, %I:%M %p}\n"
                    f"🔖 Ref {t.reference}")
            if _status_of(t) == "pending":
                body += ("\n\nIt's still with the provider. These usually settle within a few "
                         "minutes — you'll get a message here the moment it does.")
            elif _status_of(t) == "failed":
                body += "\n\nYou were not charged for it."
            else:
                body += ("\n\nIf the person says they haven't received it, reply "
                         "*report a problem* and I'll open a case with support.")
            return reply(msisdn, body)
        found = _describe_query(amount=amount, days_ago=days_ago, kind=kind,
                                recipient=recipient, status=status)
        return reply(msisdn, f"🔍 I found {len(rows)} transactions matching {found}:\n\n"
                             + "\n".join(_txn_line(t) for t in rows)
                             + "\n\nReply *report a problem* if one of these needs looking into.")

    from wallet.models import Transaction

    try:
        count = max(1, min(int(count), 20))
    except (TypeError, ValueError):
        count = 8
    rows = list(Transaction.objects.filter(user=user).order_by("-created")[:count])
    if not rows:
        return reply(msisdn, "🧾 No transactions yet. Reply *6* to add money and get started.")

    lines, pdf_rows = [], []
    for t in rows:
        sign = "＋" if t.direction == Transaction.IN else "－"
        label = (t.service or "").strip() or ("Credit" if t.direction == Transaction.IN else "Debit")
        lines.append(_txn_line(t))
        pdf_rows.append({"date": t.created.strftime("%d %b %Y, %I:%M %p"), "label": label,
                         "amount": _money(t.amount), "sign": sign, "status": _status_of(t),
                         "reference": t.reference})

    balance = _money(get_or_create_wallet(user).balance)
    generated = timezone.localtime().strftime("%d %b %Y, %I:%M %p")
    header = (f"🧾 Your last transaction was {_STATUS_LABEL[_status_of(rows[0])]}."
              if count == 1 else f"🧾 *Your last {len(rows)} transactions*")
    caption = header + "\n\n" + "\n".join(lines) + f"\n\nBalance: {balance}"

    # The file is for a STATEMENT request. A one-line "was my last one ok?" gets
    # the answer in the thread where it was asked.
    if not as_document and count <= 3:
        return reply(msisdn, caption)

    from .providers import send_document, upload_media, wa_live

    sent = False
    if wa_live():
        try:
            from .receipt import render_statement_pdf

            pdf = render_statement_pdf(pdf_rows, balance=balance, generated=generated)
            filename = f"Zitch-Statement-{timezone.now():%Y%m%d%H%M%S}.pdf"
            media_id = upload_media(pdf, "application/pdf", filename)
            if media_id:
                sent = send_document(msisdn, media_id, filename, caption=caption).get("success", False)
            if not sent:
                log.warning("wa_history_pdf_send_failed msisdn=%s", mask_pii(msisdn))
        except Exception:  # noqa: BLE001 — the text summary below must still land
            log.exception("wa_history_pdf_render_failed msisdn=%s", mask_pii(msisdn))
    if not sent:
        reply(msisdn, caption)


def _describe_query(*, amount=None, days_ago=None, kind=None, recipient=None, status=None) -> str:
    """The customer's own search terms, read back to them. A bare "I couldn't
    find it" leaves them unable to tell whether we misheard the amount, the day
    or the type — so we say which one we looked for."""
    bits = []
    if status:
        bits.append({"failed": "a failed", "pending": "a pending",
                     "successful": "a successful"}.get(status, "a"))
    if amount is not None:
        try:
            bits.append(f"{_money(Decimal(str(amount)))}")
        except (InvalidOperation, TypeError):
            pass
    bits.append({"transfer": "transfer", "airtime": "airtime purchase", "data": "data purchase",
                 "bill": "bill payment", "funding": "wallet funding"}.get(kind, "transaction"))
    if recipient:
        bits.append(f"to {str(recipient)[:40]}")
    if days_ago is not None:
        try:
            n = max(0, int(days_ago))
            bits.append("today" if n == 0 else "yesterday" if n == 1 else f"{n} days ago")
        except (TypeError, ValueError):
            pass
    return " ".join(b for b in bits if b)


def _start_problem_report(user, msisdn: str) -> None:
    """The keyword path into a support case: show what could be wrong and let the
    customer point at it. The AI path names the transaction from the message;
    this one has no message to read, so it asks."""
    from wallet.models import Transaction

    rows = list(Transaction.objects.filter(user=user).order_by("-created")[:5])
    if not rows:
        return reply(msisdn, "You don't have any transactions yet, so there's nothing to raise "
                             "a case about.\n\nIf you need help with something else:\n"
                             + (_more_info_block() or ""))
    return reply(msisdn, "🛟 *Report a problem*\n\nWhich transaction is it? Reply with the "
                         "amount and roughly when it happened — for example \"the ₦5,000 "
                         "transfer 2 days ago\".\n\n"
                 + "\n".join(_txn_line(t) for t in rows))


def _do_report_problem(user, msisdn: str, *, amount=None, days_ago=None, kind=None,
                       recipient=None, reference=None, reason=None, detail=None) -> None:
    """Open a real support case against a transaction the customer says went wrong.

    "Escalate this to customer support" used to reach nothing — the assistant had
    no tool for it, so the message fell through to a menu and the customer was
    left believing a human had been told. Nobody had been. This writes a Dispute,
    which is the same case record the app's own dispute flow and the ops console
    already work from, and it answers with the case number and the response
    window so the customer has something to hold us to.

    It never promises a refund. A dispute is an investigation, and the remedy is
    an audited path that a human decides on — see compliance.models.Dispute.
    """
    from django.db import IntegrityError

    from compliance.models import Dispute

    rows = _find_txns(user, amount=amount, days_ago=days_ago, kind=kind,
                      recipient=recipient, reference=reference, limit=4)
    if not rows:
        said = _describe_query(amount=amount, days_ago=days_ago, kind=kind, recipient=recipient)
        return reply(
            msisdn,
            f"🔍 I couldn't find {said} on your account, so I don't have a transaction to "
            "raise a case against.\n\nReply *9* to see your recent transactions and tell me "
            "which one it is — or contact our team directly:\n" + (_more_info_block() or ""))
    if len(rows) > 1:
        return reply(msisdn, "I found more than one transaction that could be the one you mean:\n\n"
                             + "\n".join(_txn_line(t) for t in rows)
                             + "\n\nWhich one should I raise with support? Reply with the amount "
                               "and the date, or the reference.")

    txn = rows[0]
    reason = reason if reason in dict(Dispute.REASONS) else Dispute.NOT_RECEIVED
    # The customer's own words, capped to the column and stripped of newlines so
    # one message cannot fill the case list with whitespace.
    note = " ".join(str(detail or "").split())[:500]
    # Looked up before creating rather than via get_or_create: the "one open case
    # per reference" rule is a CONDITIONAL unique constraint (open/investigating
    # only), and a status__in lookup cannot be expressed as get_or_create kwargs
    # without trying to write it as a field.
    case = Dispute.objects.filter(
        user=user, reference=txn.reference,
        status__in=(Dispute.OPEN, Dispute.INVESTIGATING)).first()
    if case is not None:
        return reply(
            msisdn,
            f"📌 There's already an open case for that {_money(txn.amount)} "
            f"{(txn.service or 'transaction').strip()}.\n\n"
            f"🔖 Case #{case.id} · raised {case.created:%d %b}\n"
            f"⏳ We'll come back to you by {case.due:%d %b %Y}.\n\n"
            "You don't need to do anything else — we'll message you here as soon as there's "
            "an update.")
    try:
        case = Dispute.objects.create(
            user=user, reference=txn.reference, reason=reason,
            detail=note or "Raised from WhatsApp")
    except IntegrityError:
        # Lost a race with the same customer double-sending. The constraint did
        # its job; read back the winner rather than reporting a failure for a
        # case that now exists.
        case = Dispute.objects.filter(
            user=user, reference=txn.reference,
            status__in=(Dispute.OPEN, Dispute.INVESTIGATING)).first()
        if case is None:
            raise
    log.info("wa_dispute_opened case=%s ref=%s reason=%s", case.id, txn.reference, reason)
    return reply(
        msisdn,
        f"✅ I've opened a support case for your {_money(txn.amount)} "
        f"{(txn.service or 'transaction').strip()}.\n\n"
        f"🔖 Case #{case.id}\n"
        f"🧾 Transaction {txn.reference} · {txn.created:%d %b %Y, %I:%M %p}\n"
        f"⏳ Our team will come back to you by {case.due:%d %b %Y}.\n\n"
        "You'll get an update here — no need to send it again. If it's urgent you can also "
        "reach our team directly:\n" + (_more_info_block() or ""))


def _do_support(msisdn: str) -> None:
    """Website / app / customer-care links, on demand as well as under the menu."""
    block = _more_info_block()
    if not block:
        return reply(msisdn, "💬 *Need help?* Reply \"menu\" for options — or type your question and we'll help right here.")
    return reply(msisdn, "💬 *Zitch help & information*\n\n" + block +
                 "\n\nOr just type your question here — reply \"menu\" for options.")


# --------------------------------------------------------------------------- #
# kyc — prove both contact channels and both identity numbers without leaving
# the chat. Each step drives the same server-side checks the app uses, and the
# tier is DERIVED at the end (recompute_tier), never granted by this flow.
# --------------------------------------------------------------------------- #
_KYC_STEPS = ("phone", "email", "bvn", "nin")


def _kyc_test_code(user) -> str:
    """The fixed TEST_OTP code, but ONLY for the one nominated test number.

    Scoped to `user.phone == TEST_OTP["PHONE"]`, exactly as the app's OTP model
    scopes it. An earlier version keyed only off "is TEST_OTP configured", which
    handed the same fixed code to EVERY customer's phone and email verification
    on any deploy where the pair was set — a far wider bypass than the switch is
    meant to be, and one that silently followed the pair into production.

    Reuses the app's switch rather than inventing a second one, so there is a
    single answer to "is a fixed code accepted anywhere", and wema_preflight
    already hard-fails while it is set."""
    test = getattr(settings, "TEST_OTP", {}) or {}
    phone, code = (test.get("PHONE") or "").strip(), (test.get("CODE") or "").strip()
    if not (phone and code) or (user.phone or "").strip() != phone:
        return ""
    fingerprint = hashlib.sha256((user.phone or "").encode()).hexdigest()[:12]
    log.warning("wa_test_otp_used phone_sha256=%s — TEST_OTP is set; "
                "REMOVE TEST_OTP_PHONE/TEST_OTP_CODE before go-live", fingerprint)
    return code


def _kyc_outstanding(user) -> list:
    """Which steps this customer still owes, in order."""
    done = {
        "phone": user.phone_verified,
        "email": user.email_verified,
        "bvn": user.bvn_verified,
        "nin": user.nin_verified,
    }
    return [step for step in _KYC_STEPS if not done[step]]


def _kyc_status_lines(user) -> str:
    mark = lambda ok: "✅" if ok else "⬜"  # noqa: E731
    return "\n".join([
        f"{mark(user.phone_verified)} Phone number",
        f"{mark(user.email_verified)} Email address",
        f"{mark(user.bvn_verified)} BVN",
        f"{mark(user.nin_verified)} NIN",
    ])


def _start_kyc(user, msisdn: str) -> None:
    outstanding = _kyc_outstanding(user)
    if not outstanding:
        return reply(msisdn, "✅ *You're fully verified.*\n\n" + _kyc_status_lines(user)
                     + f"\n\nTier {user.tier} · up to ₦{user.transaction_limit:,.0f} per transaction.")
    _clear_actions(msisdn)
    pa = PendingAction.objects.create(
        user=user, msisdn=msisdn, action_type="kyc", state="idle",
        payload={}, expires_at=_flow_deadline("idle"),
    )
    reply(msisdn, "🪪 *Verify your identity*\n\n" + _kyc_status_lines(user)
          + "\n\nAll four are needed to raise your limits. Let's do the rest now — "
            'reply "cancel" to stop anytime.')
    return _kyc_next(pa, user, msisdn)


def _kyc_next(pa: PendingAction, user, msisdn: str) -> None:
    """Move to the next outstanding step, or finish.

    Steps already attempted in this session are skipped. An identity queued for
    review is still "outstanding" (it is not verified), so without this the flow
    would ask for the same number forever."""
    attempted = set(pa.payload.get("attempted") or [])
    outstanding = [s for s in _kyc_outstanding(user) if s not in attempted]
    if not outstanding:
        return _kyc_finish(pa, user, msisdn)
    step = outstanding[0]
    pa.payload["attempted"] = sorted(attempted | {step})
    if step == "phone":
        return _kyc_send_phone_code(pa, user, msisdn)
    if step == "email":
        return _kyc_send_email_code(pa, user, msisdn)
    if _send_identity_flow(pa, step):
        return None
    if flows_live():
        # The secure screen EXISTS on this deploy and the dispatch failed —
        # tonight's production run proved what the chat fallback costs here: a
        # BVN and a NIN sitting in the thread in clear, exactly what the Flow
        # was built to prevent. When the deploy has Flows, identity is
        # Flow-or-nothing; the send failure is in the logs
        # (wa_identity_flow_send_failed and the provider rejection beside it).
        _clear_actions(msisdn)
        return reply(msisdn, "⚠️ The secure entry screen didn't go through, so I won't ask for "
                             "your ID number here in the chat. Reply *8* to try again in a "
                             "moment, or verify in the Zitch app.")
    which = step.upper()
    _touch(pa, state=step, payload=pa.payload)
    # Fallback only for deploys with NO Flows configured (dev/preview). We store
    # a hash, but the customer's own copy of what they typed stays in their
    # history, so the one thing that can still remove it is named explicitly.
    return reply(msisdn, f"Enter your 11-digit *{which}*. We store it only as a secure hash.\n\n"
                         "_Delete your message afterwards (press and hold → Delete → "
                         "Delete for everyone) — WhatsApp only lets the sender do this._")


def _kyc_finish(pa: PendingAction, user, msisdn: str) -> None:
    _clear_actions(msisdn)
    user.recompute_tier()
    user.save(update_fields=["tier"])
    pending = pa.payload.get("pending_review")
    tail = ""
    if pending:
        tail = (f"\n\n⏳ Your {pending.upper()} is with our team for review — we'll message you "
                "when it's approved.")
    # A completed identity ladder mints the personal account number, because
    # that is what the customer verified FOR. Instant in simulation (mock
    # NUBAN); in live mode the bank requires its own SMS round, so the ladder
    # points at it rather than silently launching another flow.
    if user.bvn_verified and user.nin_verified:
        wallet = get_or_create_wallet(user)
        if not wallet.account_number:
            if _chat_simulation_allowed():
                from accounts.views import _simulate_provision_account

                acct = _simulate_provision_account(user)
                if acct:
                    tail += (f"\n\n🏦 Your personal Zitch account number is ready: "
                             f"*{acct}*\nFund your wallet by bank transfer to it any time.")
            elif wallet_views._wema_funding_enabled():
                tail += ("\n\n🏦 One last step: reply *6* to open your personal Zitch "
                         "account number — the bank sends its own SMS code to finish.")
    reply(msisdn, "🎉 *Thanks!* Here's where you stand:\n\n" + _kyc_status_lines(user)
          + f"\n\nTier {user.tier} · up to ₦{user.transaction_limit:,.0f} per transaction." + tail)


def _kyc_send_phone_code(pa: PendingAction, user, msisdn: str) -> None:
    """SMS round-trip. Possession of this WhatsApp chat is NOT possession of the
    SIM — a messenger session outlives a SIM swap — so the code goes to the
    number itself and must come back here.

    The rail is checked BEFORE sending, not after: send_sms returns a
    silent-success dict when it has no key, so trusting its `success` would
    announce a code that never left the building and leave the customer staring
    at a phone that will never buzz."""
    if not sms_live() and not _kyc_test_code(user):
        _clear_actions(msisdn)
        log.warning("wa_kyc_sms_not_configured — TERMII_API_KEY is unset")
        return reply(msisdn, "⚠️ We can't send SMS codes at the moment, so phone verification "
                             "is unavailable. Please contact support — this is on our side, not yours.")
    code = _kyc_test_code(user) or f"{secrets.randbelow(10**6):06d}"
    sent = send_sms(user.phone or "", f"Zitch: {code} is your verification code. It expires in 10 minutes.")
    if not sent.get("success"):
        _clear_actions(msisdn)
        return reply(msisdn, "⚠️ We couldn't send the SMS just now. Please try again shortly.")
    pa.payload["code_hash"] = make_password(code)
    pa.payload["code_exp"] = (timezone.now() + timedelta(minutes=10)).isoformat()
    pa.payload["code_attempts"] = 0
    _touch(pa, state="phone", payload=pa.payload)
    masked = f"•••••{(user.phone or '')[-4:]}"
    reply(msisdn, f"📲 We sent a 6-digit code by SMS to {masked}. Enter it here. (Reply *resend* for a new one.)")


def _kyc_email_rail_error(user) -> str:
    """Why email verification can't run right now, or "" if it can."""
    if email_live() or _kyc_test_code(user):
        return ""
    log.warning("wa_kyc_email_not_configured — RESEND_API_KEY is unset")
    return ("⚠️ We can't send emails at the moment, so email verification is unavailable. "
            "Please contact support — this is on our side, not yours.")


def _kyc_mail_code(pa: PendingAction, user) -> bool:
    """Mint, send and arm a fresh email code. False if the provider refused it.

    Checking the key is not enough: a key can be present and still be refused
    for this sender (typically FROM_EMAIL on an unverified domain), which used
    to print "We sent a 6-digit code" over mail that never left the building.
    """
    code = _kyc_test_code(user) or f"{secrets.randbelow(10**6):06d}"
    # Same branded template the app's OTP emails use — one design, so a customer
    # never has to judge whether a bare-text code email is really from us.
    from accounts.views import _branded_email

    sent = send_email(user.email, "Confirm your email for Zitch",
                      f"Your Zitch email confirmation code is {code}",
                      html=_branded_email(
                          "Confirm your email",
                          "Enter this code on the secure WhatsApp screen to confirm "
                          "your email address.",
                          code=code,
                          note="This code expires in 10 minutes. If you didn't request "
                               "it, you can ignore this email — nothing changes without "
                               "the code."))
    if not sent.get("success"):
        return False
    pa.payload["code_hash"] = make_password(code)
    pa.payload["code_exp"] = (timezone.now() + timedelta(minutes=10)).isoformat()
    pa.payload["code_attempts"] = 0
    return True


def _kyc_send_email_code(pa: PendingAction, user, msisdn: str) -> None:
    if not user.email:
        if _send_email_flow(pa, "address"):
            return reply(msisdn, "📧 Tap the secure form above to enter your *email address* — "
                                 "it stays private and never appears in this chat.")
        _touch(pa, state="email_address", payload=pa.payload)
        return reply(msisdn, "What's your *email address*?")
    rail_error = _kyc_email_rail_error(user)
    if rail_error:
        _clear_actions(msisdn)
        return reply(msisdn, rail_error)
    if not _kyc_mail_code(pa, user):
        _clear_actions(msisdn)
        return reply(msisdn, "⚠️ We couldn't send the email just now. Please try again shortly.")
    # The code is a bearer credential for ten minutes: it belongs in the Flow,
    # not in a thread the customer keeps forever.
    if _send_email_flow(pa, "code"):
        return reply(msisdn, f"📧 We sent a 6-digit code to *{user.email}*. Enter it on the secure "
                             "form above. (Reply *resend* for a new one.)")
    if flows_live():
        # Same policy as the identity numbers: on a deploy with Flows, a failed
        # dispatch must not demote the code to a chat message.
        _clear_actions(msisdn)
        return reply(msisdn, "⚠️ The secure entry screen didn't go through, so I won't ask for "
                             "the code here in the chat. Reply *8* to try again in a moment.")
    _touch(pa, state="email", payload=pa.payload)
    reply(msisdn, f"📧 We sent a 6-digit code to *{user.email}*. Enter it here. "
                  "(Reply *resend* for a new one, or *change* to use a different address.)")


# --------------------------------------------------------------------------- #
# email, submitted through the encrypted Flow. Each returns (status, message)
# where status is "ok" (accepted), "retry" (show the message, ask again) or
# "stop" (the attempt is over; the message is the terminal screen).
# --------------------------------------------------------------------------- #
def kyc_flow_email_address(pa: PendingAction, email: str) -> tuple[str, str]:
    user = pa.user
    email = (email or "").strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return "retry", "That doesn't look like an email address — enter it like name@example.com."
    if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
        return "retry", "That email is already on another Zitch account — enter a different one."
    rail_error = _kyc_email_rail_error(user)
    if rail_error:
        _clear_actions(pa.msisdn)
        reply(pa.msisdn, rail_error)
        return "stop", "Email verification is unavailable right now — see the chat."
    user.email = email
    user.save(update_fields=["email"])
    if not _kyc_mail_code(pa, user):
        _clear_actions(pa.msisdn)
        reply(pa.msisdn, "⚠️ We couldn't send the email just now. Please try again shortly.")
        return "stop", "We couldn't send that email — see the chat."
    # Same open Flow, second half: arm the code step so the next submit lands
    # here, and record that the code page arrives IN-SESSION (IDENTITY_CHAIN) —
    # the render must not fall back to the CODE_SCREEN root, which the routing
    # model does not permit as a navigation from EMAIL_SCREEN.
    pa.payload["id_step"] = "code"
    pa.payload["flow_screen"] = IDENTITY_CHAIN
    _touch(pa, state=FLOW_ID_STATE, payload=pa.payload)
    reply(pa.msisdn, f"📧 We sent a 6-digit code to *{email}*. Enter it on the secure form.")
    return "ok", ""


def kyc_flow_email_code(pa: PendingAction, code: str) -> tuple[str, str]:
    user, msisdn = pa.user, pa.msisdn
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if not re.fullmatch(r"\d{6}", code):
        return "retry", "That should be exactly 6 digits."
    verdict = _kyc_code_check(pa, code)
    if verdict == "expired":
        return "stop", "That code has expired. Reply 8 in the chat to start again."
    if verdict == "locked":
        _clear_actions(msisdn)
        reply(msisdn, "Too many incorrect codes. Reply *8* to start verification again.")
        return "stop", "Too many incorrect codes — see the chat."
    if verdict != "ok":
        left = 3 - int(pa.payload.get("code_attempts", 0))
        _touch(pa, payload=pa.payload)
        return "retry", f"That code isn't right. {left} attempt(s) left."
    user.email_verified = True
    user.save(update_fields=["email_verified"])
    reply(msisdn, "✅ Email address verified.")
    _kyc_next(pa, user, msisdn)
    return "ok", ""


def _kyc_code_check(pa: PendingAction, val: str) -> str:
    """The verdict on a submitted code: "ok", "expired", "wrong" or "locked".

    Says nothing and clears nothing — the chat and the Flow each phrase it in
    their own voice, off ONE implementation of single-use, expiry and the
    attempt cap, so the two entry points cannot drift on what counts as valid.
    """
    exp = pa.payload.get("code_exp") or ""
    try:
        expired = bool(exp) and timezone.now() >= timezone.datetime.fromisoformat(exp)
    except (TypeError, ValueError):
        expired = True   # unreadable expiry is treated as expired, never as valid
    if expired:
        return "expired"
    attempts = int(pa.payload.get("code_attempts", 0)) + 1
    if check_password(val, pa.payload.get("code_hash", "")):
        # Burn it so the same code cannot be replayed.
        pa.payload["code_hash"] = ""
        return "ok"
    pa.payload["code_attempts"] = attempts
    return "locked" if attempts >= 3 else "wrong"


def _kyc_code_ok(pa: PendingAction, msisdn: str, val: str) -> bool | None:
    """True on a correct code, False to keep waiting, None if the flow was ended.
    Single-use and attempt-capped, like every other code in the channel."""
    verdict = _kyc_code_check(pa, val)
    if verdict == "ok":
        return True
    if verdict == "expired":
        reply(msisdn, "That code has expired. Reply *resend* for a new one.")
        return False
    if verdict == "locked":
        _clear_actions(msisdn)
        reply(msisdn, "Too many incorrect codes. Reply *8* to start verification again.")
        return None
    left = 3 - int(pa.payload.get("code_attempts", 0))
    _touch(pa, payload=pa.payload)
    reply(msisdn, f"That code isn't right. {left} attempt(s) left, or reply *resend*.")
    return False


def _advance_kyc(pa: PendingAction, user, msisdn: str, text: str) -> None:
    val = text.strip()
    low = val.lower()
    state = pa.state

    if state == "phone":
        if low == "resend":
            return _kyc_send_phone_code(pa, user, msisdn)
        if not re.fullmatch(r"\d{6}", val):
            return reply(msisdn, "Enter the 6-digit code from the SMS, or reply *resend*.")
        ok = _kyc_code_ok(pa, msisdn, val)
        if ok is not True:
            return
        user.phone_verified = True
        user.save(update_fields=["phone_verified"])
        reply(msisdn, "✅ Phone number verified.")
        return _kyc_next(pa, user, msisdn)

    if state == "email_address":
        email = low
        if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return reply(msisdn, "That doesn't look like an email address. Enter it like *name@example.com*.")
        if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
            return reply(msisdn, "That email is already on another Zitch account. Enter a different one.")
        user.email = email
        user.save(update_fields=["email"])
        return _kyc_send_email_code(pa, user, msisdn)

    if state == "email":
        if low == "resend":
            return _kyc_send_email_code(pa, user, msisdn)
        if low == "change":
            _touch(pa, state="email_address", payload=pa.payload)
            return reply(msisdn, "What's the correct *email address*?")
        if not re.fullmatch(r"\d{6}", val):
            return reply(msisdn, "Enter the 6-digit code from the email, or reply *resend*.")
        ok = _kyc_code_ok(pa, msisdn, val)
        if ok is not True:
            return
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        reply(msisdn, "✅ Email address verified.")
        return _kyc_next(pa, user, msisdn)

    if state in ("bvn", "nin"):
        digits = "".join(ch for ch in val if ch.isdigit())
        if len(digits) != 11:
            return reply(msisdn, f"That should be exactly 11 digits. Enter your {state.upper()} again, "
                                 'or reply "cancel".')
        return _kyc_submit_identity(pa, user, msisdn, state, digits)

    _clear_actions(msisdn)
    return send_menu(msisdn)


def _kyc_submit_identity(pa: PendingAction, user, msisdn: str, kind: str, digits: str) -> None:
    """Verify a BVN/NIN, or bank it for review when the rail cannot check it.

    Our bank has no standalone identity lookup: the real, name-matched check
    happens during account creation, and it verifies exactly ONE identity. The
    second one therefore cannot be auto-verified — so rather than dead-ending
    the customer, it is stored (hashed, never raw) and queued for the operator
    KYC review that already exists in the portal.
    """
    from accounts.models import hash_identifier
    from accounts.views import _identity_owned_by_another_user

    simulation = _chat_simulation_allowed()
    # Real identities remain globally unique. Test digits deliberately do not:
    # every tester may use the same documented fake values, and the stored marker
    # is namespaced by user rather than derived from what they entered.
    if not simulation and _identity_owned_by_another_user(user, kind, digits):
        _clear_actions(msisdn)
        return reply(msisdn, "⚠️ That number is already linked to another Zitch account. "
                             "Please contact support if this is unexpected.")
    checker = verify_bvn if kind == "bvn" else verify_nin
    result = checker(digits, name=user.get_full_name() or "")
    setter = user.set_bvn if kind == "bvn" else user.set_nin
    fields = ["bvn_hash", "bvn_last4"] if kind == "bvn" else ["nin_hash", "nin_last4"]

    if simulation and result.get("success") and result.get("mock"):
        hash_field, last4_field = fields
        setattr(user, hash_field, hash_identifier(f"simulation:{kind}:{user.pk}"))
        # Never retain even the last four digits entered in a simulation Flow.
        # A per-user marker preserves the support/audit shape without turning a
        # tester's accidental real BVN/NIN into stored identity data.
        setattr(user, last4_field, f"{user.pk:04d}"[-4:])
        user.save(update_fields=[hash_field, last4_field])
        log.warning("wa_simulated_identity_verified user=%s kind=%s", user.pk, kind)
        # The BVN keeps its OTP round even in simulation — the walkthrough must
        # rehearse the same pages production uses. The code goes to the
        # customer's own phone (the stand-in for the line on the BVN record,
        # which a simulation does not have). NIN has no OTP step by design.
        if kind == "bvn":
            otp_error = _kyc_send_identity_otp(pa, user, kind, user.phone or "")
            if otp_error is None:
                return "otp"
        # No SMS channel (or NIN): verify directly rather than blocking a demo
        # on an undeliverable code.
        setattr(user, f"{kind}_verified", True)
        user.recompute_tier()
        user.save(update_fields=[f"{kind}_verified", "tier"])
        reply(msisdn, f"✅ Your {kind.upper()} has been verified.")
        return _kyc_next(pa, user, msisdn)

    if result.get("invalid"):
        # The authoritative source answered, and the answer is no: wrong number,
        # or a number belonging to someone else. That is the CUSTOMER'S to
        # correct, not an operator's to approve — queueing it would put a human
        # in front of a decision already made, and "this BVN is not yours" is
        # exactly the request that must never be waved through. Nothing is
        # stored: an unowned identity has no business on the account.
        attempts = int(pa.payload.get("id_bad_attempts") or 0) + 1
        pa.payload["id_bad_attempts"] = attempts
        _touch(pa, payload=pa.payload)
        if attempts >= _MAX_ID_ATTEMPTS:
            # Bounded so the screen cannot be used to probe numbers.
            _clear_actions(msisdn)
            reply(msisdn, f"⚠️ That's {_MAX_ID_ATTEMPTS} incorrect {kind.upper()} attempts. "
                          "Please check the number and reply *8* to start again, or contact "
                          "support if you believe this is wrong.")
            return "stop"
        return "invalid"

    if not result.get("success"):
        # We could not ASK — provider unreachable or unconfigured. That is ours,
        # not the customer's, so it queues rather than accusing them of a wrong
        # number. Record why: "submitted for review" is one sentence for several
        # causes, and they need different actions.
        setter(digits)
        user.save(update_fields=fields)
        _record_identity_review(kind, result.get("message", ""))
        pa.payload["pending_review"] = kind
        _touch(pa, payload=pa.payload)
        reply(msisdn, f"📋 We couldn't reach the verification service just now — your "
                      f"{kind.upper()} has been submitted for review.")
        return _kyc_next(pa, user, msisdn)

    setter(digits)
    user.save(update_fields=fields)
    # A name match proves someone knows a name. A code delivered to the line
    # registered against the identity proves the person asking controls it — so
    # the lookup passing is the START of verification here, not the end.
    otp_error = _kyc_send_identity_otp(pa, user, kind, result.get("phone", ""))
    if otp_error is None:
        return "otp"
    if otp_error:                     # cannot run the challenge -> review, with the reason
        _record_identity_review(kind, otp_error)
        pa.payload["pending_review"] = kind
        _touch(pa, payload=pa.payload)
        reply(msisdn, f"📋 Your {kind.upper()} has been submitted for review.")
        return _kyc_next(pa, user, msisdn)
    # Dev/test deploys have no SMS and no Flow to collect a code in; the suite
    # and the simulation walkthrough still need the ladder to complete.
    setattr(user, f"{kind}_verified", True)
    user.save(update_fields=[f"{kind}_verified"])
    reply(msisdn, f"✅ {kind.upper()} verified.")
    return _kyc_next(pa, user, msisdn)


#: Wrong-number attempts before the identity step gives up. Bounded because an
#: unlimited retry screen is a lookup oracle: it answers "is this BVN real, and
#: whose is it" for anyone willing to sit and type.
#:
#: TWO, not three, and that is a Flow constraint rather than a policy view: each
#: attempt needs its own screen to arrive with an empty box (WhatsApp keeps form
#: state across a same-screen re-render), routing is a DAG so screens cannot
#: alternate, and a third would mean a third screen carrying a third copy of the
#: layout. Two covers the typo; anything beyond it is a wrong number, and
#: replying 8 mints a fresh attempt anyway.
_MAX_ID_ATTEMPTS = 2

#: Where the last identity-review reason is parked for /healthz. Same reasoning
#: as wa_last_flow_error: the reason exists, but only in a log stream nobody
#: debugging from a phone can reach.
IDENTITY_REVIEW_KEY = "wa_last_identity_review"


def _record_identity_review(kind: str, reason: str) -> None:
    """Why the last identity went to review. Carries no identity number and no
    resolved name — the reason concerns OUR lookup, not the person."""
    from .models import SystemSetting

    try:
        SystemSetting.set(IDENTITY_REVIEW_KEY,
                          "|".join((timezone.now().isoformat(timespec="seconds"),
                                    kind, str(reason)[:150]))[:255])
    except Exception:  # noqa: BLE001 — diagnostics never break the ladder
        log.debug("could not record identity review reason", exc_info=True)


def _kyc_send_identity_otp(pa: PendingAction, user, kind: str, phone: str):
    """Send the identity challenge code to the line on the BVN/NIN record.

    Returns None when the code is away (the caller chains to the code screen),
    a string when the challenge cannot be run (the caller queues for review with
    that reason), or "" when this deploy has no channel for it at all.
    """
    if getattr(settings, "TESTING", False) or settings.DEBUG:
        return ""
    if not flows_live():
        # The code is a bearer credential. Collecting it in the thread would undo
        # the reason the number was collected in a Flow in the first place.
        return "no secure screen to collect the code on"
    if not phone:
        return "the identity record carried no phone number"
    if not sms_live():
        return "SMS is not configured"
    code = f"{secrets.randbelow(10**6):06d}"
    sent = send_sms(phone, f"Zitch: {code} is your {kind.upper()} verification code. "
                           "It expires in 10 minutes. Never share it.")
    if not sent.get("success"):
        return "the verification SMS was rejected"
    pa.payload.update({
        "id_otp_hash": make_password(code),
        "id_otp_exp": (timezone.now() + timedelta(minutes=10)).isoformat(),
        "id_otp_attempts": 0,
        # Masked so the chat and the screen can say where it went without
        # printing a number that belongs to the identity, not the account.
        "id_otp_to": f"•••••{phone[-4:]}",
        "id_otp_kind": kind,
    })
    _touch(pa, state=FLOW_ID_STATE, payload=pa.payload)
    return None


def kyc_flow_identity_otp(pa: PendingAction, code: str):
    """Check the identity challenge code. ("retry", msg) | ("stop", msg) | ("ok", msg)."""
    user = pa.user
    kind = pa.payload.get("id_otp_kind", "bvn")
    expires = pa.payload.get("id_otp_exp")
    if not pa.payload.get("id_otp_hash") or not expires or timezone.now() > timezone.datetime.fromisoformat(expires):
        _clear_actions(pa.msisdn)
        return "stop", "That code expired. Reply 8 in the chat to try again."
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    if len(digits) != 6:
        # A five-digit entry is a typo, not a guess — it must not spend one of
        # the three attempts the real challenge gets.
        return "retry", "The code is exactly 6 digits — check the SMS and try again."
    attempts = int(pa.payload.get("id_otp_attempts") or 0) + 1
    if not check_password(digits, pa.payload["id_otp_hash"]):
        if attempts >= 3:
            # Three wrong codes is not a typo. Queue it rather than letting the
            # challenge be ground down.
            _record_identity_review(kind, "three wrong verification codes")
            _clear_actions(pa.msisdn)
            return "stop", (f"That's 3 incorrect codes — your {kind.upper()} has been sent "
                            "for manual review instead.")
        pa.payload["id_otp_attempts"] = attempts
        _touch(pa, payload=pa.payload)
        return "retry", f"That code isn't right. {3 - attempts} attempt(s) left."
    setattr(user, f"{kind}_verified", True)
    user.recompute_tier()
    user.save(update_fields=[f"{kind}_verified", "tier"])
    for key in ("id_otp_hash", "id_otp_exp", "id_otp_attempts", "id_otp_to", "id_otp_kind"):
        pa.payload.pop(key, None)
    _touch(pa, payload=pa.payload)
    return "ok", f"✅ Your {kind.upper()} has been verified."


def _do_account_details(user, msisdn: str) -> None:
    """Menu 7: who Zitch thinks you are, plus the funding account (or the way
    to mint one)."""
    wallet = get_or_create_wallet(user)
    lines = [
        "🧾 *My account details*\n",
        f"👤 {user.get_full_name() or user.first_name or '—'}",
        f"📱 {user.phone}",
    ]
    if user.email:
        lines.append(f"📧 {user.email}" + ("" if user.email_verified else " (unconfirmed)"))
    lines.append(f"⭐ Tier {user.tier} · up to ₦{user.transaction_limit:,.0f}/transaction")
    reply(msisdn, "\n".join(lines))
    if wallet.account_number:
        return _send_account_details(msisdn, wallet, intro="🏦 *Your funding account*")
    return reply(msisdn, "You don't have a funding account number yet — reply *6* (Add money) to set one up in a minute.")


# --------------------------------------------------------------------------- #
# add_account — mint the dedicated Wema NUBAN without leaving the chat.
# Same two-step contract as the app (identity → bank OTP), driving the same
# shared code: _start_wema_attempt / complete_wema_provisioning in wallet.views.
# The BVN/NIN input state is masked out of the message log by is_awaiting_bvn.
# --------------------------------------------------------------------------- #
def _start_add_account(user, msisdn: str, after_signup: bool = False) -> None:
    if not wallet_views._wema_funding_enabled():
        return reply(msisdn, "🏦 Account setup isn't available right now — please try again later.")
    _clear_actions(msisdn)
    PendingAction.objects.create(
        user=user, msisdn=msisdn, action_type="add_account", state="id_type",
        payload={}, expires_at=_flow_deadline("id_type"),
    )
    intro = ("🏦 Let's get you a *personal Zitch account number* so you can add money "
             "by bank transfer.\n\n" if not after_signup else
             "One more thing — let's mint your *personal Zitch account number* so you "
             "can add money by bank transfer.\n\n")
    reply(msisdn, intro +
          "We need one ID to open it:\n"
          "1️⃣  BVN\n2️⃣  NIN\n\nReply *1* or *2* (or \"cancel\" to do this later).")


def _account_submit_identity(pa: PendingAction, user, msisdn: str, digits: str,
                             in_flow: bool = False) -> str | None:
    """Open the NUBAN with the ID just supplied — from the Flow or, if Flows are
    not configured, from the chat. One implementation so the two entry points
    cannot drift on recovery or on what a failure does to the pending action.

    This is also where the BVN genuinely gets verified: the bank name-matches it
    while creating the account, which is the only real identity check we have.

    Returns one of three SENTINELS, because the Flow caller has to tell the
    outcomes apart to pick a closing screen and they are otherwise
    indistinguishable (every branch used to return whatever reply() gave back):
      "otp"  — accepted, code page next.
      "fail" — hard failure; the chat already carries the "⚠️ ..." reason and
               the pending action is cleared. The Flow must NOT close green.
      other  — the account was adopted/created successfully.
    """
    using_bvn = pa.payload.get("id_type") == "bvn"
    res, identity_error = wallet_views._start_wema_attempt(
        user, digits if using_bvn else "", "" if using_bvn else digits)
    if identity_error:
        _clear_actions(msisdn)
        reply(msisdn, f"⚠️ {identity_error}")
        return "fail"
    if not res.get("success"):
        # The bank may already hold an account for this customer — adopt it
        # instead of dead-ending (same recovery the app performs).
        recovered = wallet_views._adopt_existing_wema_account(
            user, using_bvn=using_bvn, reason=res.get("message", ""))
        if recovered is not None:
            _clear_actions(msisdn)
            _send_account_details(msisdn, get_or_create_wallet(user),
                                  intro="✅ *Found it!* Your Zitch account was already set up")
            return _kyc_continue_after_account(user, msisdn)
        _clear_actions(msisdn)
        reply(msisdn, f"⚠️ {res.get('message', 'Account setup failed — please try again later.')}")
        return "fail"
    pa.payload["tracking_id"] = str(res.get("tracking_id") or "")
    pa.payload["using_bvn"] = using_bvn
    _touch(pa, state="otp", payload=pa.payload)
    # The code completes account creation and is what name-matches the ID, so it
    # belongs on the secure screen too. Collecting the BVN privately and then
    # asking for the code that unlocks it in clear would be half a fix.
    if in_flow:
        # The BVN arrived through an open flow session: the code page is the
        # NEXT PAGE of that session, not a second flow message. Arm the state;
        # the caller renders the screen as the data_exchange response.
        pa.payload["id_kind"] = ACCOUNT_OTP
        pa.payload["flow_screen"] = IDENTITY_CHAIN
        _touch(pa, state=FLOW_ID_STATE, payload=pa.payload)
        reply(msisdn, "📲 We sent a code to your phone by SMS — enter it on the next page "
                      "of the secure form. (Reply *resend* if it doesn't arrive.)")
        return "otp"
    if _send_account_otp_flow(pa):
        return reply(msisdn, "📲 We sent a code to your phone by SMS. Enter it on the secure form "
                             "above to finish. (Reply *resend* if it doesn't arrive.)")
    reply(msisdn, "📲 We just sent a code to your phone by SMS. Enter it here to finish. "
                  "(Reply *resend* if it doesn't arrive.)")


def _send_account_otp_flow(pa: PendingAction) -> bool:
    """Put the bank's SMS code on the masked screen. Like every other identity
    Flow here this does NOT fail closed — without Flows configured the code is
    entered in the chat, exactly as before."""
    if not flows_live():
        return False
    pa.payload["id_kind"] = ACCOUNT_OTP
    pa.payload["flow_screen"] = CODE_SCREEN   # 6-digit root; IDENTITY_SCREEN is 11/11 now
    _touch(pa, state=FLOW_ID_STATE, payload=pa.payload)
    res = send_flow(
        pa.msisdn, sign_identity_token(pa),
        header="Finish your account", body="Enter the code privately — it never appears in this chat.",
        screen=CODE_SCREEN,
        screen_data={"summary": "Enter the code we sent to your phone",
                     "label": "SMS code", "error": ""},
        cta="Enter securely",
    )
    if res.get("success"):
        return True
    _touch(pa, state="otp", payload=pa.payload)
    log.warning("wa_account_otp_flow_send_failed pa=%s detail=%r", pa.id,
                res.get("error_detail", ""))
    return False


def account_flow_otp(pa: PendingAction, code: str) -> tuple[str, str]:
    """The SMS code submitted through the Flow. ("retry", msg) to stay on the
    screen, ("done", msg) to close it — the chat carries the detail either way.

    Delegates to the same completion the chat path uses, so the two cannot drift
    on what a 400 means or on when the pending action is cleared.
    """
    user, msisdn = pa.user, pa.msisdn
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if not code:
        return "retry", "Enter the code from the SMS."
    payload, status = wallet_views.complete_wema_provisioning(
        user, code, pa.payload.get("tracking_id", ""))
    if payload.get("success"):
        _clear_actions(msisdn)
        _send_account_details(msisdn, get_or_create_wallet(user),
                              intro="🎉 *Your Zitch account number is ready!*")
        _kyc_continue_after_account(user, msisdn)
        return "done", "Account created ✅ — see the chat for your details."
    if status == 400:   # expired / mismatched attempt: retrying the same code cannot help
        _clear_actions(msisdn)
        reply(msisdn, "⚠️ " + (payload.get("message") or "That didn't work.") + " Reply *6* to start again.")
        return "done", "That attempt expired — see the chat."
    return "retry", (payload.get("message") or "That code didn't work.")


def _kyc_continue_after_account(user, msisdn: str) -> None:
    """Roll straight from account setup into whatever identity checks remain.

    Verification is meant to happen once, at signup, rather than being deferred
    to a "reply 8" the customer never sends — and by this point the expensive
    parts are already done: opening the NUBAN name-matched their BVN, and its
    SMS code proved the SIM. What is normally left is the email and the second
    ID. Nothing is re-asked: _kyc_outstanding skips anything already verified,
    so a customer who is fully verified sees this do nothing at all.
    """
    user.refresh_from_db()
    outstanding = _kyc_outstanding(user)
    if not outstanding:
        return None
    # Only roll in when every remaining step can actually run. A dead rail ends
    # the ladder with "we can't send SMS codes" — fine as an answer to someone
    # who asked to verify, but not as the last thing a customer reads after
    # their account number was minted successfully. They can still reply 8.
    if not all(_kyc_rail_ready(user, step) for step in outstanding):
        log.info("wa_kyc_rollin_skipped steps=%s — a rail is unavailable", ",".join(outstanding))
        return None
    return _start_kyc(user, msisdn)


def _kyc_rail_ready(user, step: str) -> bool:
    """Whether the channel this step needs is actually configured. The two ID
    steps always are: they fall back to the operator review queue."""
    if step == "phone":
        return bool(sms_live() or _kyc_test_code(user))
    if step == "email":
        return bool(email_live() or _kyc_test_code(user))
    return True


def _advance_add_account(pa: PendingAction, user, msisdn: str, text: str) -> None:
    val = text.strip()
    if pa.state == "id_type":
        low = val.lower()
        if low in ("1", "bvn"):
            pa.payload["id_type"] = "bvn"
        elif low in ("2", "nin"):
            pa.payload["id_type"] = "nin"
        else:
            return reply(msisdn, "Reply *1* to use your BVN or *2* to use your NIN.")
        kind = pa.payload["id_type"]
        pa.state = "bvn"  # the masked identity-entry state, whichever ID was picked
        pa.expires_at = _flow_deadline(pa.state)
        pa.save(update_fields=["payload", "state", "expires_at"])
        # Same reasoning as the KYC ladder: this number must not land in the
        # thread. Every signup passes through here, so the chat fallback below
        # was the channel's widest remaining exposure.
        if _send_identity_flow(pa, kind, fallback_state="bvn"):
            return None
        if flows_live():
            _clear_actions(msisdn)
            return reply(msisdn, "⚠️ The secure entry screen didn't go through, so I won't ask for "
                                 "your ID number here in the chat. Reply *6* to try again in a "
                                 "moment, or finish setup in the Zitch app.")
        return reply(msisdn, f"Enter your 11-digit *{kind.upper()}*. It is used only to open your "
                             "account.\n\n_Delete your message afterwards (press and hold → Delete → "
                             "Delete for everyone) — WhatsApp only lets the sender do this._")
    if pa.state == "bvn":
        digits = "".join(ch for ch in val if ch.isdigit())
        if len(digits) != 11:
            return reply(msisdn, f"That should be exactly 11 digits. Enter your {pa.payload.get('id_type', 'BVN').upper()} again, or reply \"cancel\".")
        return _account_submit_identity(pa, user, msisdn, digits)
    if pa.state == "otp":
        if val.lower() == "resend":
            res = wema_provider.resend_wallet_otp(user.phone or "", pa.payload.get("tracking_id", ""),
                                                  bvn=bool(pa.payload.get("using_bvn")))
            if res.get("success"):
                return reply(msisdn, "📲 Code re-sent — enter it here.")
            return reply(msisdn, "⚠️ " + (res.get("message") or "Couldn't resend the code — try again shortly."))
        # Same completion as the Flow path, so a 400 means the same thing and the
        # pending action is cleared at the same moment on both. Only the wording
        # of a retry differs: here there is a chat to say "or resend" in.
        status, message = account_flow_otp(pa, val)
        if status == "retry":
            return reply(msisdn, "⚠️ " + message
                         + ' Try again, reply *resend* for a new code, or "cancel".')
        return None
    _clear_actions(msisdn)
    return send_menu(msisdn)



# --------------------------------------------------------------------------- #
# transfer (slot-filling state machine)
# --------------------------------------------------------------------------- #
def _blocked_from_spending(user, msisdn: str) -> bool:
    """Refuse to START a money flow for an account that cannot finish one.

    The authoritative gate has always been at debit time, under the wallet lock
    — money never actually left an unverified account. But gating only there
    walked a Tier-0 customer through amount, account, bank and the PIN screen
    before telling them no: the refusal came at the last step instead of the
    first, which reads as "I am able to transfer" right up until it isn't.
    Same rule, asked at the door.
    """
    from common.http import unverified_error

    message = unverified_error(user)
    if not message:
        return False
    _clear_actions(msisdn)
    reply(msisdn, f"🔒 {message}")
    return True


def _start_transfer(user, msisdn: str) -> None:
    if _blocked_from_spending(user, msisdn):
        return None
    _clear_actions(msisdn)
    # One private form (amount, account, searchable bank list) that chains into
    # the PIN screen with the recipient's resolved name — replacing the
    # question-by-question chat interrogation. The typed/AI path is untouched:
    # "send 2300 to Ada, opay, 91887..." still parses straight to a confirm.
    if flows_live():
        pa = _new_flow(user, msisdn, "transfer", FLOW_FORM_STATE, {"pin_attempts": 0})
        res = send_flow(
            msisdn, sign_flow_token(pa),
            header="Send money", body="Fill in the details privately — we'll confirm the "
                                      "account name before anything moves.",
            screen=TRANSFER_FORM, screen_data={"banks": _bank_items(), "error": ""},
            cta="Send money",
        )
        if res.get("success"):
            return reply(msisdn, "💸 Tap *Send money* on the secure form above.")
        log.warning("wa_transfer_form_send_failed pa=%s detail=%r",
                    pa.id, res.get("error_detail", ""))
        _clear_actions(msisdn)
    # Through _new_flow like every other money flow, not a hand-rolled create:
    # this fallback was the one narratable action born outside it, so an AI
    # message whose transfer details were incomplete ("send 5k for rent") lost
    # the narration on its way into the guided path.
    _new_flow(user, msisdn, "transfer", "amount", {"pin_attempts": 0})
    reply(msisdn, "How much would you like to send? (e.g. 5000 or 5k)")


def _is_new_command(text: str) -> bool:
    """Whether this message plainly starts something else.

    A keyword list was too narrow: "i want to create a new pin" is unmistakably a
    new request and matched nothing, so the customer was answered with "tap the
    secure screen" — the very stonewalling the escape hatch exists to prevent.

    While a Flow is open the customer has nothing to type in chat: the PIN goes
    in the Flow. So anything that is not digits is a new instruction. Digits stay
    excluded because a mistyped confirmation code must not cancel the payment it
    was meant for.
    """
    low = (text or "").strip().lower()
    if not low:
        return False
    if any(ch.isdigit() for ch in low) and not any(ch.isalpha() for ch in low):
        return False        # digits only — far more likely a code than a request
    return True


#: What to tell someone who has just put a secret in their own chat history.
#: WhatsApp has no view-once for text and lets only the SENDER delete, so this
#: is the only remedy that exists — and it is worth more than a refusal to read.
_DELETE_TIP = ("\n\n_Please delete your message (press and hold → Delete → Delete for "
               "everyone) — WhatsApp only lets the sender do this._")


def _identity_fallback_state(pa: PendingAction) -> str:
    """The chat state an armed identity step answers in when its value arrives
    in the thread rather than the Flow — the same state `_send_identity_flow`
    falls back to when the dispatch itself fails."""
    kind = str(pa.payload.get("id_kind", "bvn")).lower()
    if kind == ACCOUNT_OTP:
        return "otp"
    if kind == "email":
        return "email" if pa.payload.get("id_step") == "code" else "email_address"
    return str(pa.payload.get("id_fallback_state") or kind)


def _identity_answer_typed(pa: PendingAction, text: str) -> bool:
    """Whether this message IS the value the open identity step is waiting for.

    Shape-checked per step rather than "anything that isn't a command": an
    11-digit BVN and a 6-digit code are unmistakable, and an email address is
    the one step whose answer contains letters — so it has to be recognised
    before the new-instruction escape hatch, which would otherwise read it as a
    change of subject.
    """
    kind = str(pa.payload.get("id_kind", "bvn")).lower()
    val = (text or "").strip()
    if kind == "email" and pa.payload.get("id_step") != "code":
        return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", val))
    if any(ch.isalpha() for ch in val):
        return False
    digits = re.sub(r"\D", "", val)
    if not digits:
        return False
    # 11 for the identity number itself; 6 for every code that follows one.
    wanted = 11 if (kind in ("bvn", "nin") and not pa.payload.get("id_otp_hash")) else 6
    return len(digits) == wanted


def _accept_identity_in_chat(pa: PendingAction, user, msisdn: str, text: str) -> None:
    """Process an identity answer that was typed into the chat with its Flow open.

    Every branch here routes into the SAME function the chat fallback uses, so
    the two entry points cannot drift on validation, on attempt budgets, or on
    what a failure does to the pending action.
    """
    kind = str(pa.payload.get("id_kind", "bvn")).lower()
    val = text.strip()
    secret = not (kind == "email" and pa.payload.get("id_step") != "code")

    # The identity challenge code (the SMS that name-matches a BVN/NIN). It has
    # no chat state at all — it is only ever armed on a deploy with Flows live —
    # so it is checked here, mirroring flows._submit_identity_otp exactly.
    if kind not in (ACCOUNT_OTP, "email") and pa.payload.get("id_otp_hash"):
        status, message = kyc_flow_identity_otp(pa, val)
        if status == "retry":
            return reply(msisdn, "⚠️ " + message)
        reply(msisdn, message + (_DELETE_TIP if secret else ""))
        return None if status == "stop" else _kyc_next(pa, user, msisdn)

    _touch(pa, state=_identity_fallback_state(pa), payload=pa.payload)
    if secret:
        reply(msisdn, "🔐 Got it — I'll use what you typed." + _DELETE_TIP)
    handler = _advance_add_account if pa.action_type == "add_account" else _advance_kyc
    return handler(pa, user, msisdn, val)


def _advance(pa: PendingAction, user, msisdn: str, text: str) -> None:
    if pa.state == EXECUTING_STATE:
        # Authorised and in the worker's hands. Every other branch below can end
        # in _clear_actions, and clearing THIS row would delete a payment the
        # customer has already confirmed and been told is on its way — so it is
        # answered here and nowhere else.
        return reply(msisdn, "⏳ Your payment is going through — I'll send the receipt "
                             "here as soon as it lands.")
    if pa.state == FLOW_FORM_STATE:
        # The transfer form is open. Same escape hatch as the PIN screen below:
        # an unsubmitted form has moved no money, so a clear new instruction
        # replaces it rather than trapping the customer in "fill the form".
        #
        # This branch previously sat in _confirm_prompt(pa) — a string builder
        # with no `text` or `msisdn` in scope — where it was dead code one
        # routing change away from a NameError.
        if _is_new_command(text):
            _clear_actions(msisdn)
            reply(msisdn, "Okay — leaving that transfer.")
            return handle_inbound(msisdn, text)
        return reply(msisdn, "💸 Please fill the secure *Send money* form above — "
                             "or reply \"cancel\".")
    if pa.state == FLOW_VTU_STATE:
        # The airtime/data form. Same escape hatch as the transfer form: nothing
        # has been bought, so a clear new instruction replaces it rather than
        # trapping the customer in "fill the form".
        if _is_new_command(text):
            _clear_actions(msisdn)
            reply(msisdn, "Okay — leaving that purchase.")
            return handle_inbound(msisdn, text)
        return reply(msisdn, "📱 Please use the secure *Airtime & data* form above — "
                             "or reply \"cancel\".")
    if pa.state == FLOW_PIN_STATE:
        # A secure PIN Flow is open: the PIN is entered there, never in chat.
        #
        # But a customer who has moved on has moved on. Repeating "tap the secure
        # screen" at every message traps them: asking for a balance, then an
        # account number, then a different transfer all answered with the same
        # line and no way forward but a word they were never told first. An
        # unconfirmed payment has moved no money, so a clear new instruction
        # simply replaces it.
        if _is_new_command(text):
            _clear_actions(msisdn)
            reply(msisdn, "Okay — leaving that payment unconfirmed.")
            return handle_inbound(msisdn, text)
        cta = (getattr(settings, "WHATSAPP_FLOW", {}) or {}).get("CTA", "Confirm with PIN")
        return reply(msisdn, f"🔐 Tap *{cta}* on the secure screen I sent to enter your PIN — "
                             "it stays private and never appears in this chat. Or reply \"cancel\".")
    if pa.state == FLOW_ID_STATE:
        # Identity Flow open. Three different things arrive here and only one of
        # them used to get an answer.
        kind = str(pa.payload.get("id_kind", "bvn")).lower()
        low = text.strip().lower()
        if low == "resend":
            if kind == ACCOUNT_OTP:
                res = wema_provider.resend_wallet_otp(
                    user.phone or "", pa.payload.get("tracking_id", ""),
                    bvn=bool(pa.payload.get("using_bvn")))
                if res.get("success"):
                    return reply(msisdn, "📲 Code re-sent — enter it on the secure screen.")
                return reply(msisdn, "⚠️ " + (res.get("message")
                                              or "Couldn't resend the code — try again shortly."))
            if kind == "email" and pa.payload.get("id_step") == "code":
                # An expired code with no way to ask for another would strand the
                # customer inside a dead Flow.
                return _kyc_send_email_code(pa, user, msisdn)
            # No code to re-send at this step. Answered rather than falling
            # through: "resend" is plainly about the step in progress, and the
            # new-instruction hatch below would read it as abandoning it.
            return reply(msisdn, "There's no code to re-send at this step — "
                                 "enter what I asked for above, or reply \"cancel\".")
        if _identity_answer_typed(pa, text):
            # They typed it into the chat anyway. Refusing to READ it does not
            # un-send it: the number is already in the customer's own history,
            # and answering "use the secure screen" adds a dead end on top of an
            # exposure that has already happened — the loop in the screenshot,
            # where the same line came back to three different messages. So the
            # value is processed exactly as the no-Flows fallback processes it,
            # and the reply says how to delete the message.
            return _accept_identity_in_chat(pa, user, msisdn, text)
        if _is_new_command(text):
            # The same escape hatch the PIN screen and the transfer form have,
            # and missing here for no reason: an unfinished verification has
            # changed nothing, so "send 500 to Mutumin" is a new instruction
            # rather than one more thing to answer with "tap the secure screen".
            _clear_actions(msisdn)
            reply(msisdn, "Okay — leaving that verification.")
            return handle_inbound(msisdn, text)
        if kind == ACCOUNT_OTP:
            return reply(msisdn, "📲 That isn't the 6-digit code. Enter it on the secure screen "
                                 "I sent, or type it here — either works. Or reply \"cancel\".")
        if kind == "email":
            what = "email code" if pa.payload.get("id_step") == "code" else "email address"
            return reply(msisdn, f"📧 That doesn't look like your {what}. Enter it on the secure "
                                 "screen I sent, or type it here — either works. "
                                 "Or reply \"cancel\".")
        # An armed challenge means the screen is waiting for the SMS code that
        # name-matches the number, not for the number again.
        wanted = "the 6-digit code we sent you" if pa.payload.get("id_otp_hash") \
            else f"your 11-digit {kind.upper()}"
        return reply(msisdn, f"🪪 That isn't {wanted}. Enter it on the secure screen I sent, "
                             "or type it here — either works. Or reply \"cancel\".")
    handler = {
        "transfer": _advance_transfer,
        "airtime": _advance_airtime,
        "data": _advance_data,
        "electricity": _advance_electricity,
        "cable": _advance_cable,
        "convert": _advance_convert,
        "pick_service": _advance_pick_service,
        "add_account": _advance_add_account,
        "kyc": _advance_kyc,
    }.get(pa.action_type)
    if handler is None:
        _clear_actions(msisdn)
        return send_menu(msisdn)
    return handler(pa, user, msisdn, text)


def _advance_transfer(pa: PendingAction, user, msisdn: str, text: str) -> None:
    state = pa.state

    if state == "amount":
        amount = parse_amount(text)
        if amount is None or amount < MIN_TRANSFER:
            return reply(msisdn, f"Please enter a valid amount, at least ₦{MIN_TRANSFER:,.0f} (e.g. 5000 or 5k).")
        limit_msg = send_limit_error(user, amount) or daily_limit_error(user, amount, "transfer")
        if limit_msg:
            _clear_actions(msisdn)
            return _limit_reply(msisdn, user, limit_msg)
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
            shown = matches[:6]
            pa.payload["bank_choices"] = [b.code for b in shown]
            _touch(pa, state="bank_pick", payload=pa.payload)
            # The list caps at 6; with the full bank catalogue a loose name can
            # match more, so when we truncate, tell the user how to narrow it
            # instead of silently hiding the rest.
            prompt = ("I found a few banks — pick yours:" if len(matches) <= 6
                      else f"I found {len(matches)} banks — here are the first 6. "
                           "Reply a number, or type the bank's exact name:")
            return reply_list(msisdn, prompt,
                              [(str(i + 1), b.name[:24], "") for i, b in enumerate(shown)],
                              button_label="Banks")
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
    return send_menu(msisdn)


def _match_banks(text: str) -> list:
    t = text.strip().lower()
    banks = list(Bank.objects.filter(active=True))
    exact = [b for b in banks if b.name.lower() == t]
    if exact:
        return exact
    return [b for b in banks if t and (t in b.name.lower() or b.name.lower() in t)]


def _bank_items(candidates=None) -> list:
    """Dropdown data for the transfer form: {id, title} per active bank, popular
    first. When `candidates` (Bank rows) is given they lead the list — the
    NUBAN-narrowed suggestions — with everything else after, because a checksum
    match is a suggestion and the customer must stay able to pick any bank."""
    banks = list(Bank.objects.filter(active=True).order_by("-popular", "name")[:200])
    if candidates:
        heads = [b for b in banks if b.code in {c.code for c in candidates}]
        banks = heads + [b for b in banks if b.code not in {c.code for c in candidates}]
    return [{"id": b.code, "title": b.name} for b in banks[:200]]


def nuban_bank_candidates(account: str) -> list:
    """The active banks this 10-digit NUBAN is checksum-valid for.

    The CBN algorithm bakes the bank code into the check digit: weights 3,7,3
    cycling over bank_code + the 9-digit serial, check = (10 - sum mod 10) mod
    10. Running it against every bank's code narrows hundreds to a handful —
    which is why this SUGGESTS an ordering and never picks silently when more
    than one matches: a wrong guess is money at the wrong institution.
    """
    digits = "".join(ch for ch in str(account or "") if ch.isdigit())
    if len(digits) != 10:
        return []
    serial, check = digits[:9], int(digits[9])
    out = []
    for bank in Bank.objects.filter(active=True):
        code = "".join(ch for ch in (bank.bank_code or "") if ch.isdigit())
        if not code:
            continue
        seq = code + serial
        total = sum(int(c) * (3, 7, 3)[i % 3] for i, c in enumerate(seq))
        if (10 - total % 10) % 10 == check:
            out.append(bank)
    return out


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
    if not _arm_confirm(pa, user):
        return
    _send_confirm(pa, msisdn,
                  "Confirm transfer\n"
                  f"{_money(amount)} → {name.upper()}\n"
                  f"{bank.name} • {acct}")


def _flow_pin_ok(pa: PendingAction, user, msisdn: str, text: str) -> bool:
    """Shared confirm gate for every money flow: True if the reply is the armed
    single-use SMS code (preferred — the chat never carries the PIN) or, when no
    code was armed (dev/mock SMS), the transaction PIN. Sends the right message
    (expired / locked / retry / cancel) and returns False otherwise."""
    otp_hash = pa.payload.get("otp_hash", "")
    if otp_hash:
        exp = pa.payload.get("otp_exp", "")
        expired = True
        try:
            expired = timezone.now() > timezone.datetime.fromisoformat(exp)
        except (TypeError, ValueError):
            pass
        if expired:
            _clear_actions(msisdn)
            reply(msisdn, "That code has expired — cancelled for your safety. Reply \"menu\" to start over.")
            return False
        if check_password(text.strip(), otp_hash):
            pa.payload.pop("otp_hash", None)   # single use — a replay can't confirm twice
            _touch(pa, payload=pa.payload)
            return True
        attempts = int(pa.payload.get("pin_attempts", 0)) + 1
        if attempts >= PIN_FLOW_ATTEMPTS:
            _clear_actions(msisdn)
            reply(msisdn, "Too many wrong codes. Cancelled — reply \"menu\" to start over.")
            return False
        pa.payload["pin_attempts"] = attempts
        _touch(pa, payload=pa.payload)
        reply(msisdn, "That code isn't right. Check the SMS we sent and try again, or \"cancel\".")
        return False
    # The PIN-from-chat fallback is re-gated HERE, not only where the rung was
    # armed: _arm_confirm never arms it in production, but this function is the
    # point of acceptance, and an action that reaches "pin" without an armed
    # code on a production host (a stale row from a config flip, a future
    # arming bug) must fail closed rather than quietly start reading PINs out
    # of a chat that keeps them forever.
    if not _pin_in_chat_allowed():
        _clear_actions(msisdn)
        reply(msisdn, "For your security this needs to be confirmed in the Zitch app. "
                      "Nothing was sent — please start again.")
        return False
    ok, code, message = evaluate_transaction_pin(user, text)
    if ok:
        return True
    if code == "pin_locked":
        _clear_actions(msisdn)
        if user.pin_lock_is_escalated:
            message += " Reply *reset pin* to choose a new one."
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
    return _exec_transfer(pa, user, msisdn)


#: How often the settle wait re-reads the ledger. Short enough that a fast rail
#: is caught almost immediately, long enough that a 3-second budget is a handful
#: of queries rather than hundreds.
_SETTLE_POLL = 0.25

#: The three things that can have happened to money by the time a Flow closes,
#: plus the untagged default. The Flow's terminal screen shows one of them as its
#: heading, so "we tried" and "it worked" stop looking identical to the customer.
OUTCOME_SUCCESS = "success"
OUTCOME_PENDING = "pending"
OUTCOME_FAILED = "failed"


class Outcome(str):
    """An executor's closing line, tagged with what actually happened to the money.

    A plain `str` everywhere one is already used — the chat line, the approve
    API's `message`, the existing assertions — with one extra attribute the Flow
    endpoint reads to pick its terminal heading. A subclass rather than a tuple
    precisely so nothing that consumes these strings has to change, and so an
    executor that returns a bare string still renders correctly: untagged reads
    as `done`, which is what it has always meant.
    """

    status = "done"

    def __new__(cls, text: str, status: str = "done"):
        obj = super().__new__(cls, text)
        obj.status = status
        return obj


def _exec_transfer(pa: PendingAction, user, msisdn: str) -> str:
    """Execute a PIN-confirmed transfer (called by the chat PIN path AND the
    secure Flow endpoint). Sends the chat receipt and returns a short outcome
    line for the Flow success screen."""
    if velocity_exceeded(user):  # same fraud brake the app enforces (parity)
        _clear_actions(msisdn)
        msg = "Too many transactions in a short time. Please wait a few minutes and try again."
        reply(msisdn, msg)
        return Outcome(msg, OUTCOME_FAILED)
    amount = Decimal(pa.payload["amount"])
    bank = Bank.objects.filter(bank_code=pa.payload["bank_code"]).first()
    if bank is None:
        _clear_actions(msisdn)
        msg = f"Something went wrong with your {_money(amount)} transfer. Reply \"menu\" to start over."
        reply(msisdn, msg)
        return Outcome(msg, OUTCOME_FAILED)
    # Re-run the name enquiry immediately before paying, exactly as the app does
    # (transfers.views.bank_transfer). The name shown at the "bank" step can be
    # minutes old by the time the PIN comes back, and routing is purely by
    # {account_number, bank_code} — if the account now resolves to someone else,
    # paying against the stale name sends money to the wrong real person.
    confirmed_name = pa.payload["name"]
    fresh = payout_resolve_account(pa.payload["account"], bank.bank_code)
    if fresh.get("success"):
        fresh_name = (fresh.get("name") or "").strip()
        # Only enforce on a LIVE enquiry: the mock returns a fixed stub, which
        # would false-block every sandbox transfer.
        if not fresh.get("mock") and fresh_name and not _names_match(confirmed_name, fresh_name):
            _clear_actions(msisdn)
            msg = (f"This account now belongs to {fresh_name}, not {confirmed_name.upper()}. "
                   f"Your {_money(amount)} transfer was not sent — please check the account "
                   "number and start again.")
            reply(msisdn, msg)
            return Outcome(msg, OUTCOME_FAILED)
        confirmed_name = fresh_name or confirmed_name
    try:
        # Stable key per flow: a re-sent "pin" message can't double-pay.
        txn = execute_payout(
            user, amount, pa.payload["account"], bank, confirmed_name,
            # The customer's own words on the beneficiary's statement. Falls back
            # inside execute_payout to "Transfer to <name>" when it is blank,
            # which is what every transfer sent before narration existed.
            note=_narration(pa),
            idempotency_key=f"wa-{pa.id}", channel="whatsapp",
        )
    except PayoutError as exc:
        _clear_actions(msisdn)
        who = pa.payload["name"].upper()
        if exc.kind == "insufficient":
            msg = f"Insufficient balance for the {_money(amount)} transfer to {who} — cancelled."
            reply(msisdn, msg)
            return Outcome(msg, OUTCOME_FAILED)
        if exc.kind == "duplicate":
            msg = f"That {_money(amount)} transfer to {who} was already processed."
            reply(msisdn, msg)
            return Outcome(msg, OUTCOME_FAILED)
        msg = f"Transfer of {_money(amount)} to {who} failed: {exc.message}"
        reply(msisdn, msg)
        return Outcome(msg, OUTCOME_FAILED)

    _clear_actions(msisdn)

    # execute_payout returns a PENDING row for a queued (PROCESSING) transfer
    # AND for the ambiguous send-timeout / lost-response case where the rail may
    # or may not have paid the recipient (transfers/services.py holds the debit
    # rather than refunding a maybe-delivered transfer). Only a SETTLED-success
    # row may be announced as "Successful": a receipt is a forwardable proof of
    # payment, and issuing one for a transfer that is only pending — or may have
    # failed — is the single worst thing a banking channel can tell a customer.
    # Mirror _run_vtu's pending branch and the app path (transfers/views.py),
    # which both report "processing" and let the webhook / reconciler settle it.
    from wallet.alerts import mark_awaiting_settlement
    from wallet.models import Transaction

    if txn.transaction_status != Transaction.SUCCESS:
        # "processing" is not an outcome, so the settlement alert has to be let
        # through when the row finally resolves — otherwise this line is the last
        # word the customer ever gets on the money.
        mark_awaiting_settlement(txn)
        line = (f"⏳ Your transfer of {_money(amount)} to {pa.payload['name'].upper()} "
                f"is processing — we'll confirm once it settles. Ref {txn.reference}.")
        reply(msisdn, line)
        return Outcome(line, OUTCOME_PENDING)

    wallet = get_or_create_wallet(user)
    reply_receipt(msisdn, "Transfer receipt", _with_narration(pa, [
        ("To", pa.payload["name"].upper()),
        ("Bank", pa.payload["bank_name"]),
        ("Account", pa.payload["account"]),
        ("Amount", _money(amount)),
        ("Reference", txn.reference),
        ("Date", timezone.now().strftime("%d %b %Y, %H:%M")),
    ]), ref=txn.reference, user=user, balance_after=wallet.balance)
    # A settled transfer is the one case that may be CALLED successful, and it is
    # now said rather than inferred: this used to return whatever reply_receipt
    # gave back, which run_flow_execution turned into a bare "Done ✅" — the same
    # words a cancelled transfer closed on.
    return Outcome(f"{_money(amount)} sent to {pa.payload['name'].upper()} — "
                   f"the receipt is in your chat.", OUTCOME_SUCCESS)


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
    if _blocked_from_spending(user, msisdn):
        return True
    matches = _match_banks(bank_query)
    # The NUBAN carries its own bank code in the check digit, so a message that
    # names no bank is not missing anything — "send 1000 to Mutumin 2217940528"
    # was answered with the guided form purely because `bank_query` had no bank
    # name in it, which reads as the assistant ignoring a complete instruction.
    # The checksum resolves it, and the name enquiry below is the real safety
    # net either way. It also NARROWS an ambiguous name match: _match_banks does
    # substring matching over a whole sentence, so several banks matching is
    # common and the account number can only belong to one of them.
    nuban = nuban_bank_candidates(acct)
    if nuban:
        narrowed = [b for b in matches if b.code in {c.code for c in nuban}]
        matches = narrowed or (matches if matches else nuban)
    if not matches:
        return False
    if amount < MIN_TRANSFER:
        reply(msisdn, f"Minimum transfer is ₦{MIN_TRANSFER:,.0f}.")
        return True
    limit_msg = send_limit_error(user, amount) or daily_limit_error(user, amount, "transfer")
    if limit_msg:
        _limit_reply(msisdn, user, limit_msg)
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

# What customers actually call their disco. DISCO_NAMES holds the short label the
# menu shows ("Ikeja"), but nobody says that: they say IKEDC, or "Ikeja Electric",
# and a bill arrives with the abbreviation on it. Without these, naming your disco
# in the message got you the same "Which disco?" list as saying nothing at all.
#
# Note what is deliberately ABSENT: "nepa" (and "phcn"). Both mean electricity in
# general, not a company — "load my nepa bill" says nothing about which disco, and
# guessing one from it would put the wrong meter in front of a payment.
DISCO_ALIASES = {
    "ikedc": "1", "ikejaelectric": "1", "ikejaelectricity": "1", "ikejadisco": "1",
    "ikejadistribution": "1", "ikejaelectriccompany": "1",
    "ekedc": "2", "ekoelectric": "2", "ekoelectricity": "2", "ekodisco": "2",
    "ekodistribution": "2", "ekoelectricitydistributioncompany": "2",
    "aedc": "3", "abujaelectric": "3", "abujaelectricity": "3", "abujadisco": "3",
    "abujadistribution": "3",
    "kedco": "4", "kanoelectric": "4", "kanoelectricity": "4", "kanodisco": "4",
    "kanodistribution": "4",
    "phed": "5", "phedc": "5", "portharcourtelectric": "5", "portharcourtdisco": "5",
    "portharcourtelectricity": "5", "phdisco": "5", "phelectric": "5",
    "jed": "6", "jedc": "6", "jedplc": "6", "joselectric": "6", "josdisco": "6",
    "joselectricity": "6",
    "kaedco": "7", "kadunaelectric": "7", "kadunadisco": "7", "kadunaelectricity": "7",
    "eedc": "8", "enuguelectric": "8", "enugudisco": "8", "enuguelectricity": "8",
    "ibedc": "9", "ibadanelectric": "9", "ibadandisco": "9", "ibadanelectricity": "9",
}
# The cable equivalents. "gotv"/"dstv" already match CABLE_NAMES exactly; these are
# the spellings and parent-brand names that do not.
CABLE_ALIASES = {
    "go": "1", "gotvnigeria": "1", "multichoicegotv": "1",
    "dstvnigeria": "2", "multichoice": "2", "dstvng": "2",
    "startime": "3", "startimesnigeria": "3", "startimestv": "3",
}


def _alias_id(text, names: dict, aliases: dict) -> str | None:
    """Resolve a biller the customer NAMED to its menu id — the number, the menu
    label, or any of the names the thing is actually known by."""
    direct = _choice_id(str(text or ""), names)
    if direct is not None:
        return direct
    key = re.sub(r"[^a-z0-9]", "", str(text or "").lower())
    return aliases.get(key) if key else None


def _disco_id(text) -> str | None:
    return _alias_id(text, DISCO_NAMES, DISCO_ALIASES)


def _cable_id(text) -> str | None:
    return _alias_id(text, CABLE_NAMES, CABLE_ALIASES)


def _meter_type(text) -> str | None:
    """Prepaid or postpaid, however it arrives: the menu digit, the word typed
    out, or the LLM's `variation` field ("prepaid meter", "POSTPAID")."""
    raw = str(text or "").strip().lower()
    if raw in ("1", "2"):
        return "prepaid" if raw == "1" else "postpaid"
    t = re.sub(r"[^a-z]", "", raw)
    if "postpaid" in t:
        return "postpaid"
    if "prepaid" in t:
        return "prepaid"
    return None


#: The narration the CURRENT message asked for, for the duration of dispatching
#: it. Set once by dispatch_intent and read once by _new_flow.
#:
#: A context variable rather than a parameter because the alternative is
#: threading an optional string through every _begin_*/_start_* entry point and
#: every _new_flow call inside them — eight signatures to carry one word that
#: only the AI path can supply, where each new money flow added later is another
#: chance to forget it. Scoped to one dispatch and cleared in a finally, so it
#: cannot leak into the next message; a ContextVar (not a global) so concurrent
#: requests on the same worker cannot read each other's.
_ai_narration: contextvars.ContextVar[str] = contextvars.ContextVar("ai_narration", default="")

#: Action types with money and a receipt behind them — the only ones a narration
#: means anything for. An unlock or a PIN reset has nothing to narrate.
_NARRATABLE = ("transfer", "airtime", "data", "electricity", "cable")


def _new_flow(user, msisdn: str, action_type: str, state: str, payload: dict | None = None) -> PendingAction:
    _clear_actions(msisdn)
    payload = payload if payload is not None else {"pin_attempts": 0}
    note = _ai_narration.get("")
    if note and action_type in _NARRATABLE and "narration" not in payload:
        # Stamped at birth, before anything arms a confirm: the Flow's send
        # payload is built from this payload, so a narration attached afterwards
        # would be missing from the very screen it exists to appear on.
        payload = {**payload, "narration": note}
    return PendingAction.objects.create(
        user=user, msisdn=msisdn, action_type=action_type, state=state,
        payload=payload, expires_at=_flow_deadline(state),
    )


def _phone_from(text: str, user) -> str | None:
    """'me' -> the user's own number; else the digits typed (>= 10)."""
    if text.strip().lower() in ("me", "self", "mine"):
        return (user.phone or "").lstrip("+")
    digits = re.sub(r"\D", "", text)
    return digits if len(digits) >= 10 else None


def _insufficient(user, amount: Decimal) -> bool:
    return get_or_create_wallet(user).balance < amount


def _vtu_detail(pa: PendingAction, amount: Decimal) -> str:
    """The amount + who/what this VTU purchase was for, as one parenthetical —
    the same recipient/details the confirm screen showed. Every outcome line
    (pending, failed, or an early refusal) quotes this so a customer reading
    only the LAST message in the thread still knows what was being paid for,
    not just that something was."""
    fields = _flow_fields(pa)
    recip = " · ".join(x for x in (fields.get("recipient", ""), fields.get("details", "")) if x)
    return f"{_money(amount)}{' — ' + recip if recip else ''}"


def _run_vtu(pa: PendingAction, user, msisdn: str, amount: Decimal, label: str,
             provider_call, receipt) -> str:
    """Debit -> provider -> settle via the shared run_provider_purchase, then send
    the outcome. On success `receipt(txn, result)` returns ``(title, rows)`` and we
    send a branded, downloadable receipt JPEG. Returns the outcome text (also used
    verbatim on the secure Flow's success screen)."""
    detail = _vtu_detail(pa, amount)
    # Enforce the per-txn tier ceiling + large-transfer face step-up here, so EVERY
    # VTU path is gated regardless of entry point (the AI-prefilled fast-paths reach
    # this without the guided flow's own send_limit_error check, which would
    # otherwise let a Tier-3-without-face user skip the >=₦100k face requirement).
    send_msg = send_limit_error(user, amount)
    if send_msg:
        _clear_actions(msisdn)
        _limit_reply(msisdn, user, send_msg)
        return Outcome(send_msg, OUTCOME_FAILED)
    bill_limit_msg = daily_limit_error(user, amount, "bill")
    if bill_limit_msg:
        _clear_actions(msisdn)
        _limit_reply(msisdn, user, bill_limit_msg)
        return Outcome(bill_limit_msg, OUTCOME_FAILED)
    if velocity_exceeded(user):  # same fraud brake the app enforces (parity)
        _clear_actions(msisdn)
        msg = "Too many transactions in a short time. Please wait a few minutes and try again."
        reply(msisdn, msg)
        return Outcome(msg, OUTCOME_FAILED)
    try:
        # The note rides the ledger row so the settlement alert can quote it
        # later — by then the pending action is long gone, and the alert is
        # often the only thing the customer reads about a bill they paid.
        purchase_meta = {**pa.payload.get("meta", {}), "channel": "whatsapp"}
        if _narration(pa):
            purchase_meta["narration"] = _narration(pa)
        status, txn, result = run_provider_purchase(
            user, amount, label, purchase_meta, provider_call,
            idempotency_key=f"wa-{pa.id}",
        )
    except InsufficientFunds:
        _clear_actions(msisdn)
        line = f"Insufficient balance for {label} ({detail}) — cancelled."
        reply(msisdn, line)
        return Outcome(line, OUTCOME_FAILED)
    except LimitExceeded as exc:
        _clear_actions(msisdn)
        line = f"{exc} ({label}, {detail})"
        reply(msisdn, line)
        return Outcome(line, OUTCOME_FAILED)
    except DuplicateTransaction:
        _clear_actions(msisdn)
        line = f"That {label} ({detail}) was already processed."
        reply(msisdn, line)
        return Outcome(line, OUTCOME_FAILED)
    _clear_actions(msisdn)
    if status == "success":
        title, rows = receipt(txn, result)
        reply_receipt(msisdn, title, _with_narration(pa, rows), ref=txn.reference,
                      user=user, balance_after=get_or_create_wallet(user).balance)
        # Named rather than inferred, for the same reason as the transfer path.
        return Outcome(f"{label} successful — the receipt is in your chat.", OUTCOME_SUCCESS)
    if status == "pending":
        # Same as the transfer path: the chat can only say "processing", so the
        # alert on the eventual settlement must not be de-duped away as an echo
        # of a receipt this branch never sent.
        from wallet.alerts import mark_awaiting_settlement

        mark_awaiting_settlement(txn)
        line = (f"⏳ Your {label} ({detail}) is processing — we'll confirm shortly. "
                f"Ref {txn.reference}.")
        reply(msisdn, line)
        return Outcome(line, OUTCOME_PENDING)
    line = (f"❌ {label} ({detail}) failed: {result.get('message', 'please try again')}. "
            f"You were not charged.")
    reply(msisdn, line)
    return Outcome(line, OUTCOME_FAILED)


# ---- service sub-menus (tap 1 or 2 — no need to type "airtime"/"data" etc.) ----
# Each entry: (prompt, [(id, label, desc), ...]). The row ids ("1"/"2") are what
# the router receives back from a tap or a typed number, mirroring the network menu.
SERVICE_MENUS = {
    "airtime_data": ("What would you like to buy?",
                     [("1", "Airtime", ""), ("2", "Data", "")]),
    "bill": ("Which bill would you like to pay?",
             [("1", "Electricity", ""), ("2", "Cable TV", "")]),
}


def _start_vtu(user, msisdn: str) -> None:
    """Menu 3 — airtime and data as ONE Flow instead of four chat round-trips.

    The chat ladder asked what to buy, then the network, then the number, then
    the amount, each its own message and each a place to get stuck; the customer
    in the screenshot was four taps in and still had two to go. The Flow asks the
    same four things as four pages of one session and chains into the PIN, so the
    whole purchase is one interaction.

    Same fallback as the transfer form: if the Flow cannot be sent, the guided
    chat sub-menu still works. Failing closed would take airtime away from every
    deploy without Flows configured, and nothing here is secret — the PIN is
    still collected on its own encrypted page either way.
    """
    if _blocked_from_spending(user, msisdn):
        return None
    _clear_actions(msisdn)
    if flows_live():
        # action_type is provisional: the FIRST page decides airtime vs data and
        # rewrites it, because the executor is chosen by action_type later.
        pa = _new_flow(user, msisdn, "airtime", FLOW_VTU_STATE,
                       {"vtu_step": "kind", "pin_attempts": 0})
        res = send_flow(
            msisdn, sign_flow_token(pa),
            header="Airtime & data",
            body="Pick what you need and we'll take it from there — your PIN stays private.",
            screen=VTU_SCREEN, screen_data={"error": ""},
            cta="Buy",
        )
        if res.get("success"):
            return reply(msisdn, "📱 Tap *Buy* on the secure form above.")
        log.warning("wa_vtu_flow_send_failed pa=%s detail=%r", pa.id, res.get("error_detail", ""))
        _clear_actions(msisdn)
    return _start_service_menu(user, msisdn, "airtime_data")


def _start_service_menu(user, msisdn: str, kind: str) -> None:
    """Open a category sub-menu so the user picks 1 or 2 (or taps the row) instead
    of having to type the word. Stored as a `pick_service` flow so a bare "1"/"2"
    is read as the sub-menu choice, not the main-menu balance/transfer number."""
    body, rows = SERVICE_MENUS[kind]
    _new_flow(user, msisdn, "pick_service", kind)
    reply_list(msisdn, body, rows, button_label="Choose")


def _advance_pick_service(pa: PendingAction, user, msisdn: str, text: str) -> None:
    """Route a sub-menu pick to the matching guided flow. Accepts the number
    (1/2) or the word, so a tap, a typed number and typed text all work. Each
    _start_* clears this pending action (via _new_flow) as it opens its own flow."""
    choice = text.strip().lower()
    if pa.state == "airtime_data":
        if choice in ("1", "airtime"):
            return _start_airtime(user, msisdn)
        if choice in ("2", "data"):
            return _start_data(user, msisdn)
        _, rows = SERVICE_MENUS["airtime_data"]
        return reply_list(msisdn, "Reply 1 for Airtime or 2 for Data.", rows, button_label="Choose")
    if choice in ("1", "electricity", "light", "nepa", "power"):
        return _start_electricity(user, msisdn)
    if choice in ("2", "cable", "tv", "dstv", "gotv", "startimes"):
        return _start_cable(user, msisdn)
    _, rows = SERVICE_MENUS["bill"]
    return reply_list(msisdn, "Reply 1 for Electricity or 2 for Cable TV.", rows, button_label="Choose")


def _choice_id(text: str, names: dict) -> str | None:
    """Resolve a menu reply to its id: the number itself ("1") or the option's
    name typed out ("MTN", "gotv", "Port Harcourt"). A tapped list row sends the
    id, but over the text fallback users type the name they can see — both must
    work (case/space-insensitive)."""
    t = text.strip()
    if t in names:
        return t
    tl = re.sub(r"\s", "", t.lower())
    for k, v in names.items():
        if tl and re.sub(r"\s", "", v.lower()) == tl:
            return k
    return None


# ---- airtime ----
def _start_airtime(user, msisdn: str) -> None:
    if _blocked_from_spending(user, msisdn):
        return None
    _new_flow(user, msisdn, "airtime", "network")
    _ask_network(msisdn)


def _advance_airtime(pa: PendingAction, user, msisdn: str, text: str) -> None:
    st = pa.state
    if st == "network":
        net = _choice_id(text, NETWORK_NAMES)
        if net is None:
            return _ask_network(msisdn)
        pa.payload["net"] = net
        # The number may already be known (the AI path collects it first when the
        # message named a person). Asking for it again reads as not listening.
        if pa.payload.get("phone"):
            _touch(pa, state="amount", payload=pa.payload)
            known = pa.payload.get("amount")
            if known:
                return _advance_airtime(pa, user, msisdn, known)
            return reply(msisdn, "How much airtime? Type the amount (e.g. 200). Minimum ₦50.")
        _touch(pa, state="phone", payload=pa.payload)
        return reply(msisdn, "What phone number? Reply \"me\" to use your own.")
    if st == "phone":
        phone = _phone_from(text, user)
        if not phone:
            return reply(msisdn, "Enter a valid phone number (or \"me\").")
        pa.payload["phone"] = phone
        # A number reached this rung from "recharge tobi 2k" — the amount was in
        # the message and the prefix names the network, so asking for either
        # again is asking twice for something already said.
        pa.payload.setdefault("net", _network_from_prefix(phone) or "")
        _touch(pa, state="amount", payload=pa.payload)
        known = pa.payload.get("amount")
        if known and pa.payload.get("net"):
            return _advance_airtime(pa, user, msisdn, known)
        if not pa.payload.get("net"):
            _touch(pa, state="network", payload=pa.payload)
            return _ask_network(msisdn)
        return reply(msisdn, "How much airtime? Type the amount (e.g. 200). Minimum ₦50.")
    if st == "amount":
        amount = parse_amount(text)
        if amount is None or amount < MIN_AIRTIME:
            return reply(msisdn, f"Enter a valid amount, at least ₦{MIN_AIRTIME:,.0f}.")
        if _insufficient(user, amount):
            return reply(msisdn, f"Insufficient balance ({_money(get_or_create_wallet(user).balance)}).")
        # "bill", not "transfer" — airtime accrues against the bill cap, and
        # checking the wrong bucket both refused purchases the app allows and
        # let through ones it blocks (caught only later, after the OTP).
        limit_msg = send_limit_error(user, amount) or daily_limit_error(user, amount, "bill")
        if limit_msg:
            _clear_actions(msisdn)
            return _limit_reply(msisdn, user, limit_msg)
        net = NETWORK_NAMES[pa.payload["net"]]
        pa.payload["amount"] = str(amount)
        pa.payload["meta"] = {"phone": pa.payload["phone"], "network": pa.payload["net"]}
        if not _arm_confirm(pa, user):
            return
        return _send_confirm(
            pa, msisdn,
            f"📱 *Confirm airtime*\n{_money(amount)} {net} → {pa.payload['phone']}",
            logo=provider_logo(net))
    if st == "pin":
        if not _flow_pin_ok(pa, user, msisdn, text):
            return
        return _exec_airtime(pa, user, msisdn)
    _clear_actions(msisdn)
    return send_menu(msisdn)


def _exec_airtime(pa: PendingAction, user, msisdn: str) -> str:
    amount = Decimal(pa.payload["amount"])
    net = NETWORK_NAMES[pa.payload["net"]]
    phone = pa.payload["phone"]
    return _run_vtu(
        pa, user, msisdn, amount, f"Airtime — {net}",
        lambda ref: vtu_purchase(f"{net.lower()}-airtime",
                                 {"amount": str(amount), "phone": phone}, reference=ref),
        lambda txn, res: ("Airtime receipt", [
            ("Network", net), ("Phone", phone), ("Amount", _money(amount)),
            ("Reference", txn.reference), ("Date", timezone.now().strftime("%d %b %Y, %H:%M"))]),
    )


# ---- data ----
def _start_data(user, msisdn: str) -> None:
    if _blocked_from_spending(user, msisdn):
        return None
    _new_flow(user, msisdn, "data", "network")
    _ask_network(msisdn)


def _advance_data(pa: PendingAction, user, msisdn: str, text: str) -> None:
    st = pa.state
    if st == "network":
        net = _choice_id(text, NETWORK_NAMES)
        if net is None:
            return _ask_network(msisdn)
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
        limit_msg = send_limit_error(user, price) or daily_limit_error(user, price, "bill")
        if limit_msg:
            _clear_actions(msisdn)
            return _limit_reply(msisdn, user, limit_msg)
        net = NETWORK_NAMES[pa.payload["net"]]
        pa.payload["phone"] = phone
        pa.payload["meta"] = {"phone": phone, "network": pa.payload["net"], "plan_code": pa.payload["plan_code"]}
        if not _arm_confirm(pa, user):
            return
        return _send_confirm(
            pa, msisdn,
            f"🌐 *Confirm data*\n{pa.payload['plan_name']} ({net}) → {phone}\n{_money(price)}",
            logo=provider_logo(net))
    if st == "pin":
        if not _flow_pin_ok(pa, user, msisdn, text):
            return
        return _exec_data(pa, user, msisdn)
    _clear_actions(msisdn)
    return send_menu(msisdn)


def _exec_data(pa: PendingAction, user, msisdn: str) -> str:
    net = NETWORK_NAMES[pa.payload["net"]]
    phone, plan_code, price = pa.payload["phone"], pa.payload["plan_code"], Decimal(pa.payload["price"])
    return _run_vtu(
        pa, user, msisdn, price, f"Data — {net} {pa.payload['plan_name']}",
        lambda ref: vtu_purchase(f"{net.lower()}-data",
                                 {"billersCode": phone, "variation_code": plan_code, "phone": phone}, reference=ref),
        lambda txn, res: ("Data receipt", [
            ("Network", net), ("Plan", pa.payload["plan_name"]), ("Phone", phone),
            ("Amount", _money(price)), ("Reference", txn.reference),
            ("Date", timezone.now().strftime("%d %b %Y, %H:%M"))]),
    )


# ---- electricity ----
def _start_electricity(user, msisdn: str) -> None:
    if _blocked_from_spending(user, msisdn):
        return None
    pa = _new_flow(user, msisdn, "electricity", "disco")
    _electricity_next(pa, user, msisdn)


def _begin_electricity(user, msisdn: str, biller, customer_id, variation, amount) -> None:
    """Start an electricity payment from details the customer already gave.

    "Load my nepa bill. 2000010657 5,000 to IKEDC" names the disco, the meter and
    the amount in one line. Discarding all three and opening with "Which disco?"
    is the channel telling someone it did not read their message — so whatever
    arrives is put in the flow's payload up front and the flow asks only for what
    is genuinely still missing.

    Nothing is trusted for being pre-filled: the meter is verified with the
    provider exactly as a typed one is, the amount goes through the same minimum,
    balance and limit checks, and the payment still ends at the same confirm +
    PIN. Pre-filling changes which QUESTIONS get asked, never which CHECKS run.
    """
    if _blocked_from_spending(user, msisdn):
        return None
    payload = {"pin_attempts": 0}
    disco = _disco_id(biller)
    if disco:
        payload["disco"] = disco
    mt = _meter_type(variation)
    if mt:
        payload["meter_type"] = mt
    meter = re.sub(r"\D", "", str(customer_id or ""))
    if len(meter) >= 6:
        payload["meter"] = meter
    amt = parse_amount(str(amount)) if amount is not None else None
    if amt is not None and amt >= MIN_ELECTRICITY:
        payload["amount"] = str(amt)
    pa = _new_flow(user, msisdn, "electricity", "disco", payload)
    _electricity_next(pa, user, msisdn)


def _electricity_next(pa: PendingAction, user, msisdn: str) -> None:
    """Ask for the first detail still missing — or, when nothing is, confirm.

    The order of the questions is unchanged; what changed is that each one is now
    conditional on not already knowing the answer. A customer who types every
    answer walks the same path they always did.
    """
    p = pa.payload
    if not p.get("disco"):
        _touch(pa, state="disco", payload=p)
        return reply(msisdn, DISCO_PROMPT)
    if not p.get("meter_type"):
        _touch(pa, state="meter_type", payload=p)
        return reply(msisdn, "Prepaid or postpaid? Reply 1 Prepaid or 2 Postpaid.")
    if not p.get("meter"):
        _touch(pa, state="meter", payload=p)
        return reply(msisdn, "Enter the meter number.")

    note = ""
    if not p.get("meter_verified"):
        disco = DISCO_NAMES[p["disco"]].lower()
        res = vtu_verify_customer(f"{disco}-electric", p["meter"], p["meter_type"])
        if not res.get("success"):
            # Drop the number that failed. Keeping it would re-verify the same bad
            # meter on the next message and never let the customer past it.
            p.pop("meter", None)
            _touch(pa, state="meter", payload=p)
            return reply(msisdn, "Couldn't validate that meter. Check the number and try again, or \"cancel\".")
        cust = res.get("customer_name", "")
        p.update({"customer": cust, "meter_verified": True})
        note = f"Meter verified{f' ({cust})' if cust else ''}. "

    if not p.get("amount"):
        _touch(pa, state="amount", payload=p)
        return reply(msisdn, note + "How much do you want to buy? (e.g. 5000)")
    return _electricity_confirm(pa, user, msisdn, note)


def _electricity_confirm(pa: PendingAction, user, msisdn: str, note: str = "") -> None:
    """The amount is known: run every money check, then arm the confirm."""
    p = pa.payload
    amount = parse_amount(p.get("amount", ""))
    if amount is None or amount < MIN_ELECTRICITY:
        p.pop("amount", None)
        _touch(pa, state="amount", payload=p)
        return reply(msisdn, f"Enter a valid amount, at least ₦{MIN_ELECTRICITY:,.0f}.")
    if _insufficient(user, amount):
        # Asked-for amounts stay recoverable: drop it and let them name another
        # rather than end the flow they are halfway through.
        p.pop("amount", None)
        _touch(pa, state="amount", payload=p)
        return reply(msisdn, f"Insufficient balance ({_money(get_or_create_wallet(user).balance)}).")
    limit_msg = send_limit_error(user, amount) or daily_limit_error(user, amount, "bill")
    if limit_msg:
        _clear_actions(msisdn)
        return _limit_reply(msisdn, user, limit_msg)
    disco_name = DISCO_NAMES[p["disco"]]
    p["amount"] = str(amount)
    p["meta"] = {"meter": p["meter"], "disco": p["disco"], "meter_type": p["meter_type"]}
    if not _arm_confirm(pa, user):
        return
    cust = p.get("customer") or "—"
    return _send_confirm(
        pa, msisdn,
        note +
        f"💡 *Confirm electricity*\n{disco_name} ({p['meter_type']}) • "
        f"Meter {p['meter']}\nCustomer: {cust} • {_money(amount)}")


def _advance_electricity(pa: PendingAction, user, msisdn: str, text: str) -> None:
    st = pa.state
    if st == "disco":
        # Named, not just numbered: "IKEDC" at this prompt now works too.
        d = _disco_id(text)
        if d is None:
            return reply(msisdn, "Reply with the disco number.\n" + DISCO_PROMPT)
        pa.payload["disco"] = d
        return _electricity_next(pa, user, msisdn)
    if st == "meter_type":
        mt = _meter_type(text)
        if not mt:
            return reply(msisdn, "Reply 1 Prepaid or 2 Postpaid.")
        pa.payload["meter_type"] = mt
        return _electricity_next(pa, user, msisdn)
    if st == "meter":
        meter = re.sub(r"\s", "", text)
        if len(meter) < 6:
            return reply(msisdn, "Enter a valid meter number.")
        pa.payload.update({"meter": meter, "meter_verified": False})
        return _electricity_next(pa, user, msisdn)
    if st == "amount":
        amount = parse_amount(text)
        if amount is None or amount < MIN_ELECTRICITY:
            return reply(msisdn, f"Enter a valid amount, at least ₦{MIN_ELECTRICITY:,.0f}.")
        pa.payload["amount"] = str(amount)
        return _electricity_confirm(pa, user, msisdn)
    if st == "pin":
        if not _flow_pin_ok(pa, user, msisdn, text):
            return
        return _exec_electricity(pa, user, msisdn)
    _clear_actions(msisdn)
    return send_menu(msisdn)


def _exec_electricity(pa: PendingAction, user, msisdn: str) -> str:
    amount = Decimal(pa.payload["amount"])
    disco = DISCO_NAMES[pa.payload["disco"]].lower()
    disco_name = DISCO_NAMES[pa.payload["disco"]]
    meter, mt = pa.payload["meter"], pa.payload["meter_type"]

    def _receipt_rows(txn, res):
        token = res.get("token") or res.get("provider_reference", "")
        rows = [("Disco", disco_name), ("Meter", f"{meter} ({mt})"), ("Amount", _money(amount))]
        if token:
            rows.append(("Token", token))
        rows += [("Reference", txn.reference), ("Date", timezone.now().strftime("%d %b %Y, %H:%M"))]
        return ("Electricity receipt", rows)

    return _run_vtu(
        pa, user, msisdn, amount, f"Electricity — {disco_name}",
        lambda ref: vtu_purchase(f"{disco}-electric",
                                 {"billersCode": meter, "variation_code": mt, "amount": str(amount)}, reference=ref),
        _receipt_rows,
    )


# ---- cable ----
def _start_cable(user, msisdn: str) -> None:
    if _blocked_from_spending(user, msisdn):
        return None
    _new_flow(user, msisdn, "cable", "provider")
    reply(msisdn, CABLE_PROMPT)


def _begin_cable(user, msisdn: str, biller, customer_id) -> None:
    """Cable's half of the same courtesy: a named provider skips the provider
    list, and a smartcard number given up front is not asked for again. The
    package still has to be chosen — it carries the price — and the card is still
    verified with the provider before anything is confirmed."""
    if _blocked_from_spending(user, msisdn):
        return None
    payload = {"pin_attempts": 0}
    card = re.sub(r"\D", "", str(customer_id or ""))
    if len(card) >= 6:
        payload["iuc_given"] = card
    prov = _cable_id(biller)
    pa = _new_flow(user, msisdn, "cable", "provider", payload)
    if not prov:
        return reply(msisdn, CABLE_PROMPT)
    return _cable_packages(pa, user, msisdn, prov)


def _cable_packages(pa: PendingAction, user, msisdn: str, prov: str) -> None:
    """Show the chosen provider's packages (the step every cable payment reaches,
    whether the provider was tapped from the list or named in the message)."""
    plans = list(CablePlan.objects.filter(provider=prov, active=True)[:8])
    if not plans:
        _clear_actions(msisdn)
        return reply(msisdn, "No packages available for that provider right now.")
    pa.payload["prov"] = prov
    pa.payload["plan_choices"] = [pl.cable_plan_code for pl in plans]
    _touch(pa, state="plan", payload=pa.payload)
    lines = "\n".join(f"{i+1}  {pl.name} • {_money(pl.price)}" for i, pl in enumerate(plans))
    return reply(msisdn, "Choose a package:\n" + lines)


def _advance_cable(pa: PendingAction, user, msisdn: str, text: str) -> None:
    st = pa.state
    if st == "provider":
        p = _cable_id(text)
        if p is None:
            return reply(msisdn, "Reply with the provider number.\n" + CABLE_PROMPT)
        return _cable_packages(pa, user, msisdn, p)
    if st == "plan":
        plan = _pick(text, pa.payload.get("plan_choices", []),
                     lambda c: CablePlan.objects.filter(cable_plan_code=c).first())
        if plan is None:
            return reply(msisdn, "Reply with a package number from the list, or \"cancel\".")
        pa.payload.update({"plan_code": plan.cable_plan_code, "price": str(plan.price), "plan_name": plan.name})
        _touch(pa, state="iuc", payload=pa.payload)
        given = pa.payload.get("iuc_given")
        if given:
            # Already told us the card — verify it now instead of asking again.
            return _advance_cable(pa, user, msisdn, given)
        return reply(msisdn, "Enter your smartcard / IUC number.")
    if st == "iuc":
        iuc = re.sub(r"\s", "", text)
        if len(iuc) < 6:
            return reply(msisdn, "Enter a valid smartcard / IUC number.")
        # A card that fails verification must not be re-tried from the payload on
        # the customer's next message.
        pa.payload.pop("iuc_given", None)
        prov = CABLE_NAMES[pa.payload["prov"]].lower()
        res = vtu_verify_customer(prov, iuc)
        if not res.get("success"):
            return reply(msisdn, "Couldn't validate that smartcard. Check the number and try again, or \"cancel\".")
        price = Decimal(pa.payload["price"])
        if _insufficient(user, price):
            _clear_actions(msisdn)
            return reply(msisdn, f"Insufficient balance ({_money(get_or_create_wallet(user).balance)}).")
        limit_msg = send_limit_error(user, price) or daily_limit_error(user, price, "bill")
        if limit_msg:
            _clear_actions(msisdn)
            return _limit_reply(msisdn, user, limit_msg)
        prov_name = CABLE_NAMES[pa.payload["prov"]]
        cust = res.get("customer_name", "")
        pa.payload.update({"iuc": iuc, "customer": cust})
        pa.payload["meta"] = {"iuc": iuc, "provider": pa.payload["prov"], "plan_code": pa.payload["plan_code"]}
        if not _arm_confirm(pa, user):
            return
        cust = cust or "—"
        return _send_confirm(
            pa, msisdn,
            f"📺 *Confirm cable*\n{prov_name} • {pa.payload['plan_name']}\n"
            f"Card {iuc} • {cust} • {_money(price)}",
            logo=provider_logo(prov_name))
    if st == "pin":
        if not _flow_pin_ok(pa, user, msisdn, text):
            return
        return _exec_cable(pa, user, msisdn)
    _clear_actions(msisdn)
    return send_menu(msisdn)


def _exec_cable(pa: PendingAction, user, msisdn: str) -> str:
    prov = CABLE_NAMES[pa.payload["prov"]].lower()
    prov_name = CABLE_NAMES[pa.payload["prov"]]
    iuc, plan_code, price = pa.payload["iuc"], pa.payload["plan_code"], Decimal(pa.payload["price"])
    return _run_vtu(
        pa, user, msisdn, price, f"Cable — {prov_name} {pa.payload['plan_name']}",
        lambda ref: vtu_purchase(prov, {"billersCode": iuc, "variation_code": plan_code}, reference=ref),
        lambda txn, res: ("Cable receipt", [
            ("Provider", prov_name), ("Package", pa.payload["plan_name"]), ("Smartcard", iuc),
            ("Amount", _money(price)), ("Reference", txn.reference),
            ("Date", timezone.now().strftime("%d %b %Y, %H:%M"))]),
    )


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
            and SystemSetting.get_bool("ai_enabled_global", False)
            and link.ai_enabled
            and convo.ai_enabled)


def _record_intent(msisdn: str, intent: dict) -> None:
    """Attach the parsed intent to the inbound row (for QA / the monitor)."""
    from .models import WebhookEvent

    row = WaMessageLog.objects.filter(msisdn=msisdn, direction=WaMessageLog.IN).order_by("-created").first()
    if row is not None:
        # Store the MASKED input (tokens, not identifiers): intent_json feeds the
        # ops console's intent list, and a re-hydrated copy would put customer
        # account numbers on a screen that only needs to show routing quality.
        logged = {"name": intent.get("name"),
                  "input": intent.get("masked_input", intent.get("input", {}))}
        row.intent_json = WebhookEvent.redact(logged)
        row.save(update_fields=["intent_json"])


def _network_id(network) -> str | None:
    return NET_BY_NAME.get(str(network).strip().lower()) if network else None


def dispatch_intent(user, msisdn: str, intent: dict) -> bool:
    """Map one LLM tool call to a deterministic flow. Returns False for
    clarify/unknown so the caller shows the menu. Money still requires the
    flow's confirm + PIN — the LLM only routes here."""
    from .flows import clean_narration

    name = intent.get("name")
    p = intent.get("input", {}) or {}
    # Cleaned here, at the boundary, so a model that returns a newline or 300
    # characters cannot put either into a payload, a bank statement or a
    # rendered receipt. Reset in the finally below: a narration is a fact about
    # ONE message, and one that outlived its dispatch would attach the last
    # customer's words to the next customer's payment.
    token = _ai_narration.set(clean_narration(p.get("narration")))
    try:
        return _dispatch_intent(user, msisdn, name, p)
    finally:
        _ai_narration.reset(token)


def _dispatch_intent(user, msisdn: str, name, p: dict) -> bool:

    if name == "check_balance":
        _do_balance(user, msisdn)
        return True
    if name == "add_money":
        _do_add_money(user, msisdn)
        return True
    if name == "transfer":
        amt, acct, bank = p.get("amount"), p.get("account_number"), p.get("bank_name")
        # No bank_name is no longer a reason to fall back to the guided form:
        # _begin_bank_transfer reads the bank off the account number's checksum
        # when the message didn't name one, and only returns False when even
        # that can't resolve it.
        if amt and acct:
            try:
                if _begin_bank_transfer(user, msisdn, Decimal(str(amt)), re.sub(r"\D", "", str(acct)), str(bank or "")):
                    return True
            except (InvalidOperation, TypeError):
                pass
        _start_transfer(user, msisdn)  # partial details -> guided flow
        return True
    if name == "buy_airtime":
        return _begin_airtime(user, msisdn, p.get("amount"), p.get("phone"),
                              p.get("network"), p.get("recipient_ref"))
    if name == "buy_data":
        _start_data(user, msisdn)
        return True
    if name == "pay_bill":
        cat = (p.get("category") or "").lower()
        # The model extracts biller/customer_id/variation/amount; this branch used
        # to throw all four away and open the flow at question one, which is how a
        # message naming the disco, the meter AND the amount still got "Which
        # disco?". Pass them on — the flows validate everything they are given.
        if "electric" in cat:
            _begin_electricity(user, msisdn, p.get("biller"), p.get("customer_id"),
                               p.get("variation"), p.get("amount"))
        elif "cable" in cat or "tv" in cat:
            _begin_cable(user, msisdn, p.get("biller"), p.get("customer_id"))
        else:
            _start_service_menu(user, msisdn, "bill")
        return True
    if name == "convert_currency":
        _start_convert(user, msisdn)
        return True
    if name == "transaction_history":
        _do_history(user, msisdn, p.get("count"),
                    amount=p.get("amount"), days_ago=p.get("days_ago"),
                    kind=p.get("kind"), recipient=p.get("recipient"),
                    status=p.get("status"), as_document=p.get("as_document"))
        return True
    if name == "report_problem":
        _do_report_problem(user, msisdn, amount=p.get("amount"), days_ago=p.get("days_ago"),
                           kind=p.get("kind"), recipient=p.get("recipient"),
                           reference=p.get("reference"), reason=p.get("reason"),
                           detail=p.get("detail"))
        return True
    return False  # clarify / unknown


#: Nigerian mobile prefixes by network, national form. Used only to fill in a
#: network the customer did not state — never to override one they did.
_NETWORK_PREFIXES = {
    "1": ("0803", "0806", "0703", "0706", "0813", "0816", "0810", "0814", "0903", "0906", "0913", "0916"),
    "2": ("0805", "0807", "0705", "0815", "0811", "0905", "0915"),
    "3": ("0802", "0808", "0708", "0812", "0701", "0902", "0901", "0904", "0907", "0912"),
    "4": ("0809", "0817", "0818", "0908", "0909"),
}
_PREFIX_TO_NETWORK = {p: net for net, prefixes in _NETWORK_PREFIXES.items() for p in prefixes}


def _network_from_prefix(phone) -> str | None:
    """The network a Nigerian mobile number belongs to, or None if unrecognised.

    Ported numbers make this a guess, not a fact — which is why it only ever
    pre-fills a confirm screen the customer still has to approve, and why a
    network they stated always wins.
    """
    digits = re.sub(r"\D", "", str(phone or ""))
    if digits.startswith("234"):
        digits = "0" + digits[3:]
    return _PREFIX_TO_NETWORK.get(digits[:4]) if len(digits) >= 10 else None


def _begin_airtime(user, msisdn: str, amount, phone, network, recipient_ref=None) -> bool:
    """LLM airtime: if amount + network + phone are all known, jump to confirm;
    otherwise start the guided flow."""
    if _blocked_from_spending(user, msisdn):
        return True
    # "recharge tobi 2k" names WHO, not what number. Falling through to the
    # default below would have read the missing number as "me" and topped up the
    # sender's own line — the wrong number, already paid for, and nothing on the
    # confirm card to reveal it was wrong because the card shows the number it
    # guessed. Zitch cannot read the phone's contacts, so the only honest move is
    # to ask.
    if recipient_ref and not phone:
        who = str(recipient_ref)[:40]
        payload = {"pin_attempts": 0, "recipient_ref": who}
        try:
            if amount is not None:
                payload["amount"] = str(Decimal(str(amount)))
        except (InvalidOperation, TypeError):
            pass
        netid = _network_id(network)
        if netid:
            payload["net"] = netid
        _new_flow(user, msisdn, "airtime", "phone", payload)
        reply(msisdn, f"I can't look up {who}'s number — Zitch can't read your contacts. "
                      "What number should I recharge?")
        return True
    # "2k airtime for me" carries neither a number nor a network. Falling back to
    # the guided flow for that made the AI look useless on the single most common
    # sentence customers actually send — so both are inferred rather than asked
    # for: no target means the customer's own line, and a Nigerian number's
    # prefix names its network. Neither guess moves money; the confirm screen
    # still shows what was inferred and still needs biometrics or the PIN.
    ph = _phone_from(str(phone), user) if phone else (user.phone or "").lstrip("+")
    netid = _network_id(network) or _network_from_prefix(ph)
    try:
        amt = Decimal(str(amount)) if amount is not None else None
    except (InvalidOperation, TypeError):
        amt = None
    if amt and amt >= 50 and netid and ph:
        if _insufficient(user, amt):
            reply(msisdn, f"Insufficient balance ({_money(get_or_create_wallet(user).balance)}).")
            return True
        net = NETWORK_NAMES[netid]
        pa = _new_flow(user, msisdn, "airtime", "pin",
                       {"pin_attempts": 0, "net": netid, "phone": ph, "amount": str(amt.quantize(Decimal("0.01"))),
                        "meta": {"phone": ph, "network": netid}})
        if not _arm_confirm(pa, user):   # failure is already explained in-chat
            return True
        _send_confirm(pa, msisdn, f"Confirm airtime\n{_money(amt)} {net} → {ph}")
        return True
    _start_airtime(user, msisdn)
    return True


# --------------------------------------------------------------------------- #
# currency conversion (FX) — quote -> PIN-within-TTL -> settle (Fincra rail)
# --------------------------------------------------------------------------- #
CONVERT_CCYS = ["NGN", "USD", "GBP", "CAD"]  # settle-able; CNY is quote-only (blocked)


def _start_convert(user, msisdn: str) -> None:
    if _blocked_from_spending(user, msisdn):
        return None
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
        if not _arm_confirm(pa, user):
            return
        secs = max(1, int((quote.expires_at - timezone.now()).total_seconds()))
        return _send_confirm(
            pa, msisdn,
            "Confirm conversion\n"
            f"Sell {quote.sell_amount:,.2f} {quote.from_currency} → "
            f"Receive {quote.receive_amount:,.2f} {quote.to_currency}\n"
            f"Rate {quote.rate:.4f} • expires in {secs}s")
    if st == "pin":
        if not _flow_pin_ok(pa, user, msisdn, text):
            return
        return _exec_convert(pa, user, msisdn)
    _clear_actions(msisdn)
    return send_menu(msisdn)


def _exec_convert(pa: PendingAction, user, msisdn: str) -> str:
    try:
        quote = execute_fx(user, pa.payload["quote_ref"],
                           idempotency_key=f"wa-fx-{pa.id}", channel="whatsapp")
    except FxError as exc:
        _clear_actions(msisdn)
        reply(msisdn, exc.message)
        # Tagged, like every other executor. Untagged fell through to the neutral
        # "Done" heading, so a refused conversion closed the Flow on the same word
        # a successful one did — the exact tell-them-apart-at-a-glance failure the
        # status heading was added to end, still live on this one path.
        return Outcome(exc.message, OUTCOME_FAILED)
    _clear_actions(msisdn)
    new_bal = currency_balance(user, quote.to_currency)
    line = (f"✅ Converted. -{quote.sell_amount:,.2f} {quote.from_currency} / "
            f"+{quote.receive_amount:,.2f} {quote.to_currency}. "
            f"New {quote.to_currency} balance: {new_bal:,.2f}.")
    reply(msisdn, line)
    return Outcome(line, OUTCOME_SUCCESS)


# --------------------------------------------------------------------------- #
# Secure-Flow execution dispatch — the Flows endpoint (whatsapp.flows) calls this
# AFTER verifying the PIN, so a Flow-confirmed action runs the exact same money
# path as the chat PIN path.
# --------------------------------------------------------------------------- #
def authorise_flow_execution(pa: PendingAction, user) -> str:
    """The PIN just passed. Get the money OFF Meta's clock.

    A Flows data-exchange must be answered within 10 seconds or the customer is
    shown "Couldn't load content. Try again later." — and executing a transfer
    here means a name enquiry, a payout to the bank rail, a rendered receipt, a
    media upload and two Graph sends, all in sequence. That is routinely more
    than ten seconds, so the customer was shown a failure for a payment that had
    in fact gone through, with the money already gone.

    So the endpoint answers as soon as the PIN is verified, and the payment runs
    where every chat-confirmed payment has always run: the durable queue, with
    its lease, retries and dead-letter. The outcome arrives in the chat, which is
    where the receipt was always going to land.

    Inline mode (dev, tests, and any host with no background execution at all)
    keeps running it in-process, exactly as the webhook does — see
    WHATSAPP_PROCESS_INLINE.
    """
    if getattr(settings, "WHATSAPP_PROCESS_INLINE", False):
        return run_flow_execution(pa, user)

    from .jobs import drain_in_background, enqueue_flow_execution

    # Held for the worker: `executing` says authorised-not-yet-done, and the
    # deadline is pushed out because the short PIN window governs how long the
    # customer has to CONFIRM, not how long the rail has to settle.
    pa.state = EXECUTING_STATE
    pa.expires_at = timezone.now() + EXECUTION_TTL
    pa.save(update_fields=["state", "expires_at"])
    enqueue_flow_execution(pa)
    # Same safety net the webhook uses for a worker that isn't running, and off
    # the request thread so Meta's answer is not held up by it. Not under the
    # test runner, where the suite drives the queue itself and a second thread
    # would only race it.
    if not getattr(settings, "TESTING", False):
        drain_in_background()
    # Give the rail a moment to answer before closing the Flow. Without this,
    # "Successful" was unreachable in production: every money Flow closed on
    # "Pending" because the endpoint replied the instant the job was queued, so
    # the customer's last word from the Flow was always about a payment that had
    # not been attempted yet.
    settled = _await_settlement(pa.id, user, pa.action_type)
    if settled is not None:
        return settled
    # Still working. PENDING, emphatically not success: the rail has not answered
    # yet, and a tick here would read as "done" for a payment that may still
    # fail — the one thing a banking channel must never say. The receipt in the
    # chat remains the authoritative outcome.
    return Outcome("Confirmed — I'm completing your payment now. The receipt will "
                   "arrive in this chat in a few seconds.", OUTCOME_PENDING)


#: What the settled screen calls each action. "Sent" is true of a transfer and
#: false of everything else — a meter token or a data bundle is bought, not sent —
#: and this screen is the one place the customer is told the money moved, so it
#: should not describe their electricity payment as something posted to someone.
_SETTLED_VERB = {
    "transfer": "Sent",
    "airtime": "Airtime purchased",
    "data": "Data bundle purchased",
    "electricity": "Electricity paid",
    "cable": "Subscription paid",
    "convert": "Converted",
}


def _await_settlement(action_id: int, user, action_type: str = ""):
    """Poll the ledger for this action's outcome, briefly. Returns a tagged
    Outcome once the row is terminal, or None if it is still processing.

    Bounded by WHATSAPP_FLOW_SETTLE_WAIT (default 3s, hard-capped at 6). Meta
    allows the data-exchange about ten seconds before showing the customer
    "Couldn't load content. Try again later." — the failure that moving
    execution off this thread was introduced to fix — so this deliberately stays
    far from that ceiling. It also occupies a gunicorn thread, and the web dyno
    serves /healthz from the same pool of eight.

    Waiting changes NOTHING about the payment: it is queued and executing either
    way, and the chat receipt is sent by the worker regardless. All this decides
    is which heading the closing screen can honestly show.
    """
    from wallet.models import Transaction

    budget = float(getattr(settings, "WHATSAPP_FLOW_SETTLE_WAIT", 0) or 0)
    if budget <= 0:
        return None
    # The key every executor stamps its ledger row with — except FX, which has
    # always used its own prefix. Polling `wa-<id>` for a conversion therefore
    # matched nothing and timed out into "Pending" every single time, however
    # fast the rail answered.
    key = f"wa-fx-{action_id}" if action_type == "convert" else f"wa-{action_id}"
    deadline = time.monotonic() + budget
    while True:
        # .only() because this runs on the request thread and the ledger row is
        # wide; the status and the reference are all that is read.
        txn = (Transaction.objects.filter(user=user, idempotency_key=key)
               .only("transaction_status", "reference").first())
        if txn is not None and txn.transaction_status != Transaction.PENDING:
            if txn.transaction_status == Transaction.SUCCESS:
                verb = _SETTLED_VERB.get(action_type, "Done")
                return Outcome(f"{verb} — the receipt is in your chat.", OUTCOME_SUCCESS)
            # A failure is worth waiting for too: it is the one outcome the
            # customer should see BEFORE the screen closes, not only in a chat
            # message they may scroll past.
            return Outcome("That didn't go through. You were not charged — "
                           "see the chat for details.", OUTCOME_FAILED)
        if time.monotonic() >= deadline:
            return None
        time.sleep(_SETTLE_POLL)


@db_transaction.atomic
def run_flow_execution(pa: PendingAction, user) -> str:
    # Token resolution and PIN verification happen before this call. Re-read and
    # lock both records here so a concurrent cancel, replay, expiry or account
    # freeze cannot race past the final execution boundary.
    live = PendingAction.objects.select_for_update().filter(
        pk=pa.pk,
        user_id=user.pk,
        msisdn=pa.msisdn,
        state__in=(FLOW_PIN_STATE, "pin", EXECUTING_STATE),
    ).first()
    if live is None:
        return Outcome("This request expired or was cancelled. Start again in the chat.",
                       OUTCOME_FAILED)
    # An action already authorised is past the point where expiry means anything:
    # the PIN was accepted inside the window, and the clock that ran out was the
    # one measuring how long the customer had to confirm. Dropping it here would
    # discard a payment the customer was told was on its way.
    if live.expired and live.state != EXECUTING_STATE:
        live.delete()
        return Outcome("This request expired or was cancelled. Start again in the chat.",
                       OUTCOME_FAILED)

    live_user = get_user_model().objects.select_for_update().filter(pk=user.pk).first()
    if live_user is None or not live_user.is_active:
        _clear_actions(live.msisdn)
        return Outcome("Your Zitch account is currently suspended. Please contact support.",
                       OUTCOME_FAILED)

    pa = live
    user = live_user
    # Getting here means the PIN or a verified biometric just passed, so it
    # starts the re-auth window: someone who just authorised a payment should
    # not be challenged again to read their own balance.
    _mark_verified(pa.msisdn)
    executors = {
        "transfer": _exec_transfer, "airtime": _exec_airtime, "data": _exec_data,
        "electricity": _exec_electricity, "cable": _exec_cable, "convert": _exec_convert,
        "unlock": _exec_unlock,
    }
    fn = executors.get(pa.action_type)
    if fn is None:
        _clear_actions(pa.msisdn)
        return Outcome("Sorry, this action can't be completed here. Please try again in the chat.",
                       OUTCOME_FAILED)
    return fn(pa, user, pa.msisdn) or "Done ✅"
