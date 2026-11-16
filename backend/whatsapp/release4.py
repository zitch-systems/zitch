"""Release 4 WhatsApp Flow screens: "Which bill?" with cable TV paid in the form,
and the BVN and home-address steps inside the sign-up form.

Same contract as release2.py and release3.py. Every entry point is behind a
WHATSAPP_FLOW flag that defaults OFF (flows.bill_cable_form_live(),
flows.signup_bvn_live()), because the Flow JSON is published by an operator AFTER
the code deploys and answering with a screen Meta has not published is "Couldn't
load content". With both flags off nothing here is reachable.

Nothing here moves money or re-implements the bank's account opening:

* the cable pages verify the smartcard with the same partner-bank biller check the
  chat uses (vtu_verify_customer, with and then without a package), make the
  chat's balance and limit checks, and end on the same PIN_CHAIN confirm; the
  payment is run by the existing cable executor once the PIN is right;
* the BVN step drives the chat's own account-opening code
  (router._account_submit_identity / router.account_flow_otp): the bank checks
  the BVN and sends its SMS code, and that code opens the account. Only the chat
  messages those functions send on success are held back, and sent once, when
  the form finishes;
* the address is saved exactly as the app's sign-up saves it
  (accounts.views.kyc_address_save): house number, street, LGA and a state from
  the list, never verified here.

A bill session is marked on its payload (`bill_type`) when it opens, and the
endpoint dispatches on that mark rather than the flag, so a session already open
when a flag is turned back off still finishes on the screens it started on.
"""
import logging
import re
from decimal import Decimal

from . import flows as F
from .release2 import BILL_DETAILS_STEP, _bind, _warn, bill_screen

log = logging.getLogger("whatsapp")

# --------------------------------------------------------------------------- #
# 1. "Which bill?" and cable TV (BILL_CABLE_FORM)
# --------------------------------------------------------------------------- #
#: Payload mark for a bill session opened on BILL_TYPE.
BILL_TYPE_MARK = "bill_type"
TYPE_STEP = "type"
CABLE_STEP = "cable"
CABLE_PLAN_STEP = "cable_plan"
_TYPE_PAGES = (F.BILL_TYPE, F.BILL_TYPE_CHAIN)
#: RadioButtonsGroup holds at most 20 options.
_MAX_PACKAGES = 20


def is_bill_type(pa) -> bool:
    return bool((pa.payload or {}).get(BILL_TYPE_MARK))


def _type_page(pa) -> str:
    standing = F._flow_screen(pa, F.BILL_TYPE)
    return standing if standing in _TYPE_PAGES else F.BILL_TYPE


def bill_type_screen(pa, error: str = "", screen: str = "") -> dict:
    return {"screen": screen or _type_page(pa), "data": {"error": _warn(error)}}


def _provider_options() -> list:
    """The same provider list the chat's "Which provider?" prompt shows."""
    from .router import CABLE_NAMES

    return [{"id": k, "title": v} for k, v in CABLE_NAMES.items()]


def cable_screen(pa, error: str = "") -> dict:
    return {"screen": F.CABLE_SCREEN,
            "data": {"providers": _provider_options(), "error": _warn(error)}}


def cable_plan_screen(pa, error: str = "") -> dict:
    """The provider's packages, read at render time. Answers the terminal screen
    when there are none: a RadioButtonsGroup bound to an empty array renders a
    blank page with no way forward."""
    from .router import CABLE_NAMES, _flow_balance_line, _money

    p = pa.payload
    plans = list(CablePlanSource.for_provider(p.get("prov", ""))[:_MAX_PACKAGES])
    if not plans:
        log.warning("wa_cable_form_no_packages pa=%s", pa.pk)
        return F._success_screen("No packages are available for that provider right now. "
                                 "Please try again shortly.", status="failed")
    cust = (p.get("customer") or "").strip()
    return {"screen": F.CABLE_PLAN,
            "data": {"verified": f"✓ Smartcard verified · {cust}" if cust else "✓ Smartcard verified",
                     "summary": f"{CABLE_NAMES.get(p.get('prov', ''), '')} · Smartcard {p.get('iuc', '')}",
                     "packages": [{"id": plan.cable_plan_code, "title": plan.name[:30],
                                   "description": _money(plan.price)} for plan in plans],
                     "balance": _flow_balance_line(pa) or "Your balance is checked before you pay",
                     "error": _warn(error)}}


class CablePlanSource:
    """One place the packages are read from, so the page and the submit agree."""

    @staticmethod
    def for_provider(prov: str):
        from utility.models import CablePlan

        return CablePlan.available_for_provider(prov)


def bill_type_screen_for(pa) -> dict:
    """INIT / BACK: re-render whichever page this session is on."""
    step = pa.payload.get("bill_step")
    if step == CABLE_PLAN_STEP:
        return cable_plan_screen(pa)
    if step == CABLE_STEP:
        return cable_screen(pa)
    if step == TYPE_STEP:
        return bill_type_screen(pa)
    from .release2 import bill_screen_for

    return bill_screen_for(pa)


def submit_bill_type(pa, data: dict) -> dict:
    """A submit from any page of a BILL_TYPE session.

    Dispatched on what the page posted rather than the step alone: the device's
    own back arrow returns to an earlier page without telling the endpoint, so a
    submit carrying `bill` is the first page again, one carrying `smartcard` is
    the cable page again, whatever the step says.
    """
    if "bill" in data:
        return _submit_type(pa, data)
    if "smartcard" in data:
        return _submit_cable(pa, data)
    if "package" in data:
        return _submit_package(pa, data)
    from .release2 import submit_bill

    if "meter" in data:
        # The electricity details page, reached again with the back arrow from
        # its amount page: answer it as the details it is.
        pa.payload["bill_step"] = BILL_DETAILS_STEP
    return submit_bill(pa, data)


def _submit_type(pa, data: dict) -> dict:
    from .router import _touch

    choice = str(data.get("bill", "") or "").strip().lower()
    if choice == "electricity":
        # The executor is chosen by action_type once the PIN is right.
        pa.payload.update({"bill_step": BILL_DETAILS_STEP, "flow_screen": F.BILL_CHAIN})
        _touch(pa, action_type="electricity", payload=pa.payload)
        return bill_screen(screen=F.BILL_CHAIN)
    if choice == "cable":
        pa.payload.update({"bill_step": CABLE_STEP, "flow_screen": F.CABLE_SCREEN})
        _touch(pa, action_type="cable", payload=pa.payload)
        return cable_screen(pa)
    return bill_type_screen(pa, error="Choose electricity or cable TV.")


def _submit_cable(pa, data: dict) -> dict:
    """Provider and smartcard - checked with the provider before a package is
    even shown, so the customer sees whose card it is before choosing."""
    from .router import CABLE_NAMES, _cable_id, _touch, vtu_verify_customer

    prov = _cable_id(str(data.get("provider", "") or "").strip())
    if prov is None:
        return cable_screen(pa, "Pick your provider from the list.")
    iuc = re.sub(r"\D", "", str(data.get("smartcard", "") or ""))[:20]
    if len(iuc) < 6:
        return cable_screen(pa, "Enter a valid smartcard / IUC number.")
    res = vtu_verify_customer(CABLE_NAMES[prov].lower(), iuc)
    if not res.get("success"):
        return cable_screen(pa, "Couldn't validate that smartcard. Check the number and try again.")
    for stale in ("plan_code", "price", "plan_name", "meta"):
        pa.payload.pop(stale, None)
    pa.payload.update({"prov": prov, "iuc": iuc, "customer": res.get("customer_name", ""),
                       "bill_step": CABLE_PLAN_STEP, "flow_screen": F.CABLE_PLAN})
    _touch(pa, action_type="cable", payload=pa.payload)
    screen = cable_plan_screen(pa)
    if screen["screen"] != F.CABLE_PLAN:
        from .router import _clear_actions

        _clear_actions(pa.msisdn)
    return screen


def cable_check_package(pa, user) -> tuple[str, bool]:
    """Every money check on the chosen package, the ones the chat ladder makes
    at its smartcard step: the card validated for THIS package, the balance and
    the limits. ("", False) when payable (and the payload is armed), else (why,
    is_limit). A limit ends the flow; the other refusals keep the page open so
    the customer can pick another package."""
    from .router import (CABLE_NAMES, _fresh_wallet_balance, _insufficient, _money,
                         daily_limit_error, send_limit_error, vtu_verify_customer)

    p = pa.payload
    res = vtu_verify_customer(CABLE_NAMES[p["prov"]].lower(), p["iuc"], p["plan_code"])
    if not res.get("success"):
        return "Couldn't validate that smartcard for this package. Pick another package or try again.", False
    from .router import bill_charge

    price = Decimal(p["price"])
    # The bank's charge is taken from the same account on top of the package.
    charge = bill_charge(CABLE_NAMES[p["prov"]].lower(), {"variation_code": p["plan_code"]})
    p["charge"] = str(charge)
    total = price + charge
    if _insufficient(user, total):
        return f"Insufficient balance ({_money(_fresh_wallet_balance(user))}). Pick a cheaper package or add money first.", False
    limit_msg = send_limit_error(user, total, biller=True) or daily_limit_error(user, total, "bill")
    if limit_msg:
        return limit_msg, True
    p["customer"] = res.get("customer_name", "") or p.get("customer", "")
    p["meta"] = {"iuc": p["iuc"], "provider": p["prov"], "plan_code": p["plan_code"]}
    return "", False


def _submit_package(pa, data: dict) -> dict:
    """The package, through the chat path's own checks, then straight into the
    PIN page of the SAME session - the payment the chat would have armed."""
    from .router import _clear_actions, _flow_fields, _touch

    user = pa.user
    if not pa.payload.get("iuc") or not pa.payload.get("prov"):
        return cable_screen(pa, "Enter your provider and smartcard first.")
    code = str(data.get("package", "") or "").strip()
    plan = CablePlanSource.for_provider(pa.payload["prov"]).filter(cable_plan_code=code).first()
    if plan is None:
        return cable_plan_screen(pa, error="Pick a package from the list.")
    pa.payload.update({"plan_code": plan.cable_plan_code, "price": str(plan.price),
                       "plan_name": plan.name})
    problem, is_limit = cable_check_package(pa, user)
    if problem and is_limit:
        _clear_actions(pa.msisdn)
        return F._success_screen(problem, status="failed")
    if problem:
        _touch(pa, payload=pa.payload)
        return cable_plan_screen(pa, error=problem)
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
    _touch(pa, action_type="cable", state=F.FLOW_PIN_STATE, payload=pa.payload)
    return F._pin_screen(fields, screen=F.PIN_CHAIN)


def _bill_type_payload(screen: str, **extra) -> dict:
    return {BILL_TYPE_MARK: True, "bill_step": TYPE_STEP, "pin_attempts": 0,
            "flow_screen": screen, **extra}


def start_bill_type_form(user, msisdn: str) -> bool:
    """Menu "Pay a bill" with BILL_CABLE_FORM on: "Which bill?" in the secure
    form, with the chat path beside it. Same contract as release2.start_bill_form:
    True when handled, False sends the caller down its unchanged path."""
    from .router import (SERVICE_MENUS, _blocked_from_spending, _clear_actions, _new_flow,
                         flows_live, reply_list, send_flow, sign_flow_token)

    if not (F.bill_cable_form_live() and flows_live()):
        return False
    if _blocked_from_spending(user, msisdn, biller=True):
        return True
    pa = _new_flow(user, msisdn, "electricity", F.FLOW_BILL_STATE, _bill_type_payload(F.BILL_TYPE))
    first = bill_type_screen(pa, screen=F.BILL_TYPE)
    res = send_flow(msisdn, sign_flow_token(pa), header="",
                    body=BILL_CARD, screen=F.BILL_TYPE, screen_data=first["data"], cta="Pay a bill")
    if not res.get("success"):
        log.warning("wa_bill_type_form_send_failed pa=%s detail=%r", pa.id, res.get("error_detail", ""))
        _clear_actions(msisdn)
        return False
    return True


#: The approved "Pay a bill" card: one message, the chat path named in it.
BILL_CARD = ("💡 *Pay a bill*\n\nTap *Pay a bill* to use the secure form, or type *electricity* "
             "or *cable* to carry on here in the chat.")


def open_bill_type_chain(user, msisdn: str, session: str) -> dict:
    """The menu's "Pay a bill" item, on BILL_TYPE's chained twin."""
    from .router import _new_flow

    pa = _new_flow(user, msisdn, "electricity", F.FLOW_BILL_STATE,
                   _bill_type_payload(F.BILL_TYPE_CHAIN, menu_session=session))
    _bind(pa, session)
    return bill_type_screen(pa, screen=F.BILL_TYPE_CHAIN)


# --------------------------------------------------------------------------- #
# 2. the BVN and the home address inside sign-up (SIGNUP_BVN_FORM)
# --------------------------------------------------------------------------- #
#: Payload keys on the WaOnboarding row of a signup that took this path.
SIGNUP_MARK = "signup_bvn_form"
_USER_KEY = "signup_user_id"
_ACCOUNT_PA_KEY = "account_pa_id"
_CODE_SENT = "bvn_code_sent"
_FACE_MODE = "bvn_face"          # "Use face check instead" was tapped
_FACE_SENT = "bvn_face_sent"     # the bank's face link is in the chat
_CHOOSE_STATE = "choose"         # the LGA list's placeholder before a state


def _bank_ready(user) -> bool:
    """Can the partner bank open an account right now? When it can't, the BVN
    step is skipped and the chat offers account setup later, as it does today."""
    from utility.providers import partnership_new_business_allowed
    from wallet import views as wallet_views

    return bool(wallet_views._wema_funding_enabled() and partnership_new_business_allowed(user))


def signup_user(ob):
    """The account this signup already opened (before its BVN step), or None."""
    from accounts.models import User

    uid = (ob.payload or {}).get(_USER_KEY)
    if not uid:
        return None
    return User.objects.filter(pk=uid).first()


def signup_after_password(ob):
    """Where a signup goes once its phone and password are settled.

    None keeps the existing path (straight to the PIN). With SIGNUP_BVN_FORM on,
    the account is opened now - the bank can only open an account for a customer
    who exists - and the form goes on to the BVN (or, when the bank can't take
    one right now, to the address). The PIN is still the last step; until it is
    set the account simply has none, the state a chat signup without a PIN has
    always been in."""
    from .router import _clear_onboarding, _create_onboarded_user

    if not F.signup_bvn_live():
        return None
    user = signup_user(ob)
    if user is None:
        user = _create_onboarded_user(ob, ob.msisdn, "")
        if user is None:
            _clear_onboarding(ob.msisdn)
            return F._success_screen("This number already has a Zitch account. Reply 2 in the "
                                     "chat to sign in securely.", status="failed")
        ob.payload[_USER_KEY] = user.pk
        # Only the hash ever sat here, and the account now holds it.
        ob.payload.pop("flow_pw_hash", None)
    ob.payload[SIGNUP_MARK] = True
    if _bank_ready(user):
        return _to_bvn(ob)
    return _to_address(ob)


def _to_bvn(ob) -> dict:
    from .router import _onboard_to

    ob.payload["flow_screen"] = F.SIGNUP_BVN
    _onboard_to(ob, F.FLOW_BVN_STATE)
    return signup_bvn_screen(ob)


def _to_address(ob) -> dict:
    from .router import _onboard_to

    ob.payload["flow_screen"] = F.SIGNUP_ADDRESS
    _onboard_to(ob, F.FLOW_ADDRESS_STATE)
    return signup_address_screen(ob)


def _bvn_page(ob) -> str:
    """SIGNUP_BVN or its empty-code twin, whichever this signup is on."""
    standing = ob.payload.get("flow_screen")
    return F.SIGNUP_BVN_RETRY if standing == F.SIGNUP_BVN_RETRY else F.SIGNUP_BVN


def signup_bvn_screen(ob, error: str = "", notice: str = "") -> dict:
    """The page this signup's BVN step is on, with what to do next on it."""
    p = ob.payload
    face_mode, face_sent = bool(p.get(_FACE_MODE)), bool(p.get(_FACE_SENT))
    code_sent = bool(p.get(_CODE_SENT)) and not face_mode
    if not notice:
        if face_sent:
            notice = ("Open the face check link in the chat. When your bank has confirmed it, "
                      "come back and tap Next.")
        elif face_mode:
            notice = ("Face check: enter your BVN and tap Next. We'll send your bank's secure "
                      "face check link to the chat - no SMS code needed.")
        elif code_sent:
            notice = "✓ Code sent by SMS to the phone number on your BVN."
    if _bvn_page(ob) == F.SIGNUP_BVN_RETRY:
        return {"screen": F.SIGNUP_BVN_RETRY,
                "data": {"summary": notice or "Enter the code your bank sent by SMS.",
                         "error": _warn(error)}}
    # One link serves two purposes - Meta allows two EmbeddedLinks per screen
    # and "Resend code" is the other. They never apply at once: "Do this later"
    # when the bank step can't finish here, else "Use face check instead" while
    # a code is out. The choice is kept so the tap is read back the same way.
    alt = "later" if (error or face_sent) else "face" if code_sent else ""
    if p.get("bvn_alt", "") != alt:
        p["bvn_alt"] = alt
        ob.save(update_fields=["payload"])
    return {"screen": F.SIGNUP_BVN,
            "data": {"summary": "✓ Phone number verified", "notice": notice,
                     "code_sent": code_sent,
                     "alt_link": bool(alt),
                     "alt_text": {"later": "Do this later",
                                  "face": "Use face check instead"}.get(alt, " "),
                     "error": _warn(error)}}


def _account_action(ob, user):
    """The add_account action this step drives - the same one the chat's account
    setup uses, so _account_submit_identity and the bank's code treat it exactly
    as they treat the chat's."""
    from .models import PendingAction
    from .router import _flow_deadline

    pid = ob.payload.get(_ACCOUNT_PA_KEY)
    pa = (PendingAction.objects.filter(pk=pid, user=user, action_type="add_account").first()
          if pid else None)
    if pa is not None:
        return pa
    pa = PendingAction.objects.create(
        user=user, msisdn=ob.msisdn, action_type="add_account", state=F.FLOW_ID_STATE,
        payload={"id_type": "bvn", "id_kind": "bvn", SIGNUP_MARK: True},
        expires_at=_flow_deadline(F.FLOW_ID_STATE))
    ob.payload[_ACCOUNT_PA_KEY] = pa.pk
    return pa


def _account_open(user) -> bool:
    """True once the bank has the account (a number, or accepted and processing)."""
    from wallet.identity import accepted_identity_pending

    from .router import customer_funding_account

    return (bool(customer_funding_account(user).get("account_number"))
            or bool(accepted_identity_pending(user)))


def _stay(ob, error: str = "", notice: str = "") -> dict:
    """Persist this step's marks and answer the page it is on."""
    from .router import _onboard_to

    _onboard_to(ob, ob.step)
    return signup_bvn_screen(ob, error=error, notice=notice)


def submit_signup_bvn(ob, data: dict) -> dict:
    """SIGNUP_BVN and SIGNUP_BVN_RETRY.

    First Next: the BVN goes to the bank through the chat's own account opening;
    the bank checks it and sends its SMS code, and the code box appears on this
    same page (the BVN stays typed). Next again: the code opens the account, then
    the address. A wrong code lands on SIGNUP_BVN_RETRY, whose box starts empty.
    "Do this later" (offered when the bank step can't finish here) goes on to the
    address; the chat's account setup then picks the BVN up after the PIN.
    """
    from .router import _account_submit_identity

    user = signup_user(ob)
    if user is None:
        return F._success_screen("This signup expired. Send us a message to start again.",
                                 status="failed")
    p = ob.payload
    if data.get("alt"):
        # The page's second link: whichever option it was showing.
        data = {"later": True} if p.get("bvn_alt") == "later" else {"face": True}
    if data.get("later"):
        return _to_address(ob)
    if data.get("resend"):
        return _resend_bank_code(ob, user)
    if data.get("face"):
        p.update({_FACE_MODE: True, _CODE_SENT: False})
        return _stay(ob)
    if p.get(_FACE_SENT):
        if _account_open(user):
            return _to_address(ob)
        return _stay(ob, error="Your bank hasn't confirmed the face check yet. Finish it from "
                               "the link in the chat, then tap Next.")
    if p.get(_CODE_SENT) and not p.get(_FACE_MODE):
        return _submit_bank_code(ob, user, str(data.get("code", "") or ""))

    digits = "".join(ch for ch in str(data.get("bvn", "") or "") if ch.isdigit())
    if len(digits) != 11:
        return _stay(ob, error="Your BVN is 11 digits. Check it and try again.")
    pa = _account_action(ob, user)
    if p.get(_FACE_MODE):
        pa.payload["verification_method"] = "face"
        pa.save(update_fields=["payload"])
    outcome = _account_submit_identity(pa, user, ob.msisdn, digits, in_flow=True)
    if outcome == "otp":
        p[_CODE_SENT] = True
        return _stay(ob)
    if outcome == "face":
        # The bank's face link is in the chat (chosen, or the bank already holds
        # an account for this BVN). Its callback decides; Next checks the result.
        p[_FACE_SENT] = True
        p.pop(_ACCOUNT_PA_KEY, None)
        return _stay(ob)
    if outcome == "fail":
        # The chat already carries the bank's reason, and the action is cleared.
        p.pop(_ACCOUNT_PA_KEY, None)
        p[_CODE_SENT] = False
        return _stay(ob, error="We couldn't verify that BVN - see the chat for why. Check it "
                               "and try again, or tap Do this later.")
    # "processing", "adopted", "upgrade" or an adopted account: the bank has it.
    return _to_address(ob)


def _submit_bank_code(ob, user, raw: str) -> dict:
    """The bank's SMS code, through the same completion the chat uses
    (wallet.views.complete_wema_provisioning). Only its chat messages wait: the
    account details are sent once, when the form finishes."""
    from wallet import views as wallet_views

    from .models import PendingAction
    from .router import _clear_actions

    code = "".join(ch for ch in raw if ch.isdigit())
    if not code:
        return _code_retry(ob, "Enter the code from the SMS.")
    pa = PendingAction.objects.filter(pk=ob.payload.get(_ACCOUNT_PA_KEY), user=user).first()
    if pa is None or not pa.payload.get("tracking_id"):
        return _code_expired(ob, "That code has expired.")
    payload, status = wallet_views.complete_wema_provisioning(
        user, code, pa.payload.get("tracking_id", ""))
    if payload.get("success") or payload.get("pending") or status == 202:
        _clear_actions(ob.msisdn)
        ob.payload.pop(_ACCOUNT_PA_KEY, None)
        return _to_address(ob)
    if status == 400:   # expired / mismatched attempt: the same code cannot help
        _clear_actions(ob.msisdn)
        return _code_expired(ob, payload.get("message") or "That code has expired.")
    return _code_retry(ob, payload.get("message") or "That code didn't work. Check the SMS and try again.")


def _code_retry(ob, error: str) -> dict:
    """A wrong code: the empty twin, so the refused code is not left in the box."""
    ob.payload["flow_screen"] = F.SIGNUP_BVN_RETRY
    return _stay(ob, error=error)


def _code_expired(ob, reason: str) -> dict:
    """The attempt itself is over. On SIGNUP_BVN the BVN is still typed, so Next
    starts a fresh one; SIGNUP_BVN_RETRY has no BVN box, so it offers Do this later."""
    ob.payload.pop(_ACCOUNT_PA_KEY, None)
    ob.payload[_CODE_SENT] = False
    if _bvn_page(ob) == F.SIGNUP_BVN_RETRY:
        return _stay(ob, error=f"{reason} Tap Do this later - we'll finish your account "
                               "number in the chat after your PIN.")
    return _stay(ob, error=f"{reason} Tap Next to get a new code.")


def _resend_bank_code(ob, user) -> dict:
    from .models import PendingAction
    from .router import wema_provider

    pa = PendingAction.objects.filter(pk=ob.payload.get(_ACCOUNT_PA_KEY), user=user).first()
    if pa is None or not pa.payload.get("tracking_id"):
        return _stay(ob, error="Tap Next to send your BVN first.")
    res = wema_provider.resend_wallet_otp(user.phone or "", pa.payload["tracking_id"], bvn=True)
    if res.get("success"):
        return _stay(ob, notice="✓ We asked your bank to send the code again.")
    return _stay(ob, error=res.get("message") or "Couldn't resend the code - try again shortly.")


def _state_options() -> list:
    from accounts.views import _NIGERIAN_STATES

    return [{"id": s, "title": "FCT (Abuja)" if s == "FCT" else s} for s in sorted(_NIGERIAN_STATES)]


def _lga_options(state: str) -> list:
    from .lgas import LGAS_BY_STATE

    lgas = LGAS_BY_STATE.get(state) or ()
    if not lgas:
        return [{"id": _CHOOSE_STATE, "title": "Choose your state first"}]
    return [{"id": lga, "title": lga} for lga in lgas]


def signup_address_screen(ob, error: str = "", state: str = "") -> dict:
    from .router import customer_funding_account

    user = signup_user(ob)
    summary = "Saved for Tier 3. Your bank checks it only after Tier 2."
    if user is not None and _account_open(user):
        name = (customer_funding_account(user).get("account_name")
                or f"{user.first_name} {user.last_name}").strip().upper()
        summary = f"✓ BVN verified · {name}"
    state = state or ob.payload.get("address_state", "")
    return {"screen": F.SIGNUP_ADDRESS,
            "data": {"summary": summary, "states": _state_options(),
                     "lgas": _lga_options(state), "error": _warn(error)}}


def submit_signup_address(ob, data: dict) -> dict:
    """House number, street, state and LGA - kept exactly as the app's sign-up
    keeps them (accounts.views.kyc_address_save) - then the PIN.

    Picking a state posts `pick_state` and only fetches that state's LGAs; the
    page comes back with what was typed still in it.
    """
    from accounts.views import _NIGERIAN_STATES, _address_part

    from .router import _onboard_to

    state = _address_part(data, "state", 40)
    match = next((s for s in _NIGERIAN_STATES if s.lower() == state.lower()), "")
    if data.get("pick_state"):
        ob.payload["address_state"] = match
        _onboard_to(ob, ob.step)
        return signup_address_screen(ob, state=match)
    house = _address_part(data, "house_number", 20)
    street = _address_part(data, "street", 120)
    lga = _address_part(data, "lga", 60)
    if not house or not street:
        return signup_address_screen(ob, error="Enter your house number and street.", state=match)
    if not match:
        return signup_address_screen(ob, error="Choose your state from the list.")
    from .lgas import LGAS_BY_STATE

    listed = LGAS_BY_STATE.get(match) or ()
    if not lga or lga == _CHOOSE_STATE or (listed and lga not in listed):
        # Not in the chosen state's list: an LGA picked before the state changed.
        return signup_address_screen(ob, error="Choose your local government area.", state=match)
    user = signup_user(ob)
    if user is None:
        return F._success_screen("This signup expired. Send us a message to start again.",
                                 status="failed")
    if not (user.address_verified or user.address_verification_pending):
        user.set_address(f"{house} {street}, {lga}, {match}")
        user.save(update_fields=["address"])
    if not F.design_v5_live():
        # The Flow published before Release 5 has no SIGNUP_PIN page.
        ob.payload["flow_screen"] = F.PIN_CHAIN
        _onboard_to(ob, F.FLOW_PIN_STATE)
        return F._pin_screen(F._ob_summary(ob), screen=F.PIN_CHAIN)
    ob.payload["flow_screen"] = F.SIGNUP_PIN
    _onboard_to(ob, F.FLOW_SIGNUP_PIN_STATE)
    return signup_pin_screen(ob)


def signup_pin_screen(ob, error: str = "") -> dict:
    """"Create your PIN": the PIN and its confirmation on one page (design step 8)."""
    screen = F.SIGNUP_PIN_RETRY if ob.payload.get("flow_screen") == F.SIGNUP_PIN_RETRY else F.SIGNUP_PIN
    return {"screen": screen, "data": {"error": _warn(error)}}


def submit_signup_pin(ob, data: dict) -> dict:
    """Both boxes are checked together with the same PIN policy as everywhere
    else. A refusal lands on SIGNUP_PIN_RETRY, whose boxes start empty, so the
    refused digits are never left in a masked box to be resubmitted."""
    from accounts.models import transaction_pin_rejection

    from .router import _onboard_to, finish_onboarding_from_flow

    pin = "".join(ch for ch in str(data.get("pin", "") or "") if ch.isdigit())
    again = "".join(ch for ch in str(data.get("confirm_pin", "") or "") if ch.isdigit())
    problem = ""
    if len(pin) != 6:
        problem = "Your PIN is 6 digits."
    elif pin != again:
        problem = "Those PINs didn't match. Enter the same 6 digits in both boxes."
    else:
        rejected = transaction_pin_rejection(pin)
        if rejected:
            problem = rejected.rstrip(".") + "."
    if problem:
        ob.payload["flow_screen"] = F.SIGNUP_PIN_RETRY
        _onboard_to(ob, ob.step)
        return signup_pin_screen(ob, error=problem)
    message = finish_onboarding_from_flow(ob, pin)
    if message.startswith("✅"):
        return F._success_screen(message.lstrip("✅ ").strip(), status="success")
    return F._success_screen(message, status="failed")


def send_signup_welcome(user, msisdn: str) -> None:
    """The one message a signup that took this path ends on (design step 9): the
    account number when the bank has opened it, then whatever checks are left -
    the same roll-in the chat makes once an account is opened."""
    from .router import (_kyc_continue_after_account, _send_account_details, _start_add_account,
                         get_or_create_wallet, reply)

    first = (user.first_name or "").strip().title() or "there"
    wallet = get_or_create_wallet(user)
    if wallet.account_number:
        _send_account_details(msisdn, wallet,
                              intro=f"🎉 *Welcome to Zitch, {first}!* Your Zitch account number is ready.")
        _kyc_continue_after_account(user, msisdn, attempted={"bvn"})
        return
    if _account_open(user):
        reply(msisdn, f"🎉 *Welcome to Zitch, {first}!* Your bank accepted your BVN and is "
                      "creating your account number - we'll message you here as soon as it's "
                      "ready. Reply *hi* to see everything you can do.")
        return
    reply(msisdn, f"🎉 *Welcome to Zitch, {first}!* Your Zitch account is set up. Reply *hi* "
                  "to see everything you can do.")
    if _bank_ready(user):
        # The BVN step was left for later: the chat's own account setup picks it up.
        _start_add_account(user, msisdn, after_signup=True)
