"""Open-banking (Mono) endpoints: link an external bank, view it, and fund the
wallet from it via DirectPay.

Account login happens entirely in Mono's Connect widget client-side; only the
short-lived auth code reaches us here. Funding reuses the wallet's FundingIntent
+ settle_funding path (idempotent), stamped meta.provider="mono".
"""
import hashlib
import hmac
import json
import logging
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.core.exceptions import RequestDataTooBig
from django.db import IntegrityError, transaction as db_transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from common.http import api, fail, ok, parse_amount, require_user, spend_key
from utility import mono
from wallet.models import FundingIntent, Transaction
from wallet.services import make_reference, settle_funding

from .models import BankConnectSession, LinkedBankAccount

log = logging.getLogger("banklink")
MONO_WEBHOOK_BODY_MAX = 1024 * 1024
CONNECT_SESSION_TTL = timedelta(minutes=10)


def _connect_state_hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def _redirect_with_state(redirect_url: str, state: str) -> str:
    parts = urlsplit(redirect_url)
    query = [(key, value) for key, value in parse_qsl(
        parts.query, keep_blank_values=True,
    ) if key != "state"]
    query.append(("state", state))
    return urlunsplit((
        parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment,
    ))


@db_transaction.atomic
def _claim_connect_session(user, state: str) -> bool:
    session = (BankConnectSession.objects.select_for_update()
               .filter(user=user, state_hash=_connect_state_hash(state),
                       used_at__isnull=True, expires_at__gt=timezone.now())
               .first())
    if session is None:
        return False
    session.used_at = timezone.now()
    session.save(update_fields=["used_at"])
    return True


@db_transaction.atomic
def _store_linked_account(user, account_id: str, details: dict):
    """Create/reactivate this user's account without ever changing its owner."""
    acct = (LinkedBankAccount.objects.select_for_update()
            .filter(mono_account_id=account_id).first())
    if acct is None:
        try:
            with db_transaction.atomic():
                acct = LinkedBankAccount.objects.create(
                    user=user, mono_account_id=account_id,
                )
        except IntegrityError:
            # A concurrent request inserted the globally unique provider account
            # after our empty lookup. Re-read under lock and enforce ownership.
            acct = (LinkedBankAccount.objects.select_for_update()
                    .get(mono_account_id=account_id))
    if acct.user_id != user.id:
        return None

    if details.get("success"):
        acct.bank_name = str(details.get("bank_name") or "")[:120]
        acct.account_number = str(details.get("account_number") or "")[:20]
        acct.account_name = str(details.get("account_name") or "")[:120]
        acct.balance = details.get("balance_naira")
        acct.balance_updated = timezone.now()
    acct.status = LinkedBankAccount.ACTIVE
    acct.save(update_fields=[
        "bank_name", "account_number", "account_name", "balance",
        "balance_updated", "status", "updated",
    ])
    return acct


def _funding_reference(user_id: int, key: str) -> str:
    """Return one opaque, stable merchant reference per user + request key."""
    digest = hashlib.sha256(f"banklink|{user_id}|{key}".encode()).hexdigest().upper()
    return f"ZMONO{digest[:40]}"


def _funding_replay(intent: FundingIntent):
    """Replay a persisted DirectPay initialization without calling Mono again."""
    meta = intent.meta if isinstance(intent.meta, dict) else {}
    state = str(meta.get("directpay_state") or "").lower()
    reference = intent.reference
    review = meta.get("funding_review")
    if isinstance(review, dict) and review.get("active") is True:
        # Conflicting evidence always wins over a terminal-looking intent.  A
        # later signed callback can put an already-credited payment under review;
        # keep the prior credit untouched, but do not tell the customer the
        # conflict is settled or invite another provider debit.
        return ok(
            pending=True, code="funding_review", reference=reference,
            duplicate=True,
            message="This bank funding request is under review. Your wallet outcome has not been confirmed yet.",
        )
    if intent.credited or intent.status == FundingIntent.PAID:
        credit_exists = Transaction.objects.filter(
            reference=reference, user_id=intent.user_id,
            direction=Transaction.IN, transaction_status=Transaction.SUCCESS,
            amount=intent.amount,
        ).exists()
        if credit_exists and intent.credited:
            return ok(success=True, funded=True, reference=reference,
                      message="Wallet funding already confirmed", duplicate=True)
        _hold_funding_review(
            intent.id, reason="credited_without_ledger",
            data={"reference": reference, "currency": "NGN"},
            observed_amount=intent.amount,
        )
        return ok(
            pending=True, code="funding_review", reference=reference,
            duplicate=True,
            message="This bank funding request requires review. Your wallet has not been confirmed yet.",
        )
    if intent.status == FundingIntent.FAILED or state == "failed":
        return fail("This bank funding request already failed. Start a new request.",
                    status=409, code="duplicate", duplicate=True,
                    reference=reference)
    if state == "started" and meta.get("authorization_url"):
        return ok(success=True, reference=reference,
                  authorization_url=str(meta["authorization_url"]),
                  mock=bool(meta.get("mock", False)), duplicate=True)
    # `starting` includes the small window while the first HTTP request is in
    # flight.  `pending` means that request timed out ambiguously.  In either
    # case a second provider POST could pull the customer's bank twice.
    return ok(
        pending=True,
        reference=reference,
        duplicate=True,
        message=("This bank funding request is still processing. Its final status "
                 "will be updated after the provider confirms the outcome."),
    )


def _webhook_amount_naira(data: dict) -> Decimal | None:
    """Return a strictly validated NGN webhook amount.

    Mono's DirectPay API and webhook amount are denominated in the smallest
    currency unit (kobo).  Missing currency/amount is not proof of settlement;
    keeping the intent pending is safer than crediting the requested amount.
    """
    if str(data.get("currency") or "").strip().upper() != "NGN":
        return None
    try:
        kobo = Decimal(str(data.get("amount")))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not kobo.is_finite() or kobo <= 0 or kobo != kobo.to_integral_value():
        return None
    return kobo / Decimal("100")


def _funding_claim_error(intent: FundingIntent, user, linked_id: int,
                         amount, key) -> str:
    """Return a conflict marker unless an existing intent binds this request."""
    if intent.user_id != user.id:
        return "conflict"
    meta = intent.meta if isinstance(intent.meta, dict) else {}
    fingerprint = str(getattr(key, "fingerprint", "") or "")
    stored = str(meta.get("idempotency_fingerprint") or "")
    if stored and fingerprint and not hmac.compare_digest(stored, fingerprint):
        return "conflict"
    # Amount/account are a second binding guard for legacy rows without a
    # fingerprint and make the replay decision independent of mutable account
    # status.
    if intent.amount != amount or str(meta.get("linked_id")) != str(linked_id):
        return "conflict"
    return ""


@db_transaction.atomic
def _claim_funding_intent(user, acct: LinkedBankAccount, amount, key):
    """Create-or-lock the one intent represented by ``key``.

    The deterministic unique reference is also the cross-process race guard: a
    concurrent retry cannot create another intent/provider charge.  The request
    fingerprint prevents a client from reusing one key for a different account
    or amount.
    """
    reference = _funding_reference(user.id, str(key))
    fingerprint = str(getattr(key, "fingerprint", "") or "")
    intent, created = FundingIntent.objects.select_for_update().get_or_create(
        reference=reference,
        defaults={
            "user": user,
            "amount": amount,
            "meta": {
                "provider": "mono",
                "linked_id": acct.id,
                "idempotency_key": str(key),
                "idempotency_fingerprint": fingerprint,
                # Commit the claim BEFORE the provider call.  A retry that
                # arrives while it is in flight must wait for the webhook, not
                # issue a second money-moving POST.
                "directpay_state": "starting",
            },
        },
    )
    return intent, created, _funding_claim_error(
        intent, user, acct.id, amount, key,
    )


@db_transaction.atomic
def _record_directpay_result(intent_id: int, result: dict, state: str) -> FundingIntent:
    """Merge provider metadata without racing a fast success webhook."""
    intent = FundingIntent.objects.select_for_update().get(pk=intent_id)
    meta = dict(intent.meta or {})
    meta.update({
        "directpay_state": state,
        "provider_reference": str(result.get("reference") or intent.reference),
    })
    if result.get("authorization_url"):
        meta["authorization_url"] = str(result["authorization_url"])
    if result.get("mock"):
        meta["mock"] = True
    if result.get("http_status") is not None:
        meta["initialize_http_status"] = result.get("http_status")
    if result.get("message"):
        meta["initialize_message"] = str(result["message"])[:500]
    intent.meta = meta
    update_fields = ["meta", "updated"]
    if state == "failed" and not intent.credited and intent.status != FundingIntent.PAID:
        intent.status = FundingIntent.FAILED
        update_fields.append("status")
    intent.save(update_fields=update_fields)
    return intent


def _callback_reference(value) -> str:
    """A bounded scalar callback reference, or an empty string for bad shapes."""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return ""
    return str(value).strip()[:120]


def _mono_funding_intent(data: dict):
    """Resolve a callback through every reference Mono can legitimately return.

    The merchant reference is ours and therefore authoritative.  Try it before
    Mono's provider reference: after an initialization timeout we may never have
    received the provider alias, while the callback can still carry both fields.
    """
    merchant_refs = []
    # Mono's current DirectPay webhook returns in ``data.object.reference`` the
    # merchant reference supplied to /payments/initiate.  Older callback samples
    # used explicit merchant-ref names, so accept those too without treating an
    # unrelated provider id as authoritative.
    for field in ("reference", "merchant_ref", "merchant_reference"):
        value = _callback_reference(data.get(field))
        if value and value not in merchant_refs:
            merchant_refs.append(value)
    provider_reference = (_callback_reference(data.get("id"))
                          or _callback_reference(data.get("_id")))

    direct_refs = [*merchant_refs]
    for reference in direct_refs:
        intent = FundingIntent.objects.filter(
            reference=reference, meta__provider="mono",
        ).first()
        if intent is not None:
            return intent, provider_reference, merchant_refs, ""

    # The provider alias is not a first-class unique DB field yet. Fail closed if
    # bad/provider-duplicate data maps one callback to multiple customer intents.
    alias_refs = [provider_reference, *merchant_refs]
    for reference in dict.fromkeys(ref for ref in alias_refs if ref):
        matches = list(FundingIntent.objects.filter(
            meta__provider="mono", meta__provider_reference=reference,
        )[:2])
        if len(matches) == 1:
            return matches[0], provider_reference, merchant_refs, ""
        if len(matches) > 1:
            return None, provider_reference, merchant_refs, "ambiguous"
    return None, provider_reference, merchant_refs, "unresolved"


@db_transaction.atomic
def _hold_funding_review(intent_id: int, *, reason: str, data: dict,
                         observed_amount=None) -> FundingIntent:
    """Persist a non-settleable callback for finance review without crediting."""
    intent = FundingIntent.objects.select_for_update().get(pk=intent_id)
    meta = dict(intent.meta or {})
    prior = meta.get("funding_review")
    prior = dict(prior) if isinstance(prior, dict) else {}
    reasons = list(prior.get("reasons") or [])
    if reason not in reasons:
        reasons.append(reason)
    provider_reference = (_callback_reference(data.get("id"))
                          or _callback_reference(data.get("_id")))
    merchant_reference = (_callback_reference(data.get("reference"))
                          or _callback_reference(data.get("merchant_ref"))
                          or _callback_reference(data.get("merchant_reference")))
    prior.update({
        "active": True,
        "reason": reason,
        "reasons": reasons,
        "expected_amount": str(intent.amount),
        "observed_amount": (str(observed_amount)
                            if observed_amount is not None else ""),
        "currency": str(data.get("currency") or "")[:12],
        "provider_reference": provider_reference,
        "merchant_reference": merchant_reference,
        "last_event_at": timezone.now().isoformat(),
        "event_count": int(prior.get("event_count") or 0) + 1,
    })
    meta["funding_review"] = prior
    intent.meta = meta
    intent.save(update_fields=["meta", "updated"])
    return intent


def _logged_webhook_response(request, response, *, event=None, reference="",
                             action="", outcome=None, verified=True):
    """Append forensic callback evidence and return ``response`` unchanged."""
    from common.ratelimit import client_ip
    from whatsapp.models import WebhookEvent
    from whatsapp.ops import record_webhook

    record_webhook(
        "mono",
        outcome=outcome or WebhookEvent.ACCEPTED,
        verified=verified,
        http_status=getattr(response, "status_code", 200),
        remote_ip=client_ip(request),
        reference=reference,
        action=action,
        payload=event if isinstance(event, dict) else {},
    )
    return response


def _serialize(a: LinkedBankAccount) -> dict:
    return {
        "id": a.id,
        "bank_name": a.bank_name,
        "account_number": a.masked_number,
        "account_name": a.account_name,
        "balance": (str(a.balance) if a.balance is not None else None),
        "balance_updated": (a.balance_updated.isoformat() if a.balance_updated else None),
        "status": a.status,
    }


@api
@require_user
def connect_init(request):
    """POST /api/banklink/connect-init/ {access_token, redirect_url}
    -> {success, mono_url} — start a hosted Mono Connect session.

    The app opens ``mono_url`` in an auth session; Mono redirects to
    ``redirect_url`` with a ``code`` the app then posts to /connect/.
    """
    user = request.user_obj
    raw_redirect = request.data.get("redirect_url")
    if not isinstance(raw_redirect, str):
        return fail("Missing redirect_url")
    redirect_url = raw_redirect.strip()
    if not redirect_url:
        return fail("Missing redirect_url")
    state = secrets.token_urlsafe(32)
    BankConnectSession.objects.filter(
        user=user, expires_at__lte=timezone.now(),
    ).delete()
    session = BankConnectSession.objects.create(
        user=user, state_hash=_connect_state_hash(state),
        expires_at=timezone.now() + CONNECT_SESSION_TTL,
    )
    res = mono.initiate_connect(
        _redirect_with_state(redirect_url, state),
        name=(user.get_full_name() or user.username or "").strip(),
        email=getattr(user, "email", "") or "",
        ref=make_reference(),
    )
    if not res.get("success"):
        session.delete()
        return fail(res.get("message", "Could not start bank linking"), status=502)
    return ok(success=True, mono_url=res["mono_url"])


@api
@require_user
def connect(request):
    """POST /api/banklink/connect/ {access_token, code, state}
    -> {success, account} — exchange a Mono Connect auth code and link the account.
    """
    user = request.user_obj
    raw_code = request.data.get("code")
    raw_state = request.data.get("state")
    if not isinstance(raw_code, str):
        return fail("Missing Mono auth code")
    if not isinstance(raw_state, str):
        return fail("Missing or expired bank-link state", status=409,
                    code="invalid_connect_state")
    code = raw_code.strip()
    state = raw_state.strip()
    if not code:
        return fail("Missing Mono auth code")
    if not state or len(state) > 200 or not _claim_connect_session(user, state):
        return fail("Missing or expired bank-link state", status=409,
                    code="invalid_connect_state")

    res = mono.exchange_token(code)
    if not res.get("success"):
        return fail(res.get("message", "Could not link your bank"), status=502)
    account_id = str(res.get("account_id") or "").strip()[:64]
    if not account_id:
        return fail("Could not link your bank", status=502)

    details = mono.get_account(account_id)  # best-effort snapshot
    acct = _store_linked_account(user, account_id, details)
    if acct is None:
        account_fingerprint = hashlib.sha256(account_id.encode()).hexdigest()[:16]
        log.warning(
            "mono_account_ownership_conflict account=%s attempted_user=%s",
            account_fingerprint, user.id,
        )
        from whatsapp.ops import record_audit
        record_audit(
            "banklink.account_ownership_conflict", actor=user,
            target=f"mono:{account_fingerprint}",
            after={"attempted_user_id": user.id}, actor_type="user",
        )
        return fail(
            "This bank account is already linked to another Zitch account",
            status=409, code="bank_account_already_linked",
        )
    return ok(success=True, account=_serialize(acct), message="Bank linked")


@api
@require_user
def list_accounts(request):
    """POST /api/banklink/list/ {access_token} -> {accounts: [...]}"""
    items = request.user_obj.linked_banks.filter(status=LinkedBankAccount.ACTIVE)
    return ok(accounts=[_serialize(a) for a in items])


@api
@require_user
def refresh(request):
    """POST /api/banklink/refresh/ {access_token, linked_id} -> {success, account}
    Re-pulls the linked account's balance from Mono and caches it.
    """
    acct = request.user_obj.linked_banks.filter(
        id=request.data.get("linked_id"), status=LinkedBankAccount.ACTIVE).first()
    if acct is None:
        return fail("Linked account not found", status=404)
    res = mono.get_balance(acct.mono_account_id)
    if res.get("success") and res.get("balance_naira") is not None:
        acct.balance = res["balance_naira"]
        acct.balance_updated = timezone.now()
        acct.save(update_fields=["balance", "balance_updated", "updated"])
    return ok(success=True, account=_serialize(acct))


@api
@require_user
def unlink(request):
    """POST /api/banklink/unlink/ {access_token, linked_id} -> {success}"""
    acct = request.user_obj.linked_banks.filter(id=request.data.get("linked_id")).first()
    if acct is None:
        return fail("Linked account not found", status=404)
    acct.status = LinkedBankAccount.UNLINKED
    acct.save(update_fields=["status", "updated"])
    return ok(success=True, message="Bank unlinked")


@api
@require_user
def fund(request):
    """POST /api/banklink/fund/ {access_token, linked_id, amount, idempotency_key}
    -> {success, reference, authorization_url}

    Starts a Mono DirectPay debit from the linked bank. The wallet is credited
    only when Mono confirms via webhook (settle_funding, idempotent).  Retrying
    the same idempotency key replays the stored initialization and never creates
    another provider debit.
    """
    user = request.user_obj
    amount = parse_amount(request.data.get("amount"))
    if amount is None:
        return fail("Enter a valid amount")
    if amount < 100:
        return fail("Minimum funding amount is ₦100")

    raw_key = request.data.get("idempotency_key")
    if raw_key is None:
        raw_key = request.headers.get("Idempotency-Key", "")
    if not isinstance(raw_key, str):
        return fail("Invalid idempotency key")
    if not raw_key.strip():
        return fail("A stable idempotency key is required for bank funding",
                    code="idempotency_key_required")

    raw_linked_id = request.data.get("linked_id")
    if isinstance(raw_linked_id, bool):
        return fail("Linked account not found", status=404)
    try:
        linked_id = int(str(raw_linked_id).strip())
    except (TypeError, ValueError):
        return fail("Linked account not found", status=404)
    if linked_id <= 0 or str(raw_linked_id).strip() != str(linked_id):
        return fail("Linked account not found", status=404)

    key = spend_key(
        raw_key, user,
        "banklink-fund", linked_id, amount,
    )
    reference = _funding_reference(user.id, str(key))
    existing = FundingIntent.objects.filter(reference=reference).first()
    if existing is not None:
        claim_error = _funding_claim_error(
            existing, user, linked_id, amount, key,
        )
        if claim_error:
            return fail(
                "That retry key belongs to a different bank funding request. Start a new request.",
                status=409, code="idempotency_conflict", duplicate=True,
            )
        # Account state is intentionally not consulted on a replay: unlinking
        # after a lost response must not hide the durable result or invite a new
        # provider debit.
        return _funding_replay(existing)

    # Mutable account state gates only a new provider attempt.  An existing
    # intent has already committed the customer's idempotency claim above.
    acct = user.linked_banks.filter(
        id=linked_id, status=LinkedBankAccount.ACTIVE,
    ).first()
    if acct is None:
        return fail("Linked account not found", status=404)
    intent, created, claim_error = _claim_funding_intent(user, acct, amount, key)
    if claim_error:
        return fail(
            "That retry key belongs to a different bank funding request. Start a new request.",
            status=409, code="idempotency_conflict", duplicate=True,
        )
    if not created:
        return _funding_replay(intent)

    reference = intent.reference
    email = user.email or f"{user.phone}@zitch.app"
    name = (user.get_full_name() or user.phone or "").strip()
    try:
        res = mono.initiate_directpay(amount, reference, email=email, name=name)
    except Exception as exc:  # noqa: BLE001 - POST outcome is financially ambiguous
        log.exception("mono_directpay_unhandled_outcome ref=%s", reference)
        res = {
            "success": False,
            "pending": True,
            "reference": reference,
            "message": ("Bank funding request is processing; its outcome is not yet "
                        "confirmed."),
            "error_type": type(exc).__name__,
        }
    if not isinstance(res, dict):
        log.error("mono_directpay_invalid_result ref=%s type=%s",
                  reference, type(res).__name__)
        res = {
            "success": False,
            "pending": True,
            "reference": reference,
            "message": ("Bank funding request is processing; its outcome is not yet "
                        "confirmed."),
        }
    if res.get("pending"):
        _record_directpay_result(intent.id, res, "pending")
        return ok(
            pending=True,
            reference=reference,
            message=(res.get("message") or
                     "Bank funding request is processing; its outcome is not yet confirmed."),
        )
    authorization_url = str(res.get("authorization_url") or "")
    if not res.get("success"):
        _record_directpay_result(intent.id, res, "failed")
        # This branch is reached only for a definitive pre-acceptance/config or
        # non-ambiguous 4xx refusal.  A 5xx would tell durable clients to retain
        # the key as an unknown outcome even though this intent is terminal.
        return fail(res.get("message", "Could not start bank funding"), status=422,
                    code="bank_funding_failed", reference=reference)
    if not res.get("mock") and not authorization_url.startswith(("https://", "http://")):
        # A 2xx/"success" envelope without a usable authorization URL does not
        # prove that Mono failed to create the debit. Preserve the claimed intent
        # and wait for provider evidence instead of telling the user to start over.
        res = {
            **res,
            "pending": True,
            "message": ("Bank funding request is processing; its authorization link "
                        "was not confirmed."),
        }
        _record_directpay_result(intent.id, res, "pending")
        return ok(pending=True, reference=reference, message=res["message"])
    _record_directpay_result(intent.id, res, "started")
    return ok(success=True, reference=reference,
              authorization_url=authorization_url, mock=res.get("mock", False))


@csrf_exempt
def webhook(request):
    """POST /api/banklink/webhook/ — Mono callback.

    Verifies the shared-secret header, then: marks accounts active on
    account_connected, and credits the wallet (idempotently) on a successful
    DirectPay payment. Always 200 on accepted events so Mono stops retrying.
    """
    if request.method != "POST":
        return _logged_webhook_response(
            request, fail("Method not allowed", status=405),
            outcome="rejected_method", verified=False, action="method_not_allowed",
        )
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > MONO_WEBHOOK_BODY_MAX:
            return _logged_webhook_response(
                request, fail("Payload too large", status=413),
                outcome="bad_body", verified=False, action="body_too_large",
            )
    except (TypeError, ValueError):
        return _logged_webhook_response(
            request, fail("Invalid content length", status=400),
            outcome="bad_body", verified=False, action="invalid_content_length",
        )
    signature = request.headers.get("mono-webhook-secret", "")
    # Mono uses a shared secret header, so reject unauthenticated traffic before
    # spending work on an attacker-controlled body.
    if not mono.verify_webhook({}, signature):
        log.warning("mono_webhook_bad_signature has_header=%s", bool(signature))
        return _logged_webhook_response(
            request, fail("Invalid signature", status=401),
            outcome="rejected_signature", verified=False, action="bad_signature",
        )
    try:
        body = request.body
        if len(body) > MONO_WEBHOOK_BODY_MAX:
            return _logged_webhook_response(
                request, fail("Payload too large", status=413),
                outcome="bad_body", verified=True, action="body_too_large",
            )
        event = json.loads(body or b"{}")
    except (RequestDataTooBig, ValueError, TypeError):
        return _logged_webhook_response(
            request, fail("Invalid payload", status=400),
            outcome="bad_body", verified=True, action="invalid_json",
        )
    if not isinstance(event, dict):
        return _logged_webhook_response(
            request, fail("Invalid payload", status=400),
            outcome="bad_body", verified=True, action="invalid_envelope",
        )

    raw_type = event.get("event")
    etype = raw_type.strip().lower() if isinstance(raw_type, str) else ""
    data = event.get("data", {}) or {}
    if not isinstance(data, dict):
        return _logged_webhook_response(
            request, fail("Invalid payload", status=400), event=event,
            outcome="bad_body", verified=True, action="invalid_data",
        )
    if etype == "direct_debit.payment_successful":
        # Current Mono DirectPay webhooks put the payment under data.object.
        # Never interpret a similarly named or differently shaped event as a
        # settlement instruction: refund/reversal/cancel events must fail closed.
        payment = data.get("object")
        if not isinstance(payment, dict):
            return _logged_webhook_response(
                request, fail("Invalid payment payload", status=400), event=event,
                outcome="bad_body", verified=True, action="invalid_payment_object",
            )
        intent, provider_reference, merchant_refs, resolution = _mono_funding_intent(payment)
        audit_reference = (merchant_refs[0] if merchant_refs else provider_reference)
        if not audit_reference:
            return _logged_webhook_response(
                request, fail("Missing funding reference", status=400), event=event,
                reference="", action="missing_reference",
            )
        if intent is None:
            code = ("ambiguous_reference" if resolution == "ambiguous"
                    else "unresolved_reference")
            log.warning("mono_funding_%s provider_ref=%s merchant_refs=%s",
                        code, provider_reference, merchant_refs)
            return _logged_webhook_response(
                request,
                fail("Funding reference is not bound yet", status=409, code=code),
                event=event, reference=audit_reference, action=code,
            )

        payment_status = str(payment.get("status") or "").strip().lower()
        if payment_status != "successful" or payment.get("verified") is not True:
            _hold_funding_review(
                intent.id, reason="unverified_payment_status", data=payment,
            )
            log.warning(
                "mono_funding_unverified_status ref=%s status=%s verified=%r",
                intent.reference, payment_status, payment.get("verified"),
            )
            return _logged_webhook_response(
                request,
                ok(pending=True, code="funding_review", reference=intent.reference,
                   message="Funding confirmation requires review before the wallet can be credited."),
                event=event, reference=intent.reference,
                action="funding_review_unverified_status",
            )

        verified_amount = _webhook_amount_naira(payment)
        if verified_amount is None:
            _hold_funding_review(intent.id, reason="unverified_amount", data=payment)
            log.warning("mono_funding_unverified_amount ref=%s", intent.reference)
            return _logged_webhook_response(
                request,
                ok(pending=True, code="funding_review",
                   reference=intent.reference,
                   message="Funding amount requires review before the wallet can be credited."),
                event=event, reference=intent.reference, action="funding_review_missing_amount",
            )
        if verified_amount != intent.amount:
            _hold_funding_review(
                intent.id, reason="amount_mismatch", data=payment,
                observed_amount=verified_amount,
            )
            log.warning("mono_funding_amount_mismatch ref=%s expected=%s observed=%s",
                        intent.reference, intent.amount, verified_amount)
            return _logged_webhook_response(
                request,
                ok(pending=True, code="funding_review",
                   reference=intent.reference,
                   message="Funding amount requires review before the wallet can be credited."),
                event=event, reference=intent.reference, action="funding_review_amount_mismatch",
            )

        settled = settle_funding(
            intent.reference,
            verified_amount=verified_amount,
            verified_currency="NGN",
            evidence={
                "source": "mono_webhook",
                "provider_reference": provider_reference,
                "event_id": _callback_reference(event.get("event_id")),
            },
        )
        if settled is not None:
            log.info("mono_funding_settled ref=%s amount=%s",
                     intent.reference, verified_amount)
            return _logged_webhook_response(
                request, ok(status=True), event=event,
                reference=intent.reference, action="funding_settled",
            )

        intent.refresh_from_db()
        credit_exists = Transaction.objects.filter(
            reference=intent.reference, user_id=intent.user_id,
            direction=Transaction.IN, transaction_status=Transaction.SUCCESS,
            amount=intent.amount,
        ).exists()
        if intent.credited and credit_exists:
            return _logged_webhook_response(
                request, ok(status=True, duplicate=True), event=event,
                reference=intent.reference, action="funding_duplicate",
            )
        review = (intent.meta or {}).get("funding_review") or {}
        if not isinstance(review, dict) or review.get("active") is not True:
            _hold_funding_review(
                intent.id, reason="settlement_noop", data=payment,
                observed_amount=verified_amount,
            )
        log.warning("mono_funding_held_after_settlement_noop ref=%s", intent.reference)
        return _logged_webhook_response(
            request,
            ok(pending=True, code="funding_review", reference=intent.reference,
               message="Funding requires review before the wallet can be credited."),
            event=event, reference=intent.reference,
            action="funding_review_held",
        )
    elif "account" in etype and ("connected" in etype or "updated" in etype):
        account_id = (_callback_reference(data.get("id"))
                      or _callback_reference(data.get("account")))
        LinkedBankAccount.objects.filter(mono_account_id=account_id).update(
            status=LinkedBankAccount.ACTIVE)
        log.info("mono_account_event=%s acct=%s", etype, account_id)
        return _logged_webhook_response(
            request, ok(status=True), event=event,
            reference=account_id, action="account_updated",
        )
    else:
        log.info("mono_webhook ignored_event=%s", etype)
        return _logged_webhook_response(
            request, ok(status=True), event=event,
            reference=(_callback_reference(data.get("reference"))
                       or _callback_reference(data.get("merchant_ref"))),
            action="ignored_event",
        )
