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
IDENTITY_SCREEN = "IDENTITY_SCREEN"
EMAIL_SCREEN = "EMAIL_SCREEN"
SUCCESS_SCREEN = "SUCCESS"
FLOW_PIN_STATE = "flow_pin"   # PendingAction.state (and WaOnboarding.step) while a secure Flow is armed
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
    if ob is None or ob.step != FLOW_PIN_STATE or ob.expired:
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
    if pa is None or pa.state != FLOW_PIN_STATE or pa.expired:
        return None
    if not hmac.compare_digest(sig, _sig(f"{pa.id}:{pa.msisdn}")):
        return None
    return pa


# --------------------------------------------------------------------------- #
# screen builders
# --------------------------------------------------------------------------- #
def _pin_screen(summary, error: str = "") -> dict:
    """`summary` is either the structured {amount, recipient, details} the money
    flows persist, or a bare string (the signup PIN, which has no payment to
    describe). Every declared field is always supplied — the screen renders all
    three, so a missing one is a blank line rather than an omission."""
    fields = summary if isinstance(summary, dict) else {}
    heading = fields.get("amount") or (summary if isinstance(summary, str) else "")
    return {"screen": PIN_SCREEN,
            "data": {"amount": heading or "Confirm your payment",
                     "recipient": fields.get("recipient", ""),
                     "details": fields.get("details", ""),
                     "error": error or ""}}


def _pa_screen_fields(pa) -> dict:
    """The persisted screen fields for a pending action, falling back to the
    one-line summary for an action armed before the structured form existed."""
    return pa.payload.get("flow_fields") or pa.payload.get("flow_summary", "")


def _identity_screen(kind: str, error: str = "", summary: str = "", label: str = "") -> dict:
    """The masked-entry screen. Defaults to the 11-digit BVN/NIN wording, but the
    email confirmation code rides the same screen — one published masked input,
    so every secret the ladder collects is entered the same way."""
    which = (kind or "BVN").upper()
    return {"screen": IDENTITY_SCREEN,
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


def _success_screen(message: str) -> dict:
    return {"screen": SUCCESS_SCREEN, "data": {"message": message or "Done ✅"}}


# --------------------------------------------------------------------------- #
# request handler (decrypted payload -> response dict)
# --------------------------------------------------------------------------- #
def handle_flow_request(payload: dict) -> dict:
    """Route a decrypted Flows request to its response. Never raises — any
    unexpected shape resolves to a safe terminal screen so the endpoint always
    returns a well-formed (encryptable) reply."""
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
            return _submit_onboarding_pin(ob, data)
        return _pin_screen(_ob_summary(ob))

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
            return _submit_identity(pa, data)
        if kind == "email":
            return _email_step_screen(pa)
        if kind == ACCOUNT_OTP:
            return _account_otp_screen(pa)
        return _identity_screen(kind)

    if action == "INIT":
        pa = resolve_flow_token(token)
        if pa is None:
            return _success_screen("This request has expired. Please start again in the chat.")
        return _pin_screen(_pa_screen_fields(pa))

    if action == "data_exchange":
        return _submit_pin(token, data)

    # BACK / unknown actions: re-render the PIN screen if we can, else a terminal.
    pa = resolve_flow_token(token)
    return _pin_screen(_pa_screen_fields(pa)) if pa else _success_screen("Session ended.")


def _ob_summary(ob) -> str:
    return ("Re-enter your new PIN to confirm" if ob.payload.get("flow_pin_hash")
            else "Create a 4-digit PIN to authorise payments")


def _submit_onboarding_pin(ob, data: dict) -> dict:
    """Set-then-confirm across two data_exchange round-trips on the SAME published
    screen, so the signup PIN is typed into the encrypted Flow and never becomes a
    chat message. The first submit holds only a hash; the second must match it."""
    import re

    from django.contrib.auth.hashers import check_password, make_password

    from .router import finish_onboarding_from_flow

    pin = str(data.get("pin", "")).strip()
    if not re.fullmatch(r"\d{4}", pin):
        return _pin_screen(_ob_summary(ob), error="Your PIN must be exactly 4 digits.")

    held = ob.payload.get("flow_pin_hash") or ""
    if not held:
        ob.payload["flow_pin_hash"] = make_password(pin)   # never the raw PIN
        ob.save(update_fields=["payload"])
        return _pin_screen(_ob_summary(ob))

    if not check_password(pin, held):
        ob.payload["flow_pin_hash"] = ""                   # start the pair over
        ob.save(update_fields=["payload"])
        return _pin_screen(_ob_summary(ob), error="Those didn't match — set your PIN again.")

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

    from .router import _account_submit_identity, _kyc_submit_identity

    kind = pa.payload.get("id_kind", "bvn")
    number = "".join(ch for ch in str(data.get("number", "")) if ch.isdigit())
    if not re.fullmatch(r"\d{11}", number):
        return _identity_screen(kind, error="That should be exactly 11 digits.")

    try:
        # Both entry points collect the same number on the same screen; what
        # happens next is the action's business, not this module's.
        if pa.action_type == "add_account":
            _account_submit_identity(pa, pa.user, pa.msisdn, number)
        else:
            _kyc_submit_identity(pa, pa.user, pa.msisdn, kind, number)
    except Exception:  # noqa: BLE001 — never leak a stack into the Flow
        log.exception("identity flow submission failed for pa=%s", pa.id)
        return _success_screen("Something went wrong saving that. Reply 8 in the chat to try again.")
    # The chat carries the detailed outcome (verified, or queued for review), so
    # this screen only has to close cleanly.
    return _success_screen(f"{kind.upper()} received ✅ — see the chat for what's next.")


def _account_otp_screen(pa, error: str = "") -> dict:
    return _identity_screen(ACCOUNT_OTP, error=error, label="SMS code",
                            summary="Enter the code we sent to your phone")


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
    return _identity_screen("email", error=error, label="Email code",
                            summary=f"Enter the 6-digit code we sent to {pa.user.email}")


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


def _submit_pin(token: str, data: dict) -> dict:
    from common.http import evaluate_transaction_pin

    from .router import _clear_actions, run_flow_execution

    pa = resolve_flow_token(token)
    if pa is None:
        return _success_screen("This request expired or was already completed. Start again in the chat.")

    user = pa.user
    pin = str(data.get("pin", "")).strip()
    summary = _pa_screen_fields(pa)

    ok, code, message = evaluate_transaction_pin(user, pin)
    if not ok:
        # Lockout is enforced inside evaluate_transaction_pin (row-locked, shared
        # with the app/chat), so the PIN can't be brute-forced through the Flow.
        if code == "pin_locked":
            _clear_actions(pa.msisdn)
            return _success_screen(message)
        return _pin_screen(summary, error=message)

    try:
        outcome = run_flow_execution(pa, user)
    except Exception:  # never leak a stack to the Flow; the money paths are idempotent
        log.exception("flow execution failed for pa=%s", pa.id)
        _clear_actions(pa.msisdn)
        return _success_screen("Something went wrong completing that. If you were charged it will auto-reverse.")
    return _success_screen(outcome)
