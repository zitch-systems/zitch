"""Release 3 WhatsApp Flow screens: airtime or data on one page, and the
redesigned send-money form.

Same contract as release2.py. Every entry point is behind a WHATSAPP_FLOW flag
that defaults OFF (flows.vtu_one_screen_live(), flows.transfer_form_v2_live()),
because the Flow JSON is published by an operator AFTER the code deploys and
answering with a screen Meta has not published is "Couldn't load content". With
both flags off nothing here is reachable.

Nothing here moves money or re-implements a check. Both forms end on the same
PIN_CHAIN confirm as every other form, armed by the same code:

* VTU_FORM / VTU_PLAN go through flows.arm_vtu - the phone, minimum, plan,
  limits, balance and PIN checks the four-page ladder makes - and the purchase
  is executed by the existing airtime/data executor once the PIN is right;
* SEND_MONEY goes through flows._submit_transfer_form - the bank detection and
  search, name enquiry, fee quote, limits and balance - and the transfer by the
  existing payout executor.

A session is marked on its payload when it opens (`vtu_form` / `send_form`), and
the endpoint dispatches on that mark rather than the flag, so a session already
open when a flag is turned back off still finishes on the screens it started on.
"""
import logging

from . import flows as F
from .release2 import _bind, _warn

log = logging.getLogger("whatsapp")

#: Payload marks for a session opened on a Release 3 form.
VTU_MARK = "vtu_form"
SEND_MARK = "send_form"
#: The page a VTU_FORM session is on.
FORM_STEP = "form"
PLAN_STEP = "plan"

_VTU_PAGES = (F.VTU_FORM, F.VTU_FORM_CHAIN)
_SEND_PAGES = (F.SEND_MONEY, F.SEND_MONEY_CHAIN)


def is_vtu_form(pa) -> bool:
    return bool((pa.payload or {}).get(VTU_MARK))


def is_send_money(pa) -> bool:
    return bool((pa.payload or {}).get(SEND_MARK))


def _page(pa, pages) -> str:
    """The root or the menu twin - whichever this session opened on. An error
    re-render must answer that same id: answering the other one is a navigation,
    which Meta refuses when no route declares it."""
    standing = F._flow_screen(pa, pages[0])
    return standing if standing in pages else pages[0]


# --------------------------------------------------------------------------- #
# 1. airtime or data on one page (VTU_ONE_SCREEN)
# --------------------------------------------------------------------------- #
def _network_options() -> list:
    from .router import NETWORK_NAMES

    return [{"id": k, "title": v} for k, v in NETWORK_NAMES.items()]


def _amount_help(pa) -> str:
    from .router import MIN_AIRTIME, _flow_balance_line

    minimum = f"Minimum ₦{MIN_AIRTIME:,.0f}"
    balance = _flow_balance_line(pa)
    return f"{balance} · {minimum}" if balance else minimum


def vtu_form_screen(pa, error: str = "", screen: str = "", kind=None, phone=None, net=None) -> dict:
    """The one-page form. The phone defaults to the customer's own number and
    the network to the one that number's prefix belongs to - the same guess the
    chat makes (router._network_from_prefix). It is only a pre-selection: the
    customer sees it and can change it, and a network they chose always wins.
    """
    from .router import NETWORK_NAMES, _network_from_prefix, _own_phone

    p = pa.payload
    kind = kind if kind in ("airtime", "data") else (p.get("vtu_kind") or "airtime")
    phone = (phone if phone is not None else p.get("phone")) or _own_phone(pa.user) or ""
    detected = _network_from_prefix(phone)
    if net not in NETWORK_NAMES:
        net = p.get("net") if p.get("net") in NETWORK_NAMES else detected
    if detected and net in (None, detected):
        hint = "Detected from the number"
    elif detected:
        hint = f"Check the network - this number looks like {NETWORK_NAMES[detected]}"
    else:
        hint = "Check the network - we couldn't tell it from the number"
    return {"screen": screen or _page(pa, _VTU_PAGES),
            "data": {"kind": kind, "phone": phone, "networks": _network_options(),
                     # A Dropdown pre-selection must name an option on the list.
                     "network": net or next(iter(NETWORK_NAMES)),
                     "network_hint": hint, "amount_help": _amount_help(pa),
                     "error": _warn(error)}}


def vtu_plan_screen(pa, error: str = "") -> dict:
    """"Choose a plan" - the NETWORK's plans, fetched at render time, because a
    plan from another network's list is not something the provider can recover.

    Answers the terminal screen when the network has no plans: a RadioButtonsGroup
    bound to an empty array renders a blank page with no way forward.
    """
    from utility.models import DataPlan

    from .router import NETWORK_NAMES, _money

    net = pa.payload.get("net", "")
    net_name = NETWORK_NAMES.get(net, "")
    plans = list(DataPlan.objects.filter(network=net, active=True)[:20])
    if not plans:
        log.warning("wa_vtu_form_no_plans pa=%s", pa.pk)
        return F._success_screen(
            f"No {net_name} data plans are available right now. Please try again shortly.",
            status="failed")
    return {"screen": F.VTU_PLAN,
            "data": {"plans": [{"id": plan.plan_code, "title": plan.name[:30],
                                "description": f"{_money(plan.price)} · {plan.validity}"}
                               for plan in plans],
                     "summary": f"{net_name} data for {pa.payload.get('phone', '')}",
                     "error": _warn(error)}}


def vtu_form_screen_for(pa) -> dict:
    """INIT: re-render whichever page this session is on."""
    if pa.payload.get("vtu_step") == PLAN_STEP:
        return vtu_plan_screen(pa)
    return vtu_form_screen(pa)


def submit_vtu_form(pa, data: dict) -> dict:
    """A submit from VTU_FORM (kind, phone, network, amount) or VTU_PLAN (plan).

    Dispatched on the session's step, with one allowance: the device's own back
    arrow returns from VTU_PLAN to VTU_FORM without telling the endpoint, so a
    submit that carries no `plan` is the first page again, whatever the step.
    """
    if pa.payload.get("vtu_step") == PLAN_STEP and "plan" in data:
        return _submit_plan(pa, data)
    return _submit_form(pa, data)


def _submit_form(pa, data: dict) -> dict:
    from .router import NETWORK_NAMES, _clear_actions, _network_from_prefix, _phone_from, _touch

    here = _page(pa, _VTU_PAGES)
    kind = str(data.get("kind", "") or "").strip().lower()
    typed_phone = str(data.get("phone", "") or "").strip()
    net = str(data.get("network", "") or "").strip()

    def again(message):
        return vtu_form_screen(pa, error=message, screen=here, kind=kind,
                               phone=typed_phone, net=net)

    if kind not in ("airtime", "data"):
        return again("Pick airtime or data.")
    phone = _phone_from(typed_phone, pa.user)
    if not phone:
        return again("Enter a valid phone number, e.g. 08031234567.")
    if net not in NETWORK_NAMES:
        return again("Pick a network from the list.")
    # The network was pre-selected from the number the form OPENED with. Someone
    # who changes the number to a friend's on another network can leave it
    # behind, and a top-up on the wrong network is money gone. Asked once, not
    # imposed: a ported number makes the prefix a guess, so tapping Continue
    # again with the same choice keeps it.
    detected = _network_from_prefix(phone)
    confirmed = f"{phone}:{net}"
    if detected and detected != net and pa.payload.get("vtu_net_kept") != confirmed:
        pa.payload["vtu_net_kept"] = confirmed
        _touch(pa, payload=pa.payload)
        return again(f"{phone} looks like a {NETWORK_NAMES[detected]} number, but "
                     f"{NETWORK_NAMES[net]} is selected. Change the network, or tap Continue "
                     f"again to keep {NETWORK_NAMES[net]}.")
    pa.payload.update({"vtu_kind": kind, "net": net, "phone": phone, "vtu_step": FORM_STEP})
    # The executor is chosen by action_type once the PIN is right.
    _touch(pa, action_type=kind, payload=pa.payload)

    if kind == "data":
        screen = vtu_plan_screen(pa)
        if screen["screen"] != F.VTU_PLAN:
            _clear_actions(pa.msisdn)
            return screen
        pa.payload["vtu_step"] = PLAN_STEP
        _touch(pa, payload=pa.payload)
        return screen

    if not str(data.get("amount", "") or "").strip():
        return again("Enter the airtime amount, e.g. 500.")
    return F.arm_vtu(pa, {"phone": phone, "amount": data.get("amount"),
                          "narration": pa.payload.get("narration", "")},
                     lambda message: vtu_form_screen(pa, error=message, screen=here))


def _submit_plan(pa, data: dict) -> dict:
    return F.arm_vtu(pa, {"plan": data.get("plan"), "phone": pa.payload.get("phone", ""),
                          "narration": pa.payload.get("narration", "")},
                     lambda message: vtu_plan_screen(pa, error=message))


def _vtu_payload(user, kind: str, screen: str, **extra) -> dict:
    from .router import _own_phone

    phone = _own_phone(user) or ""
    return {VTU_MARK: True, "vtu_step": FORM_STEP, "vtu_kind": kind, "pin_attempts": 0,
            "flow_screen": screen, **({"phone": phone} if phone else {}), **extra}


def start_vtu_form(user, msisdn: str, kind: str = "airtime") -> bool:
    """Open VTU_FORM from the chat, with `kind` pre-selected.

    True when this handled the message (the form went out, or the account is
    blocked from paying and has been told). False sends the caller down its
    unchanged path - including when the send itself fails.
    """
    from .router import (_blocked_from_spending, _clear_actions, _new_flow, flows_live, reply,
                         send_flow, sign_flow_token)

    if not (F.vtu_one_screen_live() and flows_live()):
        return False
    if _blocked_from_spending(user, msisdn, biller=True):
        return True
    kind = kind if kind in ("airtime", "data") else "airtime"
    pa = _new_flow(user, msisdn, kind, F.FLOW_VTU_STATE, _vtu_payload(user, kind, F.VTU_FORM))
    first = vtu_form_screen(pa)
    res = send_flow(msisdn, sign_flow_token(pa), header="Airtime / Data",
                    body="Top up any number or buy a data plan - your PIN stays private.",
                    screen=F.VTU_FORM, screen_data=first["data"], cta="Buy")
    if not res.get("success"):
        log.warning("wa_vtu_form_send_failed pa=%s detail=%r", pa.id, res.get("error_detail", ""))
        _clear_actions(msisdn)
        return False
    reply(msisdn, "📱 Tap *Buy* on the secure form above.")
    return True


def open_vtu_form_chain(user, msisdn: str, session: str) -> dict:
    """The menu's "Airtime & data" item, on VTU_FORM's chained twin."""
    from .router import _new_flow

    pa = _new_flow(user, msisdn, "airtime", F.FLOW_VTU_STATE,
                   _vtu_payload(user, "airtime", F.VTU_FORM_CHAIN, menu_session=session))
    _bind(pa, session)
    return vtu_form_screen(pa, screen=F.VTU_FORM_CHAIN)


# --------------------------------------------------------------------------- #
# 2. the send-money form (TRANSFER_FORM_V2)
# --------------------------------------------------------------------------- #
def send_money_screen(pa, error: str = "", screen: str = "") -> dict:
    from .router import _flow_balance_line

    return {"screen": screen or _page(pa, _SEND_PAGES),
            "data": {"balance": _flow_balance_line(pa) or "Enter the amount in naira",
                     "error": _warn(error)}}


def send_money_screen_for(pa) -> dict:
    """INIT: the form, on whichever of the pair this session opened on."""
    return send_money_screen(pa)


#: The fields SEND_MONEY posts. Anything else is dropped before the shared
#: handler sees it, so the legacy bank-picker branch (`bank_search`) can never be
#: reached from a screen that has no picker.
_SEND_FIELDS = ("account_number", "bank", "amount", "narration")


def submit_send_money(pa, token: str, data: dict) -> dict:
    """Account number, bank, amount and note - then the transfer form's own
    checks: bank detection from the account number, the typed-name bank search,
    the name enquiry, the fee, limits and balance, and the same PIN_CHAIN."""
    here = _page(pa, _SEND_PAGES)

    def render(error="", candidates=None, query="", hint=""):
        # No bank list on this page: a narrowing hint is said as the message.
        return send_money_screen(pa, error=error or hint, screen=here)

    return F._submit_transfer_form(token, {k: data.get(k) for k in _SEND_FIELDS}, render=render)


def start_send_money(user, msisdn: str) -> bool:
    """Open SEND_MONEY from the chat. The caller has already made the spending
    and bank-list checks the old form makes. False when the send failed, so the
    caller carries on exactly as it did before this release."""
    from .router import _clear_actions, _new_flow, flows_live, reply, send_flow, sign_flow_token

    if not (F.transfer_form_v2_live() and flows_live()):
        return False
    pa = _new_flow(user, msisdn, "transfer", F.FLOW_FORM_STATE,
                   {SEND_MARK: True, "pin_attempts": 0, "flow_screen": F.SEND_MONEY})
    first = send_money_screen(pa)
    res = send_flow(msisdn, sign_flow_token(pa), header="Send money",
                    body="Fill in the details privately - we'll confirm the account name "
                         "before anything moves.",
                    screen=F.SEND_MONEY, screen_data=first["data"], cta="Send money")
    if not res.get("success"):
        log.warning("wa_send_money_send_failed pa=%s detail=%r", pa.id, res.get("error_detail", ""))
        _clear_actions(msisdn)
        return False
    reply(msisdn, "💸 Tap *Send money* on the secure form above.")
    return True


def open_send_money_chain(user, msisdn: str, session: str) -> dict:
    """The menu's "Send money" item, on SEND_MONEY's chained twin."""
    from .router import _new_flow

    pa = _new_flow(user, msisdn, "transfer", F.FLOW_FORM_STATE,
                   {SEND_MARK: True, "pin_attempts": 0, "menu_session": session,
                    "flow_screen": F.SEND_MONEY_CHAIN})
    _bind(pa, session)
    return send_money_screen(pa, screen=F.SEND_MONEY_CHAIN)
