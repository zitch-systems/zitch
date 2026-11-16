"""Release 2 WhatsApp Flow screens: one-page signup details, the SMS code and the
app password on one page, the menu as a form, and paying a bill in the form.

Every entry point here is behind a WHATSAPP_FLOW flag that defaults OFF (see
flows.details_screen_live() and its siblings). The Flow JSON is published to
Meta by an operator AFTER the code deploys, and answering with a screen Meta has
not published is the "Couldn't load content. Try again later." failure - so
until each flag is flipped, nothing in this module is reachable and the channel
behaves exactly as it did before.

Nothing here moves money on its own. The bill form ends on the same PIN_CHAIN
confirm as every other form, and the payment is executed by the same
`_exec_electricity` the chat path uses, after the same checks
(`electricity_verify_meter`, `electricity_check_amount`). The PIN, the password
and every code are only ever typed into the encrypted Flow.
"""
import hashlib
import hmac
import logging
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import flows as F

log = logging.getLogger("whatsapp")

#: Marks a flow_token that addresses a customer's chat LINK (the menu form),
#: rather than a pending action, an onboarding or a sign-in.
MENU_PREFIX = "mn"
_MENU_SESSION_TTL = 3600

#: The roots a menu-opened form is answered on instead, as its chained twin.
_TWINS = {F.TRANSFER_FORM: F.TRANSFER_CHAIN, F.VTU_SCREEN: F.VTU_CHAIN,
          F.BILL_SCREEN: F.BILL_CHAIN, F.SIGNUP_DETAILS: F.DETAILS_CHAIN}


def on_twin(standing: str, response: dict) -> dict:
    """Keep a re-render on the twin the device is standing on.

    The form handlers answer their ROOT id on an error re-render. From a chained
    twin that is a navigation, not a re-render - and Meta refuses an undeclared
    one on the device - so the twin's own id is answered instead.
    """
    if isinstance(response, dict) and standing and _TWINS.get(response.get("screen")) == standing:
        response["screen"] = standing
    return response


def _warn(error: str) -> str:
    return f"⚠️ {error}" if error else ""


# --------------------------------------------------------------------------- #
# 1. sign-up details on one page (DETAILS_SCREEN)
# --------------------------------------------------------------------------- #
def submit_signup_details_page(ob, data: dict) -> dict:
    """First name, surname, phone and email in ONE submit.

    The same validators as the two-page path (SIGNUP_SCREEN then SIGNUP_PHONE),
    applied in the same order, so the one-page and two-page forms cannot
    disagree about what a valid signup is. Then on exactly as the phone page
    goes on: the SMS proof for a number other than this WhatsApp number, else the
    password / PIN.
    """
    from .router import _local_phone, _onboard_to, send_onboarding_phone_code

    here = F._flow_screen(ob, F.SIGNUP_DETAILS)
    error, fields = F.signup_details_error(data)
    digits = ""
    if not error:
        error, digits = F.signup_phone_error(data)
    if error:
        return F._signup_screen(error=error, screen=here)
    ob.payload.update(fields)
    ob.payload["phone"] = digits
    if digits != _local_phone(ob.msisdn):
        # Not proved by the WhatsApp session: it must complete the SMS round
        # trip, or anyone could reserve somebody else's number as an account.
        if not send_onboarding_phone_code(ob):
            return F._signup_screen(
                error="We couldn't send an SMS to that number. Try again, or use this WhatsApp number.",
                screen=here)
        if F.verify_password_live():
            return open_verify_password(ob)
        _onboard_to(ob, F.FLOW_PHONE_CODE_STATE)
        return F._signup_phone_code_screen(ob)
    # _signup_to_pin persists only the step on its password branch; the
    # details collected on THIS page must not be lost with it.
    ob.save(update_fields=["payload"])
    return F._signup_to_pin(ob)


# --------------------------------------------------------------------------- #
# 2. verify phone + password on one page (VERIFY_PASSWORD_SCREEN)
# --------------------------------------------------------------------------- #
_MAX_RESENDS = 3
_RESEND_COOLDOWN = timedelta(seconds=30)


def _masked_phone(ob) -> str:
    return f"•••••{(ob.payload.get('phone') or '')[-4:]}"


def verify_password_screen(ob, error: str = "", notice: str = "") -> dict:
    summary = notice or f"We sent a 6-digit code by SMS to {_masked_phone(ob)}."
    return {"screen": F.VERIFY_PASSWORD, "data": {"summary": summary, "error": _warn(error)}}


def open_verify_password(ob) -> dict:
    """Park the signup on VERIFY_PASSWORD. The caller has already sent the code."""
    from .router import _onboard_to

    ob.payload["verify_resent_at"] = timezone.now().isoformat()
    ob.payload["verify_resends"] = 0
    ob.payload["flow_screen"] = F.VERIFY_PASSWORD
    _onboard_to(ob, F.FLOW_VERIFY_PW_STATE)
    return verify_password_screen(ob)


def _resend_code(ob) -> dict:
    """"Didn't get it? Resend code" - a fresh code to the same number, bounded
    both in number and in rate so the link cannot be used to spam a phone."""
    from .router import _onboard_to, send_onboarding_phone_code

    count = int(ob.payload.get("verify_resends") or 0)
    if count >= _MAX_RESENDS:
        return verify_password_screen(
            ob, error="You've asked for 3 new codes. If it still hasn't arrived, close this "
                      "form and start Create account again.")
    try:
        last = timezone.datetime.fromisoformat(ob.payload.get("verify_resent_at") or "")
    except (TypeError, ValueError):
        last = None
    if last is not None and timezone.now() - last < _RESEND_COOLDOWN:
        return verify_password_screen(ob, error="Please wait 30 seconds before asking for another code.")
    if not send_onboarding_phone_code(ob):
        return verify_password_screen(ob, error="We couldn't send a new code just now. Try again shortly.")
    ob.payload["verify_resends"] = count + 1
    ob.payload["verify_resent_at"] = timezone.now().isoformat()
    _onboard_to(ob, ob.step)   # persists the new code hash and the counters
    return verify_password_screen(ob, notice=f"We sent a new 6-digit code by SMS to {_masked_phone(ob)}.")


def submit_verify_password(ob, data: dict) -> dict:
    """The SMS code and the app password, typed on one encrypted page.

    The password is checked FIRST, with the same rule SIGNUP_PASSWORD applies:
    refusing it costs no code attempt, and a correct code is only consumed when
    everything else on the page is acceptable - so there is never a state where
    the phone is proved but the page still has to be submitted again.

    Same-screen re-renders here are fine: the code box is visible, and the
    password boxes keep what was typed, exactly as SIGNUP_PASSWORD does.
    """
    from django.contrib.auth.hashers import make_password

    from .router import _clear_onboarding, check_onboarding_phone_code

    if data.get("resend"):
        return _resend_code(ob)
    rejection = F.signup_password_error(ob, data)
    if rejection:
        return verify_password_screen(ob, error=rejection)
    if not ob.payload.get("phone_verified_flow"):
        status, message = check_onboarding_phone_code(ob, str(data.get("phone_code", "")))
        if status == "retry":
            return verify_password_screen(ob, error=message)
        if status != "ok":
            _clear_onboarding(ob.msisdn)
            return F._success_screen(
                "We couldn't verify that phone number. Start Create account again for a new code.",
                status="failed")
    # Only the hash is ever written - see flows._submit_signup_password.
    ob.payload["flow_pw_hash"] = make_password(str(data.get("password", "")))
    from .release4 import signup_after_password
    following = signup_after_password(ob)
    if following is not None:
        return following
    ob.step = F.FLOW_PIN_STATE
    ob.payload["flow_screen"] = F.PIN_CHAIN
    ob.save(update_fields=["payload", "step"])
    return F._pin_screen(F._ob_summary(ob), screen=F.PIN_CHAIN)


# --------------------------------------------------------------------------- #
# 4. pay a bill in the secure form (BILL_FORM) - electricity
# --------------------------------------------------------------------------- #
BILL_DETAILS_STEP = "details"
BILL_AMOUNT_STEP = "amount"


def _disco_options() -> list:
    """The same disco list the chat's "Which disco?" prompt shows."""
    # router_tail runs inside router's namespace; it is never imported on its own.
    from .router import _DISCO_SHORT, DISCO_NAMES

    # As designed: "Ikeja Electric (IKEDC)".
    return [{"id": k, "title": f"{v} Electric" + (f" ({_DISCO_SHORT[k]})" if _DISCO_SHORT.get(k) else "")}
            for k, v in DISCO_NAMES.items()]


def bill_screen(error: str = "", screen: str = "") -> dict:
    return {"screen": screen or F.BILL_SCREEN,
            "data": {"discos": _disco_options(), "error": _warn(error)}}


def bill_amount_screen(pa, error: str = "") -> dict:
    """"How much?" under the verified meter owner and address, as designed."""
    from .router import _flow_balance_line

    p = pa.payload
    cust = (p.get("customer") or "").strip()
    data = {"verified": f"✓ Meter verified · {cust}" if cust else "✓ Meter verified",
            "address": (p.get("customer_address") or "").strip()
                       or "Address not provided by the disco",
            "balance": _flow_balance_line(pa) or "Enter the amount in naira",
            "error": _warn(error)}
    if not F.design_v5_live():
        # The Flow published before Release 5 still declares the details block.
        from .router import DISCO_NAMES, _detail_lines

        data["details"] = _detail_lines([
            ("Disco", DISCO_NAMES.get(p.get("disco", ""), "")),
            ("Meter", f"{p.get('meter', '')} ({p.get('meter_type', '')})")])
    return {"screen": F.BILL_AMOUNT, "data": data}


def bill_screen_for(pa) -> dict:
    """INIT / BACK: re-render whichever page this session is on."""
    if pa.payload.get("bill_step") == BILL_AMOUNT_STEP:
        return bill_amount_screen(pa)
    return bill_screen(screen=F._flow_screen(pa, F.BILL_SCREEN))


def submit_bill(pa, data: dict) -> dict:
    if pa.payload.get("bill_step") == BILL_AMOUNT_STEP:
        return _submit_bill_amount(pa, data)
    return _submit_bill_details(pa, data)


def _submit_bill_details(pa, data: dict) -> dict:
    """Disco, meter type and meter number - then the SAME provider verification
    the chat ladder runs, before an amount is even asked for."""
    from .router import _disco_id, _meter_type, _touch, electricity_verify_meter

    here = F._flow_screen(pa, F.BILL_SCREEN)
    disco = _disco_id(str(data.get("disco", "")).strip())
    if disco is None:
        return bill_screen("Pick your disco from the list.", here)
    meter_type = _meter_type(data.get("meter_type"))
    if meter_type is None:
        return bill_screen("Choose prepaid or postpaid.", here)
    meter = re.sub(r"\s", "", str(data.get("meter", "") or ""))[:20]
    if len(meter) < 6:
        return bill_screen("Enter a valid meter number.", here)
    pa.payload.update({"disco": disco, "meter_type": meter_type, "meter": meter,
                       "meter_verified": False})
    if not electricity_verify_meter(pa.payload):
        _touch(pa, payload=pa.payload)
        return bill_screen("Couldn't validate that meter. Check the number and try again.", here)
    pa.payload["bill_step"] = BILL_AMOUNT_STEP
    _touch(pa, payload=pa.payload)
    return bill_amount_screen(pa)


def _submit_bill_amount(pa, data: dict) -> dict:
    """The amount, through the chat path's own checks, then straight into the
    PIN page of the SAME session - the payment the chat would have armed."""
    from .router import _clear_actions, _flow_fields, _touch, electricity_check_amount

    user = pa.user
    pa.payload["amount"] = str(data.get("amount", "") or "").strip()
    pa.payload["narration"] = F.clean_narration(data.get("narration"))
    problem, is_limit = electricity_check_amount(pa, user)
    if problem and is_limit:
        _clear_actions(pa.msisdn)
        return F._success_screen(problem, status="failed")
    if problem:
        _touch(pa, payload=pa.payload)
        return bill_amount_screen(pa, error=problem)
    if not user.transaction_pin:
        # The same refusal every form makes: a PIN pad nobody can satisfy is
        # worse than a clear "set one first".
        _clear_actions(pa.msisdn)
        return F._success_screen(
            "You haven't set a transaction PIN yet - it's what authorises payments here "
            "and in the Zitch app. Close this and reply \"set pin\" in the chat, then try "
            "again.", status="failed")
    pa.payload["pin_attempts"] = 0
    fields = _flow_fields(pa)
    pa.payload["flow_fields"] = fields
    pa.payload["flow_screen"] = F.PIN_CHAIN   # routed into, so never the root
    _touch(pa, state=F.FLOW_PIN_STATE, payload=pa.payload)
    return F._pin_screen(fields, screen=F.PIN_CHAIN)


def start_bill_form(user, msisdn: str) -> bool:
    """Menu 4 with BILL_FORM on: the secure form, with the chat path beside it.

    True when this handled the message (the form went out, or the account is
    blocked from paying bills and has been told). False sends the caller down
    the unchanged chat sub-menu.
    """
    from .router import (SERVICE_MENUS, _blocked_from_spending, _clear_actions, _new_flow,
                         flows_live, reply_list, send_flow, sign_flow_token)

    if F.bill_cable_form_live():
        # Release 4: "Which bill?" first, with cable TV in the form too.
        from .release4 import start_bill_type_form

        if start_bill_type_form(user, msisdn):
            return True
    if not (F.bill_form_live() and flows_live()):
        return False
    if _blocked_from_spending(user, msisdn, biller=True):
        return True
    pa = _new_flow(user, msisdn, "electricity", F.FLOW_BILL_STATE,
                   {"bill_step": BILL_DETAILS_STEP, "pin_attempts": 0,
                    "flow_screen": F.BILL_SCREEN})
    first = bill_screen()
    res = send_flow(msisdn, sign_flow_token(pa), header="Pay a bill",
                    body="Pay your electricity bill privately - we verify the meter before "
                         "anything moves, and your PIN never appears in this chat.",
                    screen=F.BILL_SCREEN, screen_data=first["data"], cta="Pay a bill")
    if not res.get("success"):
        log.warning("wa_bill_form_send_failed pa=%s detail=%r", pa.id, res.get("error_detail", ""))
        _clear_actions(msisdn)
        return False
    _, rows = SERVICE_MENUS["bill"]
    reply_list(msisdn, "Or pay here in the chat - which bill?", rows, button_label="Choose")
    return True


def advance_bill_chat(pa, user, msisdn: str, text: str) -> None:
    """A chat message while the bill form is open. The chat path stays fully
    usable beside the form: a number, the bill's name or a disco's name carries
    on in the chat exactly as the sub-menu would."""
    from .router import (SERVICE_MENUS, _begin_electricity, _clear_actions, _disco_id,
                         _is_new_command, _start_cable, _start_electricity, handle_inbound,
                         reply, reply_list)

    choice = text.strip().lower()
    if choice in ("1", "electricity", "light", "nepa", "power"):
        return _start_electricity(user, msisdn)
    if choice in ("2", "cable", "tv", "dstv", "gotv", "startimes"):
        return _start_cable(user, msisdn)
    if _disco_id(text):
        # "IKEDC" - the disco named outright, which the chat ladder accepts too.
        return _begin_electricity(user, msisdn, text, None, None, None)
    if _is_new_command(text):
        _clear_actions(msisdn)
        reply(msisdn, "Okay - leaving that bill.")
        return handle_inbound(msisdn, text)
    _, rows = SERVICE_MENUS["bill"]
    return reply_list(msisdn, "Use the secure *Pay a bill* form above, or reply 1 for "
                              "Electricity or 2 for Cable TV.", rows, button_label="Choose")


# --------------------------------------------------------------------------- #
# 6. the menu as a form (MENU_FORM)
# --------------------------------------------------------------------------- #
_MENU_TOKEN = re.compile(MENU_PREFIX + r"([1-9][0-9]{0,18})x([A-Za-z0-9_-]{12})\.([A-Za-z0-9_-]{22})")


def _menu_sig(link_id, nonce: str, msisdn: str) -> str:
    return F._sig(f"{MENU_PREFIX}{link_id}:{nonce}:{msisdn}")


def sign_menu_token(link) -> str:
    """A fresh, signed handle for one menu card, bound to the link and its
    number. The nonce keeps two cards' sessions apart."""
    nonce = secrets.token_urlsafe(12)[:12]
    return f"{MENU_PREFIX}{link.pk}x{nonce}.{_menu_sig(link.pk, nonce, link.wa_msisdn)}"


def resolve_menu_token(token: str):
    """The ACTIVE WhatsAppLink a menu token was signed for, or None."""
    from .models import WhatsAppLink

    if not isinstance(token, str) or len(token) > 80:
        return None
    match = _MENU_TOKEN.fullmatch(token.strip())
    if match is None or int(match[1]) >= 2**63:
        return None
    link = (WhatsAppLink.objects.select_related("user")
            .filter(pk=int(match[1]), status=WhatsAppLink.ACTIVE).first())
    if link is None:
        return None
    if not hmac.compare_digest(match[3], _menu_sig(link.pk, match[2], link.wa_msisdn)):
        return None
    return link


def _session_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def _cache_key(session: str) -> str:
    return "wa_menu_session:" + session


#: The approved menu's second line under each row (Release 5).
MENU_DESCRIPTIONS = {
    "balance": "Your Zitch balance",
    "send money": "Opens the send form",
    "3": "Opens the airtime and data form",
    "bill": "Electricity and cable TV",
    "exam": "WAEC, NECO, JAMB",
    "add money": "Your account number",
    "account details": "Name, account number and email",
    "verify": "Upgrade your tier",
    "history": "Your recent payments",
    "scan": "Pay with a QR code",
    "saved people": "People you send to often",
    "convert": "Between your wallets",
    "savings": "Your savings plans",
    "loan": "Balance and repayment",
}

#: Menu items answered inside the form rather than in the chat (Release 5):
#: the ones that read only our own records, so they fit Meta's ~10 seconds.
IN_FORM = {"balance": ("💰 Your balance", "_do_balance"),
           "account details": ("🧾 My account details", "_do_account_details")}


def menu_items() -> list:
    """The chat menu's own items, in its order and with its numbers as ids."""
    from utility.providers import partnership_new_business_allowed

    from .router import _menu_items

    live = partnership_new_business_allowed()
    described = F.design_v5_live()
    items = []
    for number, key, emoji, label in _menu_items():
        if not live and key == "send money":
            label = "Transfers unavailable"
        if not live and key == "scan":
            label = "QR payments unavailable"
        item = {"id": number, "title": f"{emoji} {label}"[:30]}
        if described:
            item["description"] = ("Unavailable right now" if label.endswith("unavailable")
                                   else MENU_DESCRIPTIONS.get(key, ""))[:300]
        items.append(item)
    return items


def _answer_in_form(user, msisdn: str, key: str):
    """Balance or account details shown on the form's result page, or None to
    answer in the chat instead. The chat's own handler builds the text, so the
    two can't disagree, and the idle re-check is the chat's own: after the idle
    window the chat asks for the PIN first, exactly as if it had been typed."""
    from . import router
    from .models import ConversationState

    if key not in IN_FORM or not F.design_v5_live():
        return None
    if router._needs_reauth(ConversationState.for_msisdn(msisdn)):  # noqa: SLF001
        return None
    heading, handler = IN_FORM[key]
    try:
        texts = router.capture_replies(getattr(router, handler), user, msisdn)
    except Exception:  # noqa: BLE001 - fall back to the chat, which retries
        log.exception("wa_menu_in_form_answer_failed")
        return None
    text = "\n\n".join(t for t in texts if t).strip()
    if not text:
        return None
    # A form shows text as written: drop the chat's *bold* and _italic_ marks
    # and the heading the result page already carries.
    plain = re.sub(r"[*_]", "", text)
    plain = re.sub(r"^\W*(My account details|Your balances?:?)\s*", "", plain).strip()
    screen = F._result_screen(plain)
    screen["data"]["status"] = heading
    return screen


def menu_screen(error: str = "") -> dict:
    return {"screen": F.MENU_SCREEN, "data": {"items": menu_items(), "error": _warn(error)}}


def send_menu_form(msisdn: str) -> bool:
    """The "☰ Menu" card. True when it was sent; the caller sends the numbered
    menu text after it either way, so typing a number keeps working."""
    from .router import active_link_for, flows_live, send_flow

    if not (F.menu_form_live() and flows_live()):
        return False
    link = active_link_for(msisdn)
    if link is None:
        return False
    first = menu_screen()
    res = send_flow(msisdn, sign_menu_token(link), header="",
                    body="What would you like to do?", screen=F.MENU_SCREEN,
                    screen_data=first["data"], cta="☰ Menu")
    if not res.get("success"):
        log.warning("wa_menu_form_send_failed detail=%r", res.get("error_detail", ""))
        return False
    return True


def _session_action(link, session: str):
    """The pending action this menu session opened, if it still exists."""
    from .models import PendingAction

    rows = (PendingAction.objects.filter(msisdn=link.wa_msisdn, user_id=link.user_id)
            .order_by("-created")[:10])
    return next((pa for pa in rows if (pa.payload or {}).get("menu_session") == session), None)


def handle_menu_exchange(token: str, action: str, data: dict):
    """(response, token) for an exchange on a menu card's token.

    A MENU submit (it carries `item`) is answered here. Any later exchange of
    the same session - the form it opened, the PIN, Done - comes back with the
    token of the pending action that form created, and the caller handles it
    exactly as if that form had been opened from the chat.
    """
    link = resolve_menu_token(token)
    if link is None:
        return F._result_screen("This menu has expired. Reply \"menu\" in the chat for a "
                                "fresh one.", status="failed"), token
    session = _session_key(token)
    if action == "data_exchange" and "item" in data and not data.get("close"):
        return submit_menu(link, token, session, data), token
    pa = _session_action(link, session)
    if pa is not None:
        return None, F.sign_flow_token(pa)
    try:
        from django.core.cache import cache

        mapped = cache.get(_cache_key(session))
    except Exception:  # noqa: BLE001 - only a status re-check depends on it
        mapped = None
    if isinstance(mapped, str) and mapped:
        return None, mapped
    if data.get("close"):
        return F._close_flow(token), token
    if action == "data_exchange":
        return F._result_screen("This form has ended. Reply \"menu\" in the chat to start "
                                "again.", status="failed"), token
    return menu_screen(), token


def _bind(pa, session: str) -> None:
    """Remember which menu session opened `pa`, durably on the row and - for the
    Done re-check after the row is gone - in the cache."""
    try:
        from django.core.cache import cache

        cache.set(_cache_key(session), F.sign_flow_token(pa), _MENU_SESSION_TTL)
    except Exception:  # noqa: BLE001
        log.warning("wa_menu_session_not_cached pa=%s", pa.pk, exc_info=True)


def submit_menu(link, token: str, session: str, data: dict) -> dict:
    """A tapped menu item. Items with a form open it on this same session;
    everything else closes the Flow and is answered in the chat exactly as if the
    customer had typed it - through the router's own dispatch, re-auth gate and
    all."""
    from .router import menu_keyword

    user, msisdn = link.user, link.wa_msisdn
    if not user.is_active:
        return F._result_screen("Your Zitch account is currently suspended. Please contact "
                                "support.", status="failed")
    item = str(data.get("item", "") or "").strip()
    if item not in {entry["id"] for entry in menu_items()}:
        return menu_screen(error="Pick one of the options.")
    key = menu_keyword(item)
    opener = {"send money": _open_transfer, "3": _open_vtu}.get(key)
    if key == "bill" and (F.bill_form_live() or F.bill_cable_form_live()):
        opener = _open_bill
    response = opener(user, msisdn, token, session) if opener else None
    if response is not None:
        return response
    answered = _answer_in_form(user, msisdn, key)
    if answered is not None:
        return answered
    _dispatch_in_chat(msisdn, key, session)
    return F._close_flow(token)


def _open_transfer(user, msisdn, token, session):
    from .router import _bank_items, _blocked_from_spending, _new_flow

    if _blocked_from_spending(user, msisdn):
        return F._close_flow(token)
    if not _bank_items():
        # An empty bank Dropdown does not render; the chat ladder still works.
        return None
    if F.transfer_form_v2_live():
        # Release 3: the redesigned form, on its own chained twin.
        from .release3 import open_send_money_chain

        return open_send_money_chain(user, msisdn, session)
    pa = _new_flow(user, msisdn, "transfer", F.FLOW_FORM_STATE,
                   {"pin_attempts": 0, "menu_session": session, "flow_screen": F.TRANSFER_CHAIN})
    _bind(pa, session)
    response = F._transfer_form_screen()
    response["screen"] = F.TRANSFER_CHAIN
    return response


def _open_vtu(user, msisdn, token, session):
    from .router import _blocked_from_spending, _new_flow

    if _blocked_from_spending(user, msisdn, biller=True):
        return F._close_flow(token)
    if F.vtu_one_screen_live():
        # Release 3: airtime or data on one page, on its own chained twin.
        from .release3 import open_vtu_form_chain

        return open_vtu_form_chain(user, msisdn, session)
    pa = _new_flow(user, msisdn, "airtime", F.FLOW_VTU_STATE,
                   {"vtu_step": "kind", "pin_attempts": 0, "menu_session": session,
                    "flow_screen": F.VTU_CHAIN})
    _bind(pa, session)
    return {"screen": F.VTU_CHAIN, "data": {"error": ""}}


def _open_bill(user, msisdn, token, session):
    from .router import _blocked_from_spending, _new_flow

    if _blocked_from_spending(user, msisdn, biller=True):
        return F._close_flow(token)
    if F.bill_cable_form_live():
        # Release 4: "Which bill?" first, on its own chained twin.
        from .release4 import open_bill_type_chain

        return open_bill_type_chain(user, msisdn, session)
    pa = _new_flow(user, msisdn, "electricity", F.FLOW_BILL_STATE,
                   {"bill_step": BILL_DETAILS_STEP, "pin_attempts": 0, "menu_session": session,
                    "flow_screen": F.BILL_CHAIN})
    _bind(pa, session)
    return bill_screen(screen=F.BILL_CHAIN)


def _dispatch_in_chat(msisdn: str, key: str, session: str) -> None:
    """Answer a form-less menu item in the chat, as if it had been typed.

    Off Meta's clock in production: some items (history, account details) call
    the bank and render documents, which a Flow exchange's ~10 seconds cannot
    afford. It rides the inbound queue - per-sender ordering, retries and the
    dead-letter included - exactly like a typed message. Inline mode (dev and
    tests) runs it in-process, as the webhook does.
    """
    if (getattr(settings, "WHATSAPP_PROCESS_INLINE", False)
            and getattr(settings, "WHATSAPP_FLOW_INLINE", True)):
        from .router import handle_inbound

        try:
            handle_inbound(msisdn, key)
        except Exception:  # noqa: BLE001 - the Flow must still close cleanly
            log.exception("wa_menu_inline_dispatch_failed")
        return
    from django.db import transaction as db_transaction

    from .jobs import drain_in_background, enqueue_inbound

    enqueue_inbound(message_id=f"flowmenu-{session[:16]}-{secrets.token_hex(6)}",
                    msisdn=msisdn, logged_text=f"[flow menu] {key}",
                    payload={"is_text": True, "body": key})
    if not getattr(settings, "TESTING", False):
        db_transaction.on_commit(drain_in_background)
