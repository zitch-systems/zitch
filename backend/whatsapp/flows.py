"""WhatsApp Flows business logic — the secure PIN pad handler.

`flows_crypto` handles the envelope; this module handles the (already decrypted)
data-exchange request: it maps a signed `flow_token` back to the pending money
action, verifies the PIN server-side (same brute-force lockout the chat/app use),
and — only on a correct PIN — executes the transaction and returns a terminal
success screen. The PIN is validated here, never echoed anywhere.

Screen ids mirror the published Flow JSON (`flow_assets/pin_flow.json`):
  PIN_SCREEN — masked PIN input; its submit does the data_exchange.
  SUCCESS    — terminal screen showing the outcome.
"""
import base64
import hashlib
import hmac
import logging

from django.conf import settings

log = logging.getLogger("whatsapp")

PIN_SCREEN = "PIN_SCREEN"
#: The set-then-confirm second entry. Its OWN screen rather than a re-render of
#: PIN_SCREEN: WhatsApp keeps a form's client-side state when the endpoint
#: responds with the same screen, so "re-enter to confirm" re-rendered onto the
#: first screen arrived with the first PIN still sitting in the box — one tap
#: "confirmed" it without a single digit retyped, which is no confirmation at
#: all. A separate screen starts empty because it is a separate form.
PIN_CONFIRM = "PIN_CONFIRM"
SIGNUP_SCREEN = "SIGNUP_SCREEN"
#: The signup ladder's middle pages: the email code (sent when the details are
#: accepted, entered on the SAME open session) and the account phone number.
SIGNUP_EMAIL_CODE = "SIGNUP_EMAIL_CODE"
SIGNUP_PHONE = "SIGNUP_PHONE"
SIGNUP_PHONE_CODE = "SIGNUP_PHONE_CODE"
#: Chained twins of PIN_SCREEN and IDENTITY_SCREEN. Meta forbids ONE screen being
#: both a flow's opening screen and the target of another screen's route: a Flow
#: may only open on a ROOT of the routing graph ("Specified screen X is not
#: allowed as first screen of this flow"). PIN_SCREEN and IDENTITY_SCREEN are
#: both — sent directly to confirm a payment / collect a BVN, AND chained into
#: from a form. So the chained arrival is a separate id with an identical layout;
#: `flow_screen` on the payload records which of the pair a session is sitting on
#: so error re-renders stay put instead of navigating.
PIN_CHAIN = "PIN_CHAIN"
IDENTITY_CHAIN = "IDENTITY_CHAIN"
#: The second identity attempt. A DIFFERENT screen for the same reason
#: PIN_CONFIRM is one: WhatsApp keeps a form's client-side state when the
#: endpoint answers with the same screen id, so "that number isn't valid, try
#: again" re-rendered onto IDENTITY_SCREEN arrived with the rejected digits
#: still in the box — one tap resubmitted the number that had just been
#: refused, and burned the next attempt on it. A separate screen starts empty.
IDENTITY_RETRY = "IDENTITY_RETRY"
#: The 6-digit code pages. IDENTITY_CHAIN carries a code reached IN-SESSION
#: (BVN accepted -> the bank's OTP; email address -> its code); CODE_SCREEN is
#: the ROOT for a code asked in a fresh flow message; CODE_RETRY carries every
#: wrong-code error render so the masked box never comes back holding the code
#: that just failed. Identity number fields are 11/11 client-side and code
#: fields 6/6 — which is why codes can no longer ride IDENTITY_SCREEN.
CODE_SCREEN = "CODE_SCREEN"
CODE_RETRY = "CODE_RETRY"
#: The same problem on the money path, and worse: a wrong PIN re-rendered onto
#: PIN_SCREEN came back with the wrong PIN still in the box, so tapping Confirm
#: resubmitted it — spending another of the five attempts on digits already
#: known to be wrong, and walking the customer into a lockout they did not type.
PIN_RETRY = "PIN_RETRY"
#: How many times a masked PIN box may be shown in one Flow session, per step.
#: These are SCREEN budgets before they are policy budgets. Only two distinct
#: create-PIN ids exist (the PIN_SCREEN/PIN_CHAIN root, then PIN_RETRY) and only
#: two confirm ids (PIN_CONFIRM, PIN_CONFIRM_RETRY), so a third render would have
#: to repeat an id it has already used — and a repeated id is exactly what leaves
#: the refused digits in the box. Raising either number without adding a screen
#: puts the bug straight back; test_flow_pin_attempts pins the two together.
_PIN_CREATE_ATTEMPTS = 2    # weak-PIN refusals per create session
_PIN_CONFIRM_ATTEMPTS = 2   # confirm mismatches per create session
#: And on the creation pair: a MISMATCHED confirm entry re-rendered onto
#: PIN_CONFIRM kept the mismatched digits in its masked box, so one tap
#: resubmitted the same mismatch forever — in a field the customer cannot even
#: read to correct. The error render goes here instead; the clean first render
#: stays on PIN_CONFIRM. Always legal: PIN_CONFIRM routes here, and an error on
#: this screen re-renders it (same id).
PIN_CONFIRM_RETRY = "PIN_CONFIRM_RETRY"
TRANSFER_FORM = "TRANSFER_FORM"
#: The airtime/data ladder, one Flow instead of four chat round-trips: what to
#: buy, then the network, then the details, then the PIN — each a page of the
#: SAME open session. VTU_SCREEN is the root; the other three are only ever
#: routed into, which is what keeps the root openable (see PIN_CHAIN).
#: Airtime and data split at the third page because their inputs genuinely
#: differ — an amount you type versus a plan you pick from that network's list.
VTU_SCREEN = "VTU_SCREEN"
VTU_NETWORK = "VTU_NETWORK"
VTU_AIRTIME = "VTU_AIRTIME"
VTU_DATA = "VTU_DATA"
IDENTITY_SCREEN = "IDENTITY_SCREEN"
EMAIL_SCREEN = "EMAIL_SCREEN"
SUCCESS_SCREEN = "SUCCESS"
#: The outcome, on a screen that does NOT end the Flow.
#:
#: Identical in content to SUCCESS and different in exactly one way that matters:
#: it is not terminal, so answering it leaves the panel open with the outcome on
#: it and a Done button. A Flow that closes itself is a statement that the job is
#: finished, and on a pending or failed payment that is the wrong thing to say —
#: the customer was left in their own chat thread working out whether their money
#: had moved.
#:
#: It also solves what re-rendering a PIN screen could not: WhatsApp keeps a
#: form's client-side value on a same-id reply, so showing an outcome on the PIN
#: page brought back the six rejected digits in a box fixed at 6/6, which then
#: refuses new keystrokes and reads as broken.
RESULT_SCREEN = "RESULT"
FLOW_PIN_STATE = "flow_pin"   # PendingAction.state (and WaOnboarding.step) while a secure Flow is armed
FLOW_SIGNUP_STATE = "flow_signup"   # WaOnboarding.step while the signup form is open
FLOW_EMAIL_CODE_STATE = "flow_email_code"   # ...while the signup email code is pending
FLOW_PHONE_STATE = "flow_phone"             # ...while the signup phone page is open
FLOW_PHONE_CODE_STATE = "flow_phone_code"   # ...while the signup phone SMS code is pending
FLOW_PASSWORD_STATE = "flow_password"       # ...while the app-password page is open

#: The signup page that sets the password used to sign in to the MOBILE APP.
#:
#: It lives in the Flow, never in the chat, and that is the whole design. A
#: password typed as a WhatsApp message is stored forever in the customer's own
#: chat history, in ours, and on Meta's servers — readable by anyone who picks
#: up the unlocked phone. Inside a Flow it is encrypted to this endpoint,
#: appears in no transcript, and is hashed before it is written anywhere. Same
#: reason the PIN and the BVN are collected here.
SIGNUP_PASSWORD = "SIGNUP_PASSWORD"
FLOW_FORM_STATE = "flow_transfer_form"   # PendingAction.state while the transfer form is open
FLOW_VTU_STATE = "flow_vtu"   # ...while the airtime/data form is open (payload["vtu_step"] says which page)
FLOW_ID_STATE = "flow_identity"   # PendingAction.state while the identity Flow is armed
_OB_PREFIX = "ob"             # marks a flow_token that addresses an onboarding, not a money action
_ID_PREFIX = "id"             # marks a flow_token that addresses a KYC identity step
#: id_kind for the bank's SMS code that completes account creation. It rides the
#: identity token because it is the second half of one step: the BVN goes in on
#: the masked screen and the code that name-matches it comes back on the same.
ACCOUNT_OTP = "account_otp"
_AP_PREFIX = "ap"             # marks an app hand-off token (deep-link biometric approval)

#: Confirm states an app hand-off may resolve in. Both mean "armed, awaiting the
#: customer's authorisation" — which secure channel was offered first (Flow or
#: SMS code) doesn't change what approving in the app means.
_APPROVABLE_STATES = (FLOW_PIN_STATE, "pin")


# --------------------------------------------------------------------------- #
# flow_token: a signed handle to the pending action
# --------------------------------------------------------------------------- #
def _sig(payload: str) -> str:
    mac = hmac.new(settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:22]


def sign_flow_token(pa) -> str:
    """Opaque, tamper-evident handle for a pending action: ``<id>.<sig>`` where
    the signature binds the id to the number, so a token can't be edited to point
    at another user's action even if the encrypted channel were somehow bypassed."""
    return f"{pa.id}.{_sig(f'{pa.id}:{pa.msisdn}')}"


def sign_onboarding_token(ob) -> str:
    """Signed handle for a signup in its PIN step. Prefixed so it can never be
    confused with a money-action token: the two resolve through different
    lookups, and a token for one is rejected by the other."""
    return f"{_OB_PREFIX}{ob.id}.{_sig(f'{_OB_PREFIX}{ob.id}:{ob.msisdn}')}"


def resolve_onboarding_token(token: str):
    """Return the live WaOnboarding for a signed onboarding flow_token, or None
    if it is malformed, forged, expired, or no longer in the Flow-PIN step."""
    from .models import WaOnboarding

    raw = (token or "").strip()
    if not raw.startswith(_OB_PREFIX) or "." not in raw:
        return None
    head, _, sig = raw.partition(".")
    pid = head[len(_OB_PREFIX):]
    if not pid.isdigit():
        return None
    ob = WaOnboarding.objects.filter(id=int(pid)).first()
    if ob is None or ob.expired or ob.step not in (
            FLOW_SIGNUP_STATE, FLOW_EMAIL_CODE_STATE, FLOW_PHONE_STATE,
            FLOW_PHONE_CODE_STATE, FLOW_PASSWORD_STATE, FLOW_PIN_STATE):
        return None
    if not hmac.compare_digest(sig, _sig(f"{_OB_PREFIX}{ob.id}:{ob.msisdn}")):
        return None
    return ob


def sign_identity_token(pa) -> str:
    """Signed handle for a KYC action sitting in its identity step. Prefixed like
    the onboarding token so the three token kinds can never be confused: each
    resolves through its own lookup and rejects the others' prefixes."""
    return f"{_ID_PREFIX}{pa.id}.{_sig(f'{_ID_PREFIX}{pa.id}:{pa.msisdn}')}"


def sign_approve_token(pa) -> str:
    """Signed hand-off for approving a pending WhatsApp money action in the app.

    The signature binds action id, number AND the owning user id: the token
    travels through a chat message and an OS deep link — surfaces we do not
    control — so even a token lifted whole must still fail unless it is redeemed
    from a session belonging to that exact user (the endpoint enforces the
    match; the binding here makes the check tamper-evident rather than
    row-lookup-dependent).
    """
    return (f"{_AP_PREFIX}{pa.id}."
            f"{_sig(f'{_AP_PREFIX}{pa.id}:{pa.msisdn}:{pa.user_id}')}")


def resolve_approve_token(token: str):
    """Return the live PendingAction for an app hand-off token, or None if it is
    malformed, forged, expired, or not sitting at a confirm step. Single-use is
    a property of execution, not of this lookup: completing (or cancelling) the
    action leaves no state this resolves in."""
    from .models import PendingAction

    raw = (token or "").strip()
    if not raw.startswith(_AP_PREFIX) or "." not in raw:
        return None
    head, _, sig = raw.partition(".")
    pid = head[len(_AP_PREFIX):]
    if not pid.isdigit():
        return None
    pa = PendingAction.objects.filter(id=int(pid)).first()
    if pa is None or pa.state not in _APPROVABLE_STATES or pa.expired:
        return None
    if not hmac.compare_digest(sig, _sig(f"{_AP_PREFIX}{pa.id}:{pa.msisdn}:{pa.user_id}")):
        return None
    return pa


def resolve_identity_token(token: str):
    """Return the live PendingAction for a signed identity flow_token, or None if
    it is malformed, forged, expired, or no longer in the identity Flow state."""
    from .models import PendingAction

    raw = (token or "").strip()
    if not raw.startswith(_ID_PREFIX) or "." not in raw:
        return None
    head, _, sig = raw.partition(".")
    pid = head[len(_ID_PREFIX):]
    if not pid.isdigit():
        return None
    pa = PendingAction.objects.filter(id=int(pid)).first()
    if pa is None or pa.state != FLOW_ID_STATE or pa.expired:
        return None
    if not hmac.compare_digest(sig, _sig(f"{_ID_PREFIX}{pa.id}:{pa.msisdn}")):
        return None
    return pa


def resolve_flow_token(token: str):
    """Return the live PendingAction for a signed flow_token, or None if the token
    is malformed, forged, expired, or no longer in the Flow-PIN state."""
    from .models import PendingAction

    raw = (token or "").strip()
    if "." not in raw:
        return None
    pid, _, sig = raw.partition(".")
    if not pid.isdigit():
        return None
    pa = PendingAction.objects.filter(id=int(pid)).first()
    if pa is None or pa.state not in (FLOW_PIN_STATE, FLOW_FORM_STATE, FLOW_VTU_STATE) or pa.expired:
        return None
    if not hmac.compare_digest(sig, _sig(f"{pa.id}:{pa.msisdn}")):
        return None
    return pa


# --------------------------------------------------------------------------- #
# screen builders
# --------------------------------------------------------------------------- #
def _pin_screen(summary, error: str = "", screen: str = PIN_SCREEN) -> dict:
    """`summary` is either the structured {amount, recipient, details, balance,
    narration} the money flows persist, or a bare string (the signup PIN, which
    has no payment to describe). Every declared field is always supplied — the
    screen renders all of them, so a missing one is a blank line rather than an
    omission, and an omission is a contract mismatch: the endpoint must answer
    with exactly the properties the published screen declares, or WhatsApp shows
    "Couldn't load content. Try again later." on every ending of the Flow.

    That is also why `balance` and `narration` default to empty strings rather
    than being left out on the paths that have neither — the signup PIN and the
    set-PIN pages share this screen and describe no payment at all.
    """
    fields = summary if isinstance(summary, dict) else {}
    heading = fields.get("amount") or (summary if isinstance(summary, str) else "")
    return {"screen": screen,
            "data": {"balance": fields.get("balance", ""),
                     "amount": heading or "Confirm your payment",
                     "recipient": fields.get("recipient", ""),
                     "details": fields.get("details", ""),
                     "narration": fields.get("narration", ""),
                     "error": error or ""}}


def _flow_screen(container, default: str) -> str:
    """The screen this Flow session is actually displaying.

    A session that opened on PIN_SCREEN and one that arrived at PIN_CHAIN from a
    form render the same thing, but returning the wrong id turns a re-render into
    a navigation — which Meta rejects when no route exists between them.
    """
    return (getattr(container, "payload", None) or {}).get("flow_screen") or default


def _pa_screen_fields(pa) -> dict:
    """The persisted screen fields for a pending action, falling back to the
    one-line summary for an action armed before the structured form existed."""
    return pa.payload.get("flow_fields") or pa.payload.get("flow_summary", "")


def _identity_screen(kind: str, error: str = "", summary: str = "", label: str = "",
                     screen: str = IDENTITY_SCREEN) -> dict:
    """The masked-entry screen. Defaults to the 11-digit BVN/NIN wording, but the
    email confirmation code rides the same screen — one published masked input,
    so every secret the ladder collects is entered the same way."""
    which = (kind or "BVN").upper()
    return {"screen": screen,
            "data": {"summary": summary or f"Enter your 11-digit {which}",
                     "label": label or which,
                     "error": error or ""}}


def _email_screen(error: str = "", summary: str = "", label: str = "") -> dict:
    """Address entry. Unmasked, unlike everything else here: an email address is
    not a secret, and masking one you have to type correctly only breeds typos."""
    return {"screen": EMAIL_SCREEN,
            "data": {"summary": summary or "What's your email address?",
                     "label": label or "Email address",
                     "error": error or ""}}


#: The heading the terminal screen closes on, per executor outcome. The Flow's
#: last screen used to render only the outcome SENTENCE, so a settled transfer, a
#: queued one and a refused one all looked alike at a glance — the customer had
#: to read a paragraph to find out whether their money had moved. The status is
#: the heading now and the sentence is the body.
_STATUS_HEADINGS = {
    "success": "✅ Successful",
    "pending": "⏳ Pending",
    "failed": "❌ Not completed",
    "done": "Done",
}


def result_screen_live() -> bool:
    """Is RESULT actually PUBLISHED on Meta's side yet?

    The Flow JSON and this code are a contract, and the publish is a manual step
    (`manage.py publish_flow --publish`) that does not run from a deploy. So the
    code can reach production before the screen does — and answering a screen
    Meta has never heard of is the "Couldn't load content. Try again later."
    failure, on exactly the endings this feature exists to improve.

    Defaults OFF for that reason: until someone confirms the publish, the
    endings behave exactly as they did before. Flip WHATSAPP_FLOW['RESULT_SCREEN']
    to true once `publish_flow` reports RESULT among the live screens.
    """
    return bool((getattr(settings, "WHATSAPP_FLOW", {}) or {}).get("RESULT_SCREEN", False))


def _result_screen(message: str, status: str = "") -> dict:
    """The outcome, with the panel left open. Same content as the terminal
    screen; the customer closes it by tapping Done.

    Falls back to the terminal screen while RESULT is not live — the panel
    closes as it used to, which is worse than holding open and far better than
    an unrenderable screen.
    """
    data = {"status": _STATUS_HEADINGS.get(status or "done", _STATUS_HEADINGS["done"]),
            "message": message or "Done"}
    if not result_screen_live():
        return {"screen": SUCCESS_SCREEN, "data": data}
    return {"screen": RESULT_SCREEN, "data": data}


def _success_screen(message: str, status: str = "") -> dict:
    """The terminal screen. `status` is one of router.Outcome's tags; anything
    else (including the default) closes on the neutral "Done" heading, which is
    right for the non-money terminals — an expired session, a signup that ended
    in the chat — that have no transaction outcome to report."""
    return {"screen": SUCCESS_SCREEN,
            "data": {"status": _STATUS_HEADINGS.get(status or "done", _STATUS_HEADINGS["done"]),
                     "message": message or "Done ✅"}}


# --------------------------------------------------------------------------- #
# request handler (decrypted payload -> response dict)
# --------------------------------------------------------------------------- #
def _screen_contract() -> dict:
    """screen id -> {data property: declared type} for the PUBLISHED Flow.

    Read from the shipped flow_assets/pin_flow.json, which is the same document
    uploaded to Meta, so the contract cannot drift from the one the device is
    holding us to. Empty on any read failure — a guard that cannot load its rules
    must not start refusing valid payments.

    The TYPE matters as much as the key: most properties are strings, but the
    Dropdown sources (`banks`, `plans`, `networks`) are arrays of objects. A guard
    that knew only the key names would happily stringify a bank list into
    "[{'id': 'gtb', ...}]" and break every screen it was meant to protect.
    """
    global _CONTRACT
    if _CONTRACT is None:
        try:
            import json
            import os

            path = os.path.join(os.path.dirname(__file__), "flow_assets", "pin_flow.json")
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            _CONTRACT = {
                s["id"]: {k: str((v or {}).get("type") or "string")
                          for k, v in (s.get("data") or {}).items()}
                for s in doc.get("screens", []) if s.get("id")}
        except Exception:  # noqa: BLE001 — never block a payment to validate one
            log.exception("could not read the Flow screen contract")
            _CONTRACT = {}
    return _CONTRACT


#: What an absent property becomes, per declared type. A declared-but-missing key
#: is as unrenderable as a wrong one, and a blank of the right shape is the only
#: substitute that renders.
_EMPTY_FOR = {"string": "", "array": [], "boolean": False, "number": 0}


def _conform(value, declared_type: str):
    """Coerce one value to its declared type, or to that type's blank."""
    if value is None:
        return _EMPTY_FOR.get(declared_type, "")
    if declared_type == "string":
        # str() rather than the value as-is: `Outcome` is a str subclass and
        # encodes fine, but a Decimal reaching a declared string property is the
        # same unrenderable screen by a different route.
        return str(value)
    if declared_type == "array":
        # Never stringify a Dropdown's source, and never hand it a non-list: an
        # empty list renders as an empty dropdown, which is recoverable; a string
        # where an array belongs takes the whole screen down.
        return value if isinstance(value, (list, tuple)) else []
    return value


_CONTRACT: dict | None = None


def _check_contract(response: dict) -> dict:
    """Force a response's data to match its screen's declaration, and log when it
    didn't.

    WhatsApp renders ANY mismatch — a missing declared property, an extra
    undeclared one, or a value that is not the declared type — as "Couldn't load
    content. Try again later." on the device. The customer has already entered
    their PIN by then, so the sentence they read is about a payment that in fact
    went through, and the device says nothing about which screen or which key.

    This used to only log, on the reasoning that filling in a value the caller
    forgot papers over the bug that produced it. That trade was wrong for a
    banking channel: papering over a missing subtitle costs a blank line, and
    NOT papering over it costs the customer the outcome of their payment. So the
    response is now conformed to the contract — declared-but-absent keys are
    filled with "", undeclared keys are dropped, and every value is coerced to a
    string — and the mismatch is still logged at ERROR with the exact key, so the
    underlying bug is as findable as it was before.

    Only the endpoint's half of the contract is enforceable here; `contract` is
    read from the repo's pin_flow.json, so this cannot detect a Flow that Meta
    has published one revision behind the code. `published_flow_report()` checks
    that other half, and its verdict is on /healthz as
    whatsapp_flow_published.drifted_screens.
    """
    contract = _screen_contract()
    screen = str(response.get("screen") or "")
    if not contract or screen not in contract:
        return response
    data = response.get("data") or {}
    declared = contract[screen]
    sent, expected = set(data.keys()), set(declared)
    if sent != expected:
        log.error("wa_flow_contract_mismatch screen=%s missing=%s unexpected=%s",
                  screen, sorted(expected - sent), sorted(sent - expected))
    response["data"] = {k: _conform(data.get(k), t) for k, t in declared.items()}
    return response


def handle_flow_request(payload: dict) -> dict:
    """Route a decrypted Flows request to its response. Never raises — any
    unexpected shape resolves to a safe terminal screen so the endpoint always
    returns a well-formed (encryptable) reply."""
    response = _check_contract(_handle_flow_request(payload))
    # One line per answered exchange. Without it the only trace of a Flow session
    # in Render was gunicorn's access log — a 200 and a byte count — which is why
    # "every request succeeded" and "the customer saw an error screen" were both
    # true and neither was diagnosable. No values are logged: the payload carries
    # balances, account names and the customer's own narration.
    log.info("wa_flow_response screen=%s keys=%s",
             response.get("screen") or "-", sorted((response.get("data") or {}).keys()))
    return response


def _handle_flow_request(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return _success_screen("Invalid request. Please start again in the chat.")
    action = payload.get("action", "")

    # Health check Meta fires against the endpoint.
    if action == "ping":
        return {"data": {"status": "active"}}

    data = payload.get("data", {}) or {}
    if not isinstance(data, dict):
        data = {}
    # Client-side error report (Meta convention) — just acknowledge.
    if data.get("error_message"):
        return {"data": {"acknowledged": True}}

    token = payload.get("flow_token", "")

    # A signup setting its PIN uses the same published screen, addressed by a
    # prefixed token. Handled first so an onboarding token never falls through
    # to the money-action lookup.
    if str(token).startswith(_OB_PREFIX):
        ob = resolve_onboarding_token(token)
        if ob is None:
            return _success_screen("This signup expired. Send us a message to start again.")
        if action == "data_exchange":
            # Which page submitted is the onboarding's STEP, not the shape of
            # the posted data — same rule as the money session's dispatch.
            if ob.step == FLOW_SIGNUP_STATE:
                return _submit_signup_details(ob, data)
            if ob.step == FLOW_EMAIL_CODE_STATE:
                return _submit_signup_email_code(ob, data)
            if ob.step == FLOW_PHONE_STATE:
                return _submit_signup_phone(ob, data)
            if ob.step == FLOW_PHONE_CODE_STATE:
                return _submit_signup_phone_code(ob, data)
            if ob.step == FLOW_PASSWORD_STATE:
                return _submit_signup_password(ob, data)
            return _submit_onboarding_pin(ob, data)
        if ob.step == FLOW_SIGNUP_STATE:
            return _signup_screen()
        if ob.step == FLOW_EMAIL_CODE_STATE:
            return _signup_email_code_screen(ob)
        if ob.step == FLOW_PHONE_STATE:
            return _signup_phone_screen()
        if ob.step == FLOW_PHONE_CODE_STATE:
            return _signup_phone_code_screen(ob)
        if ob.step == FLOW_PASSWORD_STATE:
            return _signup_password_screen()
        return (_confirm_pin_screen() if ob.payload.get("flow_pin_hash")
                else _pin_screen(_ob_summary(ob), screen=_flow_screen(ob, PIN_SCREEN)))

    # A KYC identity step. Same reasoning as the PIN: a BVN or NIN typed into the
    # chat stays in the customer's own history forever — WhatsApp has no
    # view-once for text and lets only the sender delete — so it is collected in
    # the encrypted Flow instead.
    if str(token).startswith(_ID_PREFIX):
        pa = resolve_identity_token(token)
        if pa is None:
            return _success_screen("This verification expired. Reply 8 in the chat to start again.")
        kind = pa.payload.get("id_kind", "BVN")
        if action == "data_exchange":
            if kind == "email":
                return _submit_email(pa, data)
            if kind == ACCOUNT_OTP:
                return _submit_account_otp(pa, data)
            # An armed challenge means this submit is the CODE, not the number:
            # the identity was accepted on the previous exchange of this session.
            if pa.payload.get("id_otp_hash"):
                return _submit_identity_otp(pa, data)
            return _submit_identity(pa, data)
        if kind == "email":
            return _email_step_screen(pa)
        if kind == ACCOUNT_OTP:
            return _account_otp_screen(pa)
        if pa.payload.get("id_otp_hash"):
            return _identity_otp_screen(pa)
        return _identity_screen(kind)

    if action == "INIT":
        # The money confirm opens through here (flow_action=data_exchange), so
        # this is the check that a re-tapped card gets: no live action, no PIN
        # pad. `resolve_flow_token` returns None for an action that is expired,
        # cancelled, mid-execution, or gone because the payment completed and the
        # executor cleared it — every one of which should end the card rather
        # than re-offer it.
        pa = resolve_flow_token(token)
        if pa is None:
            return _success_screen(
                "That payment is already done or has expired, so there's nothing "
                "to confirm. Check the chat for the receipt, or start again there.",
                status="failed")
        if pa.state == FLOW_FORM_STATE:
            return _transfer_form_screen()
        if pa.state == FLOW_VTU_STATE:
            return _vtu_screen_for(pa)
        if pa.action_type == "setpin":
            return _set_pin_screen(pa)
        return _open_pin_screen(pa)

    if action == "data_exchange":
        # Which screen submitted is the pending action's STATE, not the shape of
        # the data it posted. The form and the PIN pad are two exchanges on one
        # session, and sniffing for a field name would misroute the moment a
        # screen gains or loses one.
        pa = resolve_flow_token(token)
        if pa is not None and pa.state == FLOW_FORM_STATE:
            return _submit_transfer_form(token, data)
        if pa is not None and pa.state == FLOW_VTU_STATE:
            return _submit_vtu(pa, data)
        return _submit_pin(token, data)

    # BACK / unknown actions: re-render the PIN screen if we can, else a terminal.
    pa = resolve_flow_token(token)
    return (_pin_screen(_pa_screen_fields(pa), screen=_flow_screen(pa, PIN_SCREEN))
            if pa else _success_screen("Session ended."))


def _open_pin_screen(pa) -> dict:
    """The PIN pad for a FRESH open of a live payment.

    Two things this does that a re-render must not.

    It answers on PIN_SCREEN, always — never the `flow_screen` the session last
    sat on. PIN_CHAIN and PIN_RETRY are not routing roots, and Meta refuses a
    non-root as a Flow's first screen ("Specified screen X is not allowed as
    first screen of this flow", error 131009). A customer who mistyped their PIN,
    closed the card and tapped it again would otherwise be answered with
    PIN_RETRY and the card simply would not open.

    And it resets `flow_screen` to match, so the next error render navigates
    forward from where the device actually is rather than from where a previous
    session left off.

    The fields are rebuilt rather than replayed, so the balance shown is the one
    at OPEN time. Under `navigate` it was necessarily the balance at send time —
    which on a card tapped an hour later was simply a stale number on the screen
    the customer checks before spending.
    """
    from .router import _flow_fields

    fields = _flow_fields(pa)
    pa.payload["flow_fields"] = fields
    pa.payload["flow_screen"] = PIN_SCREEN
    pa.save(update_fields=["payload"])
    return _pin_screen(fields, screen=PIN_SCREEN)


def _confirm_pin_screen(error: str = "") -> dict:
    """Routing is forward-only, so a mismatch cannot send the customer back to
    PIN_SCREEN — the held first entry stays authoritative and the error says how
    to start over instead (cancel in the chat).

    An ERROR render answers with the retry twin. From PIN_CONFIRM that is a
    routed navigation and the masked box arrives EMPTY. From the twin itself it
    is a same-screen re-render, and the box does NOT arrive empty — WhatsApp
    keeps a form's client-side value on a same-id answer, so the digits that just
    failed are still in it. That second render is therefore capped by the CALLERS
    (_PIN_CONFIRM_ATTEMPTS), which is where the counting has to live: this
    function takes no container and cannot count."""
    return {"screen": PIN_CONFIRM_RETRY if error else PIN_CONFIRM,
            "data": {"amount": "Re-enter your PIN",
                     "recipient": "Type the same 6 digits again to confirm",
                     "details": "",
                     "error": error or ""}}


def _signup_screen(error: str = "") -> dict:
    # The refusal is flagged loudly: a same-screen re-render keeps the typed
    # values (right for visible fields), so without a marker "we didn't accept
    # that" reads as "nothing happened".
    return {"screen": SIGNUP_SCREEN, "data": {"error": f"⚠️ {error}" if error else ""}}


def _submit_signup_details(ob, data: dict) -> dict:
    """The signup form: names + email in ONE private screen, then straight into
    the PIN pair on the same open Flow — the whole signup with zero chat
    round-trips. The same validation the chat path applies, because two entry
    points must not disagree on what a valid signup is. The email is only
    COLLECTED here; the OTP round-trip still verifies it afterwards.
    """
    import re

    from accounts.models import User

    first = str(data.get("first_name", "")).strip()[:40]
    last = str(data.get("last_name", "")).strip()[:40]
    email = str(data.get("email", "")).strip().lower()
    if len(first) < 2 or len(last) < 2:
        return _signup_screen(error="Please enter your first and last name.")
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return _signup_screen(error="That doesn't look like an email address.")
    if User.objects.filter(email__iexact=email).exists():
        # Recovery looks accounts up by email; a duplicate would make reset
        # codes ambiguous — refused at entry, exactly like the chat path.
        return _signup_screen(error="That email is already on a Zitch account — use a different one.")
    ob.payload.update({"first_name": first, "last_name": last, "email": email})
    from .router import send_onboarding_email_code

    if send_onboarding_email_code(ob):
        ob.step = FLOW_EMAIL_CODE_STATE
        ob.save(update_fields=["payload", "step"])
        return _signup_email_code_screen(ob)
    # No email rail on this deploy: the address is kept unverified (the KYC
    # ladder re-verifies it later) and signup moves on rather than dead-ending.
    ob.step = FLOW_PHONE_STATE
    ob.save(update_fields=["payload", "step"])
    return _signup_phone_screen()


def _signup_email_code_screen(ob, error: str = "") -> dict:
    return {"screen": SIGNUP_EMAIL_CODE,
            "data": {"summary": f"We sent a 6-digit code to {ob.payload.get('email', 'your email')}.",
                     "error": f"⚠️ {error}" if error else ""}}


def _signup_phone_screen(error: str = "") -> dict:
    return {"screen": SIGNUP_PHONE,
            "data": {"error": f"⚠️ {error}" if error else ""}}


def _submit_signup_email_code(ob, data: dict) -> dict:
    """The email code, on the same open session that collected the address."""
    from .router import check_onboarding_email_code

    status, message = check_onboarding_email_code(ob, str(data.get("email_code", "")))
    if status == "retry":
        return _signup_email_code_screen(ob, error=message)
    # Verified, or attempts/expiry exhausted — either way the ladder moves on;
    # an unverified address is re-verified later, a dead end helps nobody.
    ob.step = FLOW_PHONE_STATE
    ob.save(update_fields=["payload", "step"])
    return _signup_phone_screen(error=message if status == "unverified" else "")


def _submit_signup_phone(ob, data: dict) -> dict:
    """The account phone number. Normalised to local 0XXXXXXXXXXX; refused with
    the reason when it is malformed or already on another account."""
    from accounts.models import User

    digits = "".join(ch for ch in str(data.get("phone", "")) if ch.isdigit())
    if digits.startswith("234") and len(digits) == 13:
        digits = "0" + digits[3:]
    if len(digits) != 11 or not digits.startswith("0"):
        return _signup_phone_screen(error="Enter the 11-digit number, e.g. 08012345678.")
    if User.objects.filter(phone=digits).exists() or User.objects.filter(username=digits).exists():
        return _signup_phone_screen(error="That number is already on a Zitch account — "
                                          "open the app to link it, or use another number.")
    ob.payload["phone"] = digits
    from .router import _local_phone, send_onboarding_phone_code

    if digits != _local_phone(ob.msisdn) and send_onboarding_phone_code(ob):
        # A number OTHER than the one they are chatting from: possession is not
        # proven by the session, so it gets the same code round-trip as the
        # email. The chat number itself needs no SMS — the chat is the phone.
        ob.step = FLOW_PHONE_CODE_STATE
        ob.save(update_fields=["payload", "step"])
        return _signup_phone_code_screen(ob)
    return _signup_to_pin(ob)


def signup_password_live() -> bool:
    """Is SIGNUP_PASSWORD actually PUBLISHED on Meta's side yet?

    Same gate, and the same hard-won reason, as result_screen_live(): the Flow
    JSON ships in this repo but is published by hand, so this code can reach
    production before the screen does — and answering with a screen Meta has
    never heard of is the "Couldn't load content. Try again later." failure, here
    in the middle of somebody's signup.

    Defaults OFF. While it is off, signup behaves exactly as it does today: the
    account is created with no app password, and the customer sets one with
    "Forgot password" in the app. Flip WHATSAPP_FLOW['PASSWORD_SCREEN'] on once
    `publish_flow` lists SIGNUP_PASSWORD among the live screens.
    """
    return bool((getattr(settings, "WHATSAPP_FLOW", {}) or {}).get("PASSWORD_SCREEN", False))


def _signup_password_screen(error: str = "") -> dict:
    return {"screen": SIGNUP_PASSWORD,
            "data": {"summary": "This is the password you'll use to sign in to "
                                "the Zitch app. Your PIN comes next.",
                     "error": f"⚠️ {error}" if error else ""}}


def _submit_signup_password(ob, data: dict) -> dict:
    """The app password, typed twice on one encrypted screen.

    Both boxes are on the same screen rather than across two round-trips (which
    is how the PIN does it) because a Flow screen can hold two masked inputs and
    the published PIN pad holds one. Typing it twice is the point: this is the
    credential the customer will next use days later, from a different device,
    with no way to see what they typed.

    What is NEVER done here: keep the password. It is validated, hashed, and only
    the hash is written to the onboarding row — so a signup abandoned halfway,
    or a database read by anyone, yields a hash and not a credential. The raw
    string exists for the length of this function and is never logged, never put
    on a screen, and never sent to the chat.
    """
    from accounts.models import password_rejection
    from django.contrib.auth.hashers import make_password

    password = str(data.get("password", ""))
    confirm = str(data.get("confirm_password", ""))
    if not password:
        return _signup_password_screen(error="Please choose a password.")
    # Mismatch first: telling someone their two entries differ is more useful
    # than critiquing the composition of one they may have mistyped.
    if password != confirm:
        return _signup_password_screen(error="Those two passwords don't match. Try again.")
    # The same rule the app's own endpoints apply — one function, so the two
    # front doors cannot disagree about what a valid password is.
    rejection = password_rejection(password, _ob_user_shape(ob))
    if rejection:
        return _signup_password_screen(error=rejection)
    ob.payload["flow_pw_hash"] = make_password(password)
    ob.step = FLOW_PIN_STATE
    ob.payload["flow_screen"] = PIN_CHAIN
    ob.save(update_fields=["payload", "step"])
    return _pin_screen(_ob_summary(ob), screen=PIN_CHAIN)


def _ob_user_shape(ob):
    """An unsaved User carrying just the signup's own details, so Django's
    similarity validator can refuse a password that IS the customer's name or
    email. Unsaved deliberately — the real account does not exist yet, and this
    must never touch the database."""
    from accounts.models import User

    return User(username=(ob.payload.get("phone") or ""),
                email=(ob.payload.get("email") or ""),
                first_name=(ob.payload.get("first_name") or ""),
                last_name=(ob.payload.get("last_name") or ""))


def _signup_to_pin(ob) -> dict:
    """The step after the phone is settled: the app password when that screen is
    published, otherwise straight to the PIN as before."""
    if signup_password_live() and not ob.payload.get("flow_pw_hash"):
        ob.step = FLOW_PASSWORD_STATE
        ob.save(update_fields=["step"])
        return _signup_password_screen()
    ob.payload["flow_screen"] = PIN_CHAIN   # SIGNUP pages route to PIN_CHAIN
    ob.step = FLOW_PIN_STATE
    ob.save(update_fields=["payload", "step"])
    return _pin_screen(_ob_summary(ob), screen=PIN_CHAIN)


def _signup_phone_code_screen(ob, error: str = "") -> dict:
    masked = f"•••••{(ob.payload.get('phone') or '')[-4:]}"
    return {"screen": SIGNUP_PHONE_CODE,
            "data": {"summary": f"We sent a 6-digit code by SMS to {masked}.",
                     "error": f"⚠️ {error}" if error else ""}}


def _submit_signup_phone_code(ob, data: dict) -> dict:
    """The SMS code proving the typed number is really theirs."""
    from .router import check_onboarding_phone_code

    status, message = check_onboarding_phone_code(ob, str(data.get("phone_code", "")))
    if status == "retry":
        return _signup_phone_code_screen(ob, error=message)
    # Verified, or attempts/expiry exhausted — the ladder moves on either way;
    # an unverified number gets the KYC ladder's SMS round-trip later.
    return _signup_to_pin(ob)


def _ob_summary(ob) -> str:
    return ("Re-enter your new PIN to confirm" if ob.payload.get("flow_pin_hash")
            else "Create a 6-digit PIN to authorise payments")


def _submit_onboarding_pin(ob, data: dict) -> dict:
    """Set-then-confirm across two data_exchange round-trips on the SAME published
    screen, so the signup PIN is typed into the encrypted Flow and never becomes a
    chat message. The first submit holds only a hash; the second must match it."""
    from accounts.models import transaction_pin_rejection
    from django.contrib.auth.hashers import check_password, make_password

    from .router import finish_onboarding_from_flow

    pin = str(data.get("pin", "")).strip()
    held = ob.payload.get("flow_pin_hash") or ""
    if not held:
        rejected = transaction_pin_rejection(pin)
        if rejected:
            # Land on the empty twin, not a re-render of the screen we are on.
            # WhatsApp keeps a form's client-side value whenever the endpoint
            # answers with the SAME screen id, so re-rendering here would leave
            # the refused PIN sitting in a box the customer cannot see and one
            # Confirm tap would resubmit the identical weak PIN, looping on the
            # same error. This is the same reason wrong-PIN uses PIN_RETRY and
            # confirm-mismatch uses PIN_CONFIRM_RETRY; the create step was the
            # one masked-PIN error path still missing it. PIN_RETRY is a legal
            # forward route from both PIN_SCREEN and PIN_CHAIN and itself routes
            # on to PIN_CONFIRM, so the confirm step stays reachable.
            #
            # And only ONCE. transaction_pin_rejection is a policy check with no
            # counter of its own, so a third refusal would answer PIN_RETRY onto
            # PIN_RETRY and retain the digits — with nothing to stop it, on a
            # container that lives for ONBOARD_TTL (15 minutes), which makes this
            # the most reachable instance of the retained-box trap in the module,
            # not the least.
            tries = int(ob.payload.get("pin_policy_tries", 0)) + 1
            if tries >= _PIN_CREATE_ATTEMPTS:
                # Budget spent for THIS session. Reset it rather than persisting
                # the exhaustion: the cap exists to stop a render loop inside one
                # open form, and client-side field state dies with the session —
                # so re-tapping the card is a genuinely fresh, empty start and
                # should get a fresh budget. Persisting the count would leave the
                # signup row alive (below) pointing at a card that terminates on
                # the customer's very next keystroke, forever.
                ob.payload["pin_policy_tries"] = 0
                ob.save(update_fields=["payload"])
                # No _clear_actions: this holds a WaOnboarding, not a
                # PendingAction, and that is not its teardown. Left intact, the
                # signup resumes from the card or expires on its own TTL.
                return _success_screen("That PIN isn't one we can accept — it can't be six "
                                       "of the same digit or a run like 123456. Tap the "
                                       "secure screen above to try again.")
            ob.payload["pin_policy_tries"] = tries
            ob.payload["flow_screen"] = PIN_RETRY
            ob.save(update_fields=["payload"])
            return _pin_screen(_ob_summary(ob), error=rejected.rstrip(".") + ".",
                               screen=PIN_RETRY)
        ob.payload["flow_pin_hash"] = make_password(pin)   # never the raw PIN
        ob.save(update_fields=["payload"])
        return _confirm_pin_screen()

    if not check_password(pin, held):
        # Capped for the same reason as the create step: the first mismatch
        # navigates PIN_CONFIRM -> PIN_CONFIRM_RETRY and arrives empty, but a
        # second would answer PIN_CONFIRM_RETRY onto itself and hold the digits
        # that just failed — in a field the customer cannot read to correct.
        # The counting lives here because _confirm_pin_screen takes no container.
        tries = int(ob.payload.get("pin_confirm_tries", 0)) + 1
        if tries >= _PIN_CONFIRM_ATTEMPTS:
            # Reset the session budget, and drop the held first entry too: the
            # customer could not reproduce it, so keeping it authoritative would
            # make the next session unwinnable. Re-tapping the card starts the
            # create-then-confirm pair over from an empty box.
            ob.payload["pin_confirm_tries"] = 0
            ob.payload.pop("flow_pin_hash", None)
            ob.save(update_fields=["payload"])
            return _success_screen("Those didn't match. Tap the secure screen above to "
                                   "choose your PIN again.")
        ob.payload["pin_confirm_tries"] = tries
        ob.save(update_fields=["payload"])
        return _confirm_pin_screen(error="Those didn't match — enter the same PIN you "
                                         "chose on the first screen, or reply \"cancel\" "
                                         "in the chat to start over.")

    try:
        message = finish_onboarding_from_flow(ob, pin)
    except Exception:  # noqa: BLE001 — never leak a stack into the Flow
        log.exception("onboarding flow completion failed for ob=%s", ob.id)
        return _success_screen("Something went wrong finishing your signup. Send us a message to try again.")
    return _success_screen(message)


def _submit_identity(pa, data: dict) -> dict:
    """Take a BVN/NIN from the encrypted Flow and hand it to the same verification
    the chat path uses — one implementation, so the two entry points cannot drift
    on what counts as valid, what gets hashed, or what is queued for review.

    The number is never echoed back into a screen, and never reaches the chat.
    """
    import re

    from .router import _MAX_ID_ATTEMPTS, _account_submit_identity, _kyc_submit_identity

    kind = pa.payload.get("id_kind", "bvn")
    number = "".join(ch for ch in str(data.get("number", "")) if ch.isdigit())
    if not re.fullmatch(r"\d{11}", number):
        # The retry twin, so the masked box comes back empty — same reasoning as
        # a rejected number, minus the attempt: a typo is not a verdict.
        pa.payload["flow_screen"] = IDENTITY_RETRY
        pa.save(update_fields=["payload"])
        return _identity_screen(kind, error="That should be exactly 11 digits.",
                                screen=IDENTITY_RETRY)

    try:
        # Both entry points collect the same number on the same screen; what
        # happens next is the action's business, not this module's.
        if pa.action_type == "add_account":
            outcome = _account_submit_identity(pa, pa.user, pa.msisdn, number,
                                               in_flow=True)
            if outcome == "otp":
                # The bank accepted the ID and sent its SMS code — collected on
                # the NEXT PAGE of this same session, not a second flow message.
                pa.refresh_from_db()
                return _account_otp_screen(pa)
            if outcome == "fail":
                # A hard failure: the ID was refused, name-matched to a different
                # person, or the provider was unreachable. _account_submit_identity
                # has already cleared the pending action and sent a "⚠️ ..." line
                # to the chat. Falling through to the shared "received ✅" screen
                # would close the secure Flow on a green success the chat is
                # simultaneously contradicting. Unlike the KYC branch, there is no
                # review queue here that would make "received" true.
                return _success_screen(
                    "We couldn't finish setting up your account — see the chat for what happened.")
        else:
            outcome = _kyc_submit_identity(pa, pa.user, pa.msisdn, kind, number)
            if outcome == "invalid":
                # A wrong number is corrected by the customer, not queued — but
                # on a FRESH screen, so the refused digits are gone and the retry
                # is a real retry rather than a resubmit of the same number.
                pa.refresh_from_db()
                left = _MAX_ID_ATTEMPTS - int(pa.payload.get("id_bad_attempts") or 0)
                pa.payload["flow_screen"] = IDENTITY_RETRY
                pa.save(update_fields=["payload"])
                return _identity_screen(
                    kind, screen=IDENTITY_RETRY,
                    error=f"That {kind.upper()} isn't valid for this account. "
                          f"Check the digits and try again ({left} attempt(s) left).")
            if outcome == "stop":
                return _success_screen(
                    f"Too many incorrect {kind.upper()} attempts. Reply 8 in the chat to "
                    "start again.")
            if outcome == "otp":
                # The lookup passed and a code is on its way to the line
                # REGISTERED AGAINST THE IDENTITY — not the account's own
                # number. Collected on the same open session, so the code never
                # becomes a chat message either.
                pa.refresh_from_db()
                return _identity_otp_screen(pa)
    except Exception:  # noqa: BLE001 — never leak a stack into the Flow
        log.exception("identity flow submission failed for pa=%s", pa.id)
        return _success_screen("Something went wrong saving that. Reply 8 in the chat to try again.")
    # The chat carries the detailed outcome (verified, or queued for review), so
    # this screen only has to close cleanly.
    return _success_screen(f"{kind.upper()} received ✅ — see the chat for what's next.")


def _identity_otp_screen(pa, error: str = "") -> dict:
    """The identity challenge code. Always the chained twin: this is only ever
    reached from IDENTITY_SCREEN inside one session, never opened on."""
    kind = (pa.payload.get("id_otp_kind") or "bvn").upper()
    return _identity_screen(kind, error=error, label=f"{kind} code",
                            summary=f"Enter the 6-digit code we sent to "
                                    f"{pa.payload.get('id_otp_to', 'your phone')}",
                            screen=CODE_RETRY if error else IDENTITY_CHAIN)


def _submit_identity_otp(pa, data: dict) -> dict:
    """The code that proves control of the line the identity is registered to."""
    from .router import kyc_flow_identity_otp

    try:
        status, message = kyc_flow_identity_otp(pa, str(data.get("number", "")))
    except Exception:  # noqa: BLE001 — never leak a stack into the Flow
        log.exception("identity otp submission failed for pa=%s", pa.id)
        return _success_screen("Something went wrong. Reply 8 in the chat to try again.")
    if status == "retry":
        return _identity_otp_screen(pa, error=message)
    if status == "ok":
        from .router import _kyc_next

        try:
            _kyc_next(pa, pa.user, pa.msisdn)   # the chat says what remains
        except Exception:  # noqa: BLE001
            log.exception("kyc advance failed after identity otp for pa=%s", pa.id)
    return _success_screen(message)


def _account_otp_screen(pa, error: str = "") -> dict:
    """Clean render: whichever code page this session is on (IDENTITY_CHAIN when
    chained from the BVN entry, CODE_SCREEN when opened fresh). Error render:
    always CODE_RETRY — legal from both and from itself, and the masked box
    arrives empty instead of holding the code that just failed."""
    screen = CODE_RETRY if error else _flow_screen(pa, CODE_SCREEN)
    return _identity_screen(ACCOUNT_OTP, error=error, label="SMS code",
                            summary="Enter the code we sent to your phone",
                            screen=screen)


def _submit_account_otp(pa, data: dict) -> dict:
    """The bank's SMS code, entered here rather than in the chat.

    It completes account creation and is what name-matches the BVN, so it is a
    bearer credential for as long as it lives — the same reason the email code
    moved off the thread. Collecting the BVN privately and then asking for the
    code that unlocks it in clear would have been half a fix.
    """
    from .router import account_flow_otp

    code = str(data.get("number", "")).strip()
    try:
        status, message = account_flow_otp(pa, code)
    except Exception:  # noqa: BLE001 — never leak a stack into the Flow
        log.exception("account otp flow submission failed for pa=%s", pa.id)
        return _success_screen("Something went wrong. Reply 6 in the chat to try again.")
    if status == "retry":
        return _account_otp_screen(pa, error=message)
    return _success_screen(message)


def _email_code_screen(pa, error: str = "") -> dict:
    # Reached two ways: chained from EMAIL_SCREEN inside one session (so
    # IDENTITY_CHAIN, which EMAIL_SCREEN routes to), or as its own flow message
    # when the address was already known (so the CODE_SCREEN root). An error
    # render always answers CODE_RETRY so the box comes back empty.
    if error:
        screen = CODE_RETRY
    elif _flow_screen(pa, CODE_SCREEN) in (EMAIL_SCREEN, IDENTITY_CHAIN):
        screen = IDENTITY_CHAIN
    else:
        screen = CODE_SCREEN
    return _identity_screen("email", error=error, label="Email code",
                            summary=f"Enter the 6-digit code we sent to {pa.user.email}",
                            screen=screen)


def _email_step_screen(pa, error: str = "") -> dict:
    """Whichever half of the email step this action is sitting in."""
    if pa.payload.get("id_step") == "code":
        return _email_code_screen(pa, error=error)
    return _email_screen(error=error)


def _submit_email(pa, data: dict) -> dict:
    """The email half of the KYC ladder, run in the encrypted Flow exactly like
    BVN and NIN: the address is entered here, the 6-digit code comes back here,
    and neither ever becomes a chat message.

    Both halves delegate to the router, so the Flow and the chat fallback agree
    on what a valid address is, who already owns it, and how many wrong codes
    end the attempt.
    """
    from .router import kyc_flow_email_address, kyc_flow_email_code

    step = pa.payload.get("id_step", "address")
    value = str(data.get("number", "")).strip()
    verdict = kyc_flow_email_address if step == "address" else kyc_flow_email_code
    try:
        status, message = verdict(pa, value)
    except Exception:  # noqa: BLE001 — never leak a stack into the Flow
        log.exception("email flow submission failed for pa=%s step=%s", pa.id, step)
        return _success_screen("Something went wrong. Reply 8 in the chat to try again.")

    if status == "stop":
        return _success_screen(message)
    if status == "retry":
        return _email_step_screen(pa, error=message)
    # Accepted. The address half moves on to the code on the same open Flow; the
    # code half is the end of the email step, and the chat says what comes next.
    if step == "address":
        return _email_code_screen(pa)
    return _success_screen("Email verified ✅ — see the chat for what's next.")


#: What a narration may be before it is stored. The bank rail puts this on the
#: beneficiary's statement and the receipt renders it into an image, so it is
#: bounded here rather than at either consumer: 60 characters (the Flow field's
#: own max-chars, restated because the chat and AI paths do not pass through it),
#: single-line, and printable.
NARRATION_MAX = 60


def clean_narration(raw) -> str:
    """Normalise a customer-supplied narration, or "" if there is nothing usable.

    Optional everywhere, so "" is an ordinary answer and never an error. Newlines
    and control characters are collapsed rather than rejected: they arrive from
    voice notes and pasted text, they break both the statement narration and the
    rendered receipt, and refusing a payment over whitespace would be a strange
    place to be strict.
    """
    text = str(raw or "")
    # Control characters (including the newlines a dictated note arrives with)
    # become spaces, then runs of whitespace collapse to one.
    text = "".join(" " if ch < " " or ch == "\x7f" else ch for ch in text)
    return " ".join(text.split())[:NARRATION_MAX].strip()


def _close_in_chat(msisdn: str, text: str) -> None:
    """Say in the THREAD what the Flow's terminal screen just said.

    The terminal screen is not a reliable place to have told someone something.
    It renders inside Meta's panel, it is gone the moment the customer taps Done
    or swipes the sheet away, and when the data-exchange answer does not reach
    the device at all — a timeout, a dropped connection, a contract mismatch —
    WhatsApp replaces it with "Couldn't load content. Try again later.", which
    names no outcome and is indistinguishable from every other Flow failure.

    Every ending that MOVED money already writes to the chat from its executor
    (the receipt, or the failure line). The endings that did not were exactly the
    ones a customer is most likely to misread as "nothing happened": a wrong PIN
    that cancelled the payment, a lockout, a PIN that was never set. Those closed
    the panel and left the thread silent, so the last thing in the customer's
    history was the confirm card — still sitting there, still looking live.

    Best-effort by design: a send that fails must not turn a settled outcome into
    an exception on the endpoint thread, where the only thing left to answer with
    is the error screen this exists to make redundant.
    """
    from .router import reply

    try:
        reply(msisdn, text)
    except Exception:  # noqa: BLE001 — the screen is still the primary answer
        log.exception("could not mirror flow outcome to chat for %s", msisdn)


def _hold_open(pa, summary, message: str, status: str = "failed") -> dict:
    """Show `message` WITHOUT closing the Flow.

    Only a settled success may close the panel by itself. Everything else —
    pending, failed, a spent PIN budget, a lockout — lands on RESULT, which
    carries the outcome and a Done button, so the customer reads it where they
    were already looking and dismisses it when they have.

    RESULT rather than a re-render of the PIN page: WhatsApp keeps a form's
    client-side value on a same-id reply, so the outcome used to arrive with the
    six rejected digits still in a box fixed at 6/6 — which then refuses new
    keystrokes and reads as broken. RESULT has no form on it at all.

    `summary` is unused now and kept in the signature because the callers hold
    it for the screens that DO re-render a payment card.
    """
    del summary  # see docstring
    return _result_screen(message, status=status)


def _submit_pin(token: str, data: dict) -> dict:
    from common.http import evaluate_transaction_pin

    from .router import PIN_FLOW_ATTEMPTS, _clear_actions, authorise_flow_execution

    pa = resolve_flow_token(token)
    if pa is None:
        return _success_screen("This request expired or was already completed. Start again "
                               "in the chat.", status="failed")

    user = pa.user
    pin = str(data.get("pin", "")).strip()
    summary = _pa_screen_fields(pa)

    if pa.action_type == "setpin":
        if pa.payload.get("pin_reset_otp_hash"):
            # The reset's code page posts its digits as "number" (the code
            # screens' field); the PIN pages post "pin". While the code is
            # unproven, the submission IS the code.
            return _submit_pin_reset_code(pa, user, str(data.get("number", "") or pin))
        return _submit_new_pin(pa, user, pin)

    ok, code, message = evaluate_transaction_pin(user, pin)
    if not ok:
        # Lockout is enforced inside evaluate_transaction_pin (row-locked, shared
        # with the app/chat), so the PIN can't be brute-forced through the Flow.
        if code == "pin_locked":
            _clear_actions(pa.msisdn)
            # On the repeat (24-hour) lock the shared message offers a reset;
            # here it also has to say what to TYPE. The Flow is closing, so the
            # instruction has to point back at the thread that outlives it —
            # and asterisks are chat markdown, not Flow markup.
            if user.pin_lock_is_escalated:
                message += " Reply \"reset pin\" in the chat to choose a new one."
            _close_in_chat(pa.msisdn, f"🔒 {message}")
            return _result_screen(message, status="failed")
        if code == "no_pin":
            # Unsatisfiable, and — unlike a wrong PIN — uncounted: the branch
            # returns before evaluate_transaction_pin's atomic block, so it never
            # touches pin_failed_attempts and can never reach the lockout that
            # ends every other failing path. Re-rendering the pad would loop on
            # one screen id until the token expired, for a PIN that does not
            # exist and that no number of retries can conjure.
            _clear_actions(pa.msisdn)
            no_pin = ("You don't have a transaction PIN yet — reply "
                      "\"set pin\" in the chat to create one.")
            _close_in_chat(pa.msisdn, f"❌ That payment was cancelled. {no_pin}")
            return _result_screen(no_pin, status="failed")
        # One retry, then cancel: the budget the chat rung already enforces
        # (PIN_FLOW_ATTEMPTS, spec §7), and a SCREEN budget as much as a policy
        # one. Attempt 1 answers PIN_RETRY — a different id, so the pad arrives
        # empty. A second wrong PIN would have to answer PIN_RETRY *onto*
        # PIN_RETRY, and WhatsApp keeps a form's client-side value whenever the
        # endpoint answers with the SAME screen id. That leaves the refused
        # digits in a box the customer cannot read, one Confirm tap from
        # resubmitting them — and because the field is min-chars/max-chars 6/6,
        # a retained six-character value REFUSES new keystrokes until six
        # invisible characters are deleted. The box reads as broken, and the
        # customer burns attempts 3-5 on it into a one-hour cross-channel
        # lockout they never typed. So we stop instead of re-rendering.
        #
        # This does not cost the customer attempts: every wrong PIN still
        # increments the shared counter exactly as before, so the budget before
        # lockout is unchanged. It caps the attempts spendable in ONE session,
        # and the customer restarts in the chat with a fresh window.
        tries = int(pa.payload.get("flow_pin_tries", 0)) + 1
        if tries >= PIN_FLOW_ATTEMPTS:
            _clear_actions(pa.msisdn)
            cancelled = (f"{message} Cancelled for your safety — start the "
                         "payment again in the chat.")
            # The one wrong-PIN ending that gets a chat line. The RETRY render
            # below deliberately does not: the pad itself is showing the error,
            # the customer is still looking at it, and a message per attempt
            # would bury the thread in duplicates of what is already on screen.
            # This branch is different — the panel is closing and the payment is
            # gone, which is not something to learn by noticing an absence.
            _close_in_chat(pa.msisdn, f"❌ {cancelled}")
            return _result_screen(cancelled, status="failed")
        # Keep writing flow_screen: INIT/BACK re-render _flow_screen(pa, PIN_SCREEN),
        # and answering PIN_SCREEN while the device sits on PIN_RETRY would be a
        # backward navigation, which Meta refuses outright.
        #
        # A NEW key, not `pin_attempts`: that one belongs to _flow_pin_ok on the
        # chat rung and shares this cap, so reusing it would halve the budget for
        # a session that ever crossed rungs.
        pa.payload["flow_pin_tries"] = tries
        pa.payload["flow_screen"] = PIN_RETRY
        pa.save(update_fields=["payload"])
        return _pin_screen(summary, error=message, screen=PIN_RETRY)

    try:
        # Hands off to the queue in production and runs in-process in dev/test —
        # either way this returns fast enough for Meta's 10-second data-exchange
        # deadline, which executing a payout inline did not.
        outcome = authorise_flow_execution(pa, user)
    except Exception:  # never leak a stack to the Flow; the money paths are idempotent
        log.exception("flow execution failed for pa=%s", pa.id)
        _clear_actions(pa.msisdn)
        broke = ("Something went wrong completing that. If you were charged it "
                 "will auto-reverse.")
        _close_in_chat(pa.msisdn, f"❌ {broke}")
        return _hold_open(pa, summary, broke)
    # `status` is carried on the returned line itself (router.Outcome), so an
    # executor that has not been tagged yet still closes on the neutral heading
    # rather than claiming an outcome nobody established.
    status = getattr(outcome, "status", "")
    # ONLY a settled success closes the panel. A Flow that closes itself is a
    # statement that the job is done, and on a pending or failed payment that is
    # the wrong thing to say — the customer is left looking at their own chat
    # thread trying to work out whether their money moved. Everything else holds
    # the panel open with the outcome written on it, and the customer dismisses
    # it when they have read it.
    if status == "success":
        return _success_screen(outcome, status)
    # Pending keeps its own heading — "⏳ Pending" is the true thing to show a
    # customer whose money is with the rail, and calling it a failure would be
    # as wrong as calling it done.
    return _hold_open(pa, summary, str(outcome), status=status or "pending")


# --------------------------------------------------------------------------- #
# airtime / data — one Flow, four pages
# --------------------------------------------------------------------------- #
#: The page a VTU session is on. Kept on the payload rather than inferred from
#: the posted field names, for the same reason the transfer form is: sniffing
#: for a key misroutes the moment a screen gains or loses one.
VTU_KIND_STEP = "kind"
VTU_NETWORK_STEP = "network"
VTU_DETAILS_STEP = "details"


def _vtu_kind_screen(error: str = "") -> dict:
    return {"screen": VTU_SCREEN, "data": {"error": error or ""}}


def _vtu_network_screen(kind: str, error: str = "") -> dict:
    from .router import NETWORK_NAMES

    return {"screen": VTU_NETWORK,
            "data": {"networks": [{"id": k, "title": v} for k, v in NETWORK_NAMES.items()],
                     "summary": "Airtime top-up" if kind == "airtime" else "Data bundle",
                     "error": error or ""}}


def _vtu_airtime_screen(net_name: str, error: str = "", phone: str = "") -> dict:
    return {"screen": VTU_AIRTIME,
            "data": {"summary": f"{net_name} airtime", "phone": phone or "",
                     "error": error or ""}}


def _vtu_data_screen(net: str, net_name: str, error: str = "", phone: str = "") -> dict:
    """The plan list is the NETWORK's, fetched at render time — a plan picked
    from another network's list is not a typo the provider can recover from.

    Returns the TERMINAL screen when the network has no plans. A Flow Dropdown
    bound to an empty array does not render: the customer gets a blank panel
    with a spinner and no way forward but the X, which is indistinguishable from
    the product being broken. Refusing here rather than at the one call site
    that remembered to check means the INIT/BACK re-render and the error
    re-render cannot reintroduce it.
    """
    from utility.models import DataPlan

    from .router import _money

    plans = list(DataPlan.objects.filter(network=net, active=True)[:20])
    if not plans:
        log.warning("wa_vtu_data_no_plans net=%s", net)
        return _success_screen(
            f"No {net_name} data plans are available right now. Please try again shortly.",
            status="failed")
    return {"screen": VTU_DATA,
            "data": {"plans": [{"id": p.plan_code,
                                "title": f"{p.name} • {p.validity} • {_money(p.price)}"}
                               for p in plans],
                     "summary": f"{net_name} data", "phone": phone or "",
                     "error": error or ""}}


def _vtu_screen_for(pa) -> dict:
    """Re-render whichever page this session is standing on (INIT / BACK)."""
    from .router import NETWORK_NAMES, _own_phone

    step = pa.payload.get("vtu_step", VTU_KIND_STEP)
    kind = pa.payload.get("vtu_kind", "airtime")
    if step == VTU_KIND_STEP:
        return _vtu_kind_screen()
    if step == VTU_NETWORK_STEP:
        return _vtu_network_screen(kind)
    net = pa.payload.get("net", "1")
    net_name = NETWORK_NAMES.get(net, "")
    phone = pa.payload.get("phone") or _own_phone(pa.user) or ""
    return (_vtu_airtime_screen(net_name, phone=phone) if kind == "airtime"
            else _vtu_data_screen(net, net_name, phone=phone))


def _submit_vtu(pa, data: dict) -> dict:
    """One page of the airtime/data ladder. Dispatches on the session's STEP."""
    step = pa.payload.get("vtu_step", VTU_KIND_STEP)
    if step == VTU_KIND_STEP:
        return _submit_vtu_kind(pa, data)
    if step == VTU_NETWORK_STEP:
        return _submit_vtu_network(pa, data)
    return _submit_vtu_details(pa, data)


def _submit_vtu_kind(pa, data: dict) -> dict:
    from .router import _touch

    kind = str(data.get("kind", "")).strip().lower()
    if kind not in ("airtime", "data"):
        return _vtu_kind_screen(error="⚠️ Pick airtime or data.")
    pa.payload["vtu_kind"] = kind
    pa.payload["vtu_step"] = VTU_NETWORK_STEP
    pa.action_type = kind
    _touch(pa, payload=pa.payload)
    pa.save(update_fields=["action_type"])
    return _vtu_network_screen(kind)


def _submit_vtu_network(pa, data: dict) -> dict:
    from .router import NETWORK_NAMES, _clear_actions, _own_phone, _touch

    net = str(data.get("network", "")).strip()
    if net not in NETWORK_NAMES:
        return _vtu_network_screen(pa.payload.get("vtu_kind", "airtime"),
                                   error="⚠️ Pick a network from the list.")
    kind = pa.payload.get("vtu_kind", "airtime")
    net_name = NETWORK_NAMES[net]
    phone = pa.payload.get("phone") or _own_phone(pa.user) or ""
    if phone:
        pa.payload["phone"] = phone
    if kind == "data":
        # _vtu_data_screen answers the terminal screen itself when the network
        # has no plans (an empty Dropdown does not render), so the only thing
        # left to do here is drop the action behind it.
        screen = _vtu_data_screen(net, net_name, phone=phone)
        if screen["screen"] == SUCCESS_SCREEN:
            _clear_actions(pa.msisdn)
            return screen
    pa.payload["net"] = net
    pa.payload["vtu_step"] = VTU_DETAILS_STEP
    _touch(pa, payload=pa.payload)
    return screen if kind == "data" else _vtu_airtime_screen(net_name, phone=phone)


def _submit_vtu_details(pa, data: dict) -> dict:
    """Amount + phone (airtime) or plan + phone (data), then straight into the
    PIN page of the SAME session. Every check the chat ladder makes is made
    here — minimum, limits, balance — because two entry points must not
    disagree about what a valid purchase is.
    """
    from decimal import Decimal, InvalidOperation

    from common.http import daily_limit_error, send_limit_error
    from utility.models import DataPlan

    from .router import (MIN_AIRTIME, NETWORK_NAMES, _clear_actions, _flow_fields,
                         _insufficient, _money, _own_phone, _phone_from, _touch)
    from wallet.services import get_or_create_wallet

    user = pa.user
    kind = pa.payload.get("vtu_kind", "airtime")
    net = pa.payload.get("net", "1")
    net_name = NETWORK_NAMES.get(net, "")

    def refuse(message):
        phone_value = pa.payload.get("phone") or _own_phone(user) or ""
        return (_vtu_airtime_screen(net_name, error=f"⚠️ {message}", phone=phone_value)
                if kind == "airtime" else
                _vtu_data_screen(net, net_name, error=f"⚠️ {message}", phone=phone_value))

    phone = _phone_from(str(data.get("phone") or pa.payload.get("phone") or ""), user)
    if not phone:
        return refuse("Enter a valid phone number.")

    if kind == "airtime":
        try:
            amount = Decimal(str(data.get("amount", "")).strip())
        except InvalidOperation:
            return refuse("Enter the amount as a number, e.g. 500.")
        if amount < MIN_AIRTIME:
            return refuse(f"Minimum airtime is NGN {MIN_AIRTIME:,.0f}.")
        meta = {"phone": phone, "network": net}
        extra = {}
    else:
        plan = DataPlan.objects.filter(plan_code=str(data.get("plan", "")).strip(),
                                       network=net, active=True).first()
        if plan is None:
            return refuse("Pick a plan from the list.")
        amount = plan.price
        meta = {"phone": phone, "network": net, "plan_code": plan.plan_code}
        extra = {"plan_code": plan.plan_code, "price": str(plan.price), "plan_name": plan.name}

    # Airtime and data accrue against the BILL cap, not the transfer one.
    limit_msg = send_limit_error(user, amount) or daily_limit_error(user, amount, "bill")
    if limit_msg:
        _clear_actions(pa.msisdn)
        return _success_screen(limit_msg, status="failed")
    if _insufficient(user, amount):
        return refuse(f"Insufficient balance — you have {_money(get_or_create_wallet(user).balance)}.")
    if not user.transaction_pin:
        # Same refusal the transfer form makes: a PIN pad the customer can never
        # satisfy is worse than a clear "set one first".
        _clear_actions(pa.msisdn)
        return _success_screen(
            "You haven't set a transaction PIN yet — it's what authorises payments here "
            "and in the Zitch app. Close this and reply \"set pin\" in the chat, then try "
            "again.", status="failed")

    pa.payload.update({"phone": phone, "amount": str(amount), "meta": meta,
                       "pin_attempts": 0,
                       "narration": clean_narration(data.get("narration")), **extra})
    # Built from the payload rather than assembled here, so the confirm screen
    # gains the balance and the narration on this path too — an inline dict would
    # ship a response missing properties the published screen declares.
    fields = _flow_fields(pa)
    pa.payload["flow_fields"] = fields
    pa.payload["flow_screen"] = PIN_CHAIN   # routed into, so never the root
    _touch(pa, state=FLOW_PIN_STATE, payload=pa.payload)
    return _pin_screen(fields, screen=PIN_CHAIN)


def _transfer_form_screen(error: str = "", candidates=None, query: str = "",
                          hint: str = "") -> dict:
    """`hint` is the server talking back about a narrowing, which is not an error
    and must not read like one. Both are always supplied — the screen declares
    them, and a declared-but-absent property is the mismatch WhatsApp renders as
    "Couldn't load content. Try again later."."""
    from .router import _bank_items

    return {"screen": TRANSFER_FORM,
            "data": {"banks": _bank_items(candidates, query=query),
                     "error": error or "", "hint": hint or ""}}


def _submit_transfer_form(token: str, data: dict) -> dict:
    """The transfer form: amount + account + (optional) bank in one private
    screen. The server verifies everything the chat interrogation verified —
    minimum, limits, balance, and the name enquiry — then chains into the PIN
    screen on the same session, showing WHO the money is going to.

    Bank auto-detect is deliberately a suggestion: the NUBAN checksum narrows
    the list, picks alone only when exactly ONE bank matches, and otherwise
    re-renders with the candidates leading the dropdown. The name enquiry is
    the real safety net either way.
    """
    from decimal import Decimal, InvalidOperation

    from common.http import MIN_TRANSFER, daily_limit_error, send_limit_error
    from transfers.models import Bank
    from utility.providers import payout_resolve_account

    from .router import (_clear_actions, _flow_fields, _insufficient, _touch,
                         nuban_bank_candidates)
    from wallet.services import get_or_create_wallet

    pa = resolve_flow_token(token)
    if pa is None:
        return _success_screen("This request has expired. Please start again in the chat.")
    user = pa.user

    # A search, not a submit. Handled FIRST and before any validation: the
    # customer who is still hunting for their bank has not finished filling the
    # form, and answering "enter a valid amount" to someone who typed "kuda" is
    # refusing the thing they actually asked for.
    #
    # The rule is `bank_search` set AND `bank` empty. Once a bank is picked the
    # search text is ignored, so leftover text can never trap someone in a loop
    # of narrowing instead of paying — which is the failure mode of treating the
    # search as a mode rather than as a state.
    #
    # Re-rendering the SAME screen id is what makes this usable: WhatsApp keeps a
    # form's client-side values on a same-screen answer, so the amount and account
    # number the customer already typed survive the narrowing. That retention is
    # a nuisance on the PIN pages — it is why PIN_RETRY exists as a separate
    # screen — and here it is exactly the behaviour wanted.
    search = " ".join(str(data.get("bank_search", "") or "").split())[:40]
    if search and not str(data.get("bank", "")).strip():
        from .router import _bank_items

        shown = _bank_items(query=search)
        full = _bank_items()
        if len(shown) == len(full):
            hint = (f'No bank name contains "{search}" — showing all of them. '
                    "Check the spelling, or just pick from the list.")
        else:
            hint = (f'Showing {len(shown)} bank{"" if len(shown) == 1 else "s"} '
                    f'matching "{search}". Pick yours, then tap Continue.')
        return _transfer_form_screen(query=search, hint=hint)

    try:
        amount = Decimal(str(data.get("amount", "")).strip())
    except InvalidOperation:
        return _transfer_form_screen(error="Enter the amount as a number, e.g. 5000.")
    account = "".join(ch for ch in str(data.get("account_number", "")) if ch.isdigit())
    if len(account) != 10:
        return _transfer_form_screen(error="The account number should be exactly 10 digits.")
    if amount < MIN_TRANSFER:
        return _transfer_form_screen(error=f"Minimum transfer is NGN {MIN_TRANSFER:,.0f}.")
    limit_msg = send_limit_error(user, amount) or daily_limit_error(user, amount, "transfer")
    if limit_msg:
        return _transfer_form_screen(error=limit_msg)
    if _insufficient(user, amount):
        balance = get_or_create_wallet(user).balance
        return _transfer_form_screen(error=f"Insufficient balance — you have NGN {balance:,.2f}.")

    code = str(data.get("bank", "")).strip()
    bank = Bank.objects.filter(code=code, active=True).first() if code else None
    if bank is None:
        candidates = nuban_bank_candidates(account)
        if len(candidates) == 1:
            bank = candidates[0]
        elif candidates:
            return _transfer_form_screen(
                error=f"This account number matches {len(candidates)} banks — "
                      "pick yours from the top of the list.",
                candidates=candidates)
        else:
            return _transfer_form_screen(error="Pick the bank from the list.")

    res = payout_resolve_account(account, bank.bank_code)
    if not res.get("success"):
        return _transfer_form_screen(
            error=f"Couldn't verify that account at {bank.name} — check the number.")
    name = (res.get("name") or "").strip() or "Bank recipient"

    # The chat path routes through _arm_confirm, which refuses to raise a PIN pad
    # for an account that has no PIN — a screen the customer can never satisfy.
    # The form chains straight to PIN_SCREEN, so it must make the same refusal.
    if not user.transaction_pin:
        _clear_actions(pa.msisdn)
        return _success_screen(
            "You haven't set a transaction PIN yet — it's what authorises payments here "
            "and in the Zitch app. Close this and reply \"set pin\" in the chat, then try "
            "the transfer again.")

    pa.payload.update({"amount": str(amount), "account": account,
                       "bank_code": bank.bank_code, "bank_name": bank.name,
                       "name": name, "pin_attempts": 0,
                       "narration": clean_narration(data.get("narration"))})
    fields = _flow_fields(pa)
    pa.payload["flow_fields"] = fields
    # The form routes to PIN_CHAIN, not PIN_SCREEN — see the PIN_CHAIN comment.
    pa.payload["flow_screen"] = PIN_CHAIN
    _touch(pa, state=FLOW_PIN_STATE, payload=pa.payload)
    return _pin_screen(fields, screen=PIN_CHAIN)


def _pin_reset_code_screen(pa, error: str = "") -> dict:
    """The live proof-of-possession before a PIN reset: the SMS code page. Error
    renders land on CODE_RETRY so the masked box comes back empty."""
    masked = f"•••••{(pa.user.phone or '')[-4:]}"
    return _identity_screen("pin_reset", error=error, label="PIN reset code",
                            summary=f"Enter the code we sent by SMS to {masked}",
                            screen=CODE_RETRY if error else _flow_screen(pa, CODE_SCREEN))


def _submit_pin_reset_code(pa, user, code: str) -> dict:
    """Check the reset code; only a match reaches the create/confirm pair."""
    from django.contrib.auth.hashers import check_password
    from django.utils import timezone

    from .router import _clear_actions, _touch

    digits = "".join(ch for ch in str(code) if ch.isdigit())
    if len(digits) != 6:
        return _pin_reset_code_screen(pa, error="The code is exactly 6 digits — check the SMS.")
    exp = pa.payload.get("pin_reset_otp_exp", "")
    if exp and timezone.now() > timezone.datetime.fromisoformat(exp):
        _clear_actions(pa.msisdn)
        return _success_screen("That code expired. Reply *reset pin* in the chat to start again.")
    if not check_password(digits, pa.payload.get("pin_reset_otp_hash", "")):
        attempts = int(pa.payload.get("pin_reset_otp_attempts") or 0) + 1
        pa.payload["pin_reset_otp_attempts"] = attempts
        pa.save(update_fields=["payload"])
        if attempts >= 3:
            _clear_actions(pa.msisdn)
            return _success_screen("That's 3 incorrect codes — the PIN reset was cancelled. "
                                   "Reply *reset pin* in the chat to start again.")
        return _pin_reset_code_screen(pa, error=f"That code isn't right. {3 - attempts} attempt(s) left.")
    # Possession proven: the create/confirm pair arrives on the SAME session's
    # next page.
    for key in ("pin_reset_otp_hash", "pin_reset_otp_exp", "pin_reset_otp_attempts"):
        pa.payload.pop(key, None)
    pa.payload["flow_screen"] = PIN_CHAIN
    _touch(pa, payload=pa.payload)
    return _set_pin_screen(pa)


def _set_pin_screen(pa, error: str = "") -> dict:
    """The reset's code page while the code is unproven; then create on the
    session's PIN page (PIN_CHAIN when chained from the code, the PIN_SCREEN
    root on a direct set); confirm on PIN_CONFIRM, whose form starts empty."""
    if pa.payload.get("pin_reset_otp_hash"):
        return _pin_reset_code_screen(pa, error=error)
    if pa.payload.get("new_pin_hash"):
        return _confirm_pin_screen(error=error)
    return _pin_screen({"amount": "Create a 6-digit PIN",
                        "recipient": "",
                        "details": "You'll enter it again to confirm"},
                       error=error, screen=_flow_screen(pa, PIN_SCREEN))


def _submit_new_pin(pa, user, pin: str) -> dict:
    """Set-then-confirm across two round-trips on the same published screen, so a
    new PIN is typed into the encrypted Flow and never becomes a chat message.
    The first submit holds only a hash; the second must match it.

    Mirrors the signup PIN deliberately — one shape for "choose a PIN", whether
    it is the first one or a replacement.
    """
    from accounts.models import transaction_pin_rejection
    from django.contrib.auth.hashers import make_password

    from .router import _clear_actions, reply

    held = pa.payload.get("new_pin_hash") or ""
    if not held:
        rejected = transaction_pin_rejection(pin)
        if rejected:
            # Empty twin rather than a same-id re-render — see the matching
            # branch in _submit_onboarding_pin for why the refused digits would
            # otherwise stay in the masked box and be one tap from resubmission,
            # and why there is only one such twin to spend.
            tries = int(pa.payload.get("pin_policy_tries", 0)) + 1
            pa.payload["pin_policy_tries"] = tries
            if tries >= _PIN_CREATE_ATTEMPTS:
                # A PendingAction, so _clear_actions IS the teardown here —
                # matching the reset-code branch above.
                _clear_actions(pa.msisdn)
                return _success_screen("That PIN isn't one we can accept. Reply "
                                       "\"set pin\" in the chat to start again.")
            pa.payload["flow_screen"] = PIN_RETRY
            pa.save(update_fields=["payload"])
            return _pin_screen({"amount": "Create a 6-digit PIN",
                                "recipient": "",
                                "details": "You'll enter it again to confirm"},
                               error=rejected.rstrip(".") + ".", screen=PIN_RETRY)
        pa.payload["new_pin_hash"] = make_password(pin)   # never the raw PIN
        pa.save(update_fields=["payload"])
        return _set_pin_screen(pa)

    from django.contrib.auth.hashers import check_password
    if not check_password(pin, held):
        # Routing is forward-only, so the customer cannot be sent back to the
        # create screen — the held first entry stays authoritative and the error
        # names the way out (cancel in the chat). Capped at one error render, as
        # in _submit_onboarding_pin: the second would re-render
        # PIN_CONFIRM_RETRY onto itself and keep the mismatched digits.
        tries = int(pa.payload.get("pin_confirm_tries", 0)) + 1
        pa.payload["pin_confirm_tries"] = tries
        pa.save(update_fields=["payload"])
        if tries >= _PIN_CONFIRM_ATTEMPTS:
            _clear_actions(pa.msisdn)
            return _success_screen("Those didn't match. Reply \"set pin\" in the chat "
                                   "to start again.")
        return _confirm_pin_screen(error="Those didn't match — enter the same PIN you "
                                         "chose on the first screen, or reply \"cancel\" "
                                         "in the chat to start over.")

    # Also clears pin_reset_required AND the lockout — this reset is the
    # documented way out of the 24-hour lock, so it has to actually let the
    # customer pay afterwards. Omitting the lockout fields here left them locked
    # with a PIN they had just chosen.
    user.set_transaction_pin(pin)
    user.save(update_fields=list(user.PIN_UPDATE_FIELDS))
    _clear_actions(pa.msisdn)
    reply(pa.msisdn, "✅ *Your new 6-digit PIN is set.* Use it to authorise payments here "
                     "and in the Zitch app — it's one PIN for both.")
    return _success_screen("PIN set ✅ — see the chat.")
