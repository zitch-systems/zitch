import hashlib
import hmac
import logging
import re
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.db import transaction as db_transaction

from common.http import (
    MIN_TRANSFER, api, check_daily_limit, check_send_limits, fail, idempotent_replay,
    ok, parse_amount, require_user, spend_key, verify_transaction_pin,
)
from common.ratelimit import ratelimit
from utility.providers import funding_initialize, funding_verify, payment_provider

from .models import FundingIntent, Transaction
from .services import (
    DuplicateTransaction,
    InsufficientFunds,
    LimitExceeded,
    existing_for_key,
    hold_funding_review,
    customer_safe_failure,
    customer_visible_transactions,
    customer_funding_account,
    wallet_balance_payload,
    get_or_create_wallet,
    settle_funding,
    transfer,
)

log = logging.getLogger("wallet")
User = get_user_model()

@api
@require_user
def wallet_balance(request):
    """POST /api/wallet_balance/ {access_token}
    -> {success, wallet, user_first_name, user_last_name, user_phone_number, user_email}
    """
    from accounts.views import avatar_url, spend_account_namespace

    user = request.user_obj
    wallet = get_or_create_wallet(user)
    return ok(
        success=True,
        wallet=str(wallet.balance),
        **{key: str(value) for key, value in wallet_balance_payload(user, wallet=wallet).items()},
        **customer_funding_account(user),
        account_namespace=spend_account_namespace(user),
        user_first_name=user.first_name or "",
        user_last_name=user.last_name or "",
        user_phone_number=user.phone or "",
        user_email=user.email or "",
        user_avatar=avatar_url(request, user),
    )


@api
@require_user
def wallet_account(request):
    """POST /api/wallet/account/ {access_token}
    -> {success, account_number, account_name, bank_name, bank_accounts}

    A fast, side-effect-free read of the user's dedicated funding account: it never
    calls the provider on load. (A reserve needs the raw BVN, which we never store,
    so a read-time attempt can't succeed — it would only hang the Add-money page on
    a slow provider call.) Provisioning is explicit: at BVN verification time, or via
    /api/wallet/account/create/, both of which have the BVN in hand.
    """
    user = request.user_obj
    wallet = get_or_create_wallet(user)
    return ok(
        success=True,
        **customer_funding_account(user),
        bvn_verified=user.bvn_verified,
        nin_verified=user.nin_verified,
        # The customer's registered legal name, so the Add-money screen can always
        # show whose account this is — even before it's provisioned, or on the rare
        # provider response that omits the holder name (account_name is blank).
        holder_name=(user.get_full_name() or "").strip(),
    )



@api
@require_user
def transaction_history(request):
    """POST /api/user-transaction-history/ {access_token}
    -> {status, all_site_transactions: [{service, amount, transaction_status, date}]}
    """
    user = request.user_obj
    txns = list(customer_visible_transactions(user.transactions.all())[:100])
    reviews = _transaction_reviews(txns)
    return ok(
        status=True,
        all_site_transactions=[_txn_row(t, reviews.get(t.pk)) for t in txns],
    )


def _transaction_reviews(txns) -> dict[int, str]:
    """Bulk-resolve every durable review rail for customer transaction rows."""
    from .review_state import transaction_review_map

    return {pk: kind for pk, (kind, _reason) in
            transaction_review_map(txns).items()}


_REVIEW_UNSET = object()


def _txn_row(t, review_kind=_REVIEW_UNSET) -> dict:
    """One transaction, in the shape the app's history list already expects.

    Extracted so the single-transaction lookup below cannot drift from the list:
    two hand-written copies of the same row is how a detail screen ends up
    disagreeing with the list it was opened from.
    """
    meta = t.meta if isinstance(t.meta, dict) else {}
    if review_kind is _REVIEW_UNSET:
        review_kind = _transaction_reviews([t]).get(t.pk, "")
    else:
        review_kind = review_kind or ""
    under_review = bool(review_kind)
    return {
        "service": t.service,
        "amount": str(t.amount),
        # A terminal-looking row with unresolved bank evidence is not a terminal
        # customer outcome. Expose it as Pending until finance resolves the hold;
        # otherwise FAILED invites a retry and SUCCESS creates a false receipt.
        "transaction_status": (Transaction.PENDING if under_review
                               else t.transaction_status),
        "under_review": under_review,
        "review_kind": review_kind,
        "status_message": ("We are confirming the provider's final outcome. Do not retry."
                           if under_review else ""),
        "date": t.created.strftime("%Y-%m-%d %H:%M"),
        "reference": t.reference,
        "direction": t.direction,
        # The customer's own note. `note` is what a bank transfer stores
        # (execute_payout) and `narration` what a bill or top-up stores
        # (run_provider_purchase) — two keys for one idea, named differently long
        # before there was a field to fill either, and wallet.alerts._narration_line
        # already reads both the same way.
        "narration": str(meta.get("note") or meta.get("narration") or "")[:120],
    }


@api
@ratelimit("transaction_status", limit=120, window=60)
@require_user
def transaction_status(request):
    """POST /api/transaction/status/ {access_token, reference}
    -> {success, transaction: {...}}

    One transaction, read live. The detail screen was built entirely from the
    route params handed to it when the row was tapped, so a PENDING transfer
    displayed there stayed "Pending" forever — the reconciler settles the row
    minutes later, but nothing on that screen ever asked again. Customers
    reasonably read a permanently-pending transfer as lost money, which is the
    single worst thing a payments screen can imply.

    Scoped to the caller's OWN transactions: `user.transactions` is the filter,
    so a reference belonging to somebody else is a 404 here rather than a lookup
    oracle for other people's payment references.
    """
    user = request.user_obj
    reference = str(request.data.get("reference") or "").strip()
    if not reference:
        return fail("Missing transaction reference")
    txn = customer_visible_transactions(
        user.transactions.filter(reference=reference)).first()
    if txn is None:
        return fail("Transaction not found", status=404)
    row = _txn_row(txn)
    # Recover purchased value after an app restart or a late provider settlement.
    # Keep it out of bulk history, and never expose a token while the payment is
    # pending, refunded or under review. The lookup above is owner-scoped.
    meta = txn.meta if isinstance(txn.meta, dict) else {}
    bank_bill = meta.get("provider") == "9psb" and meta.get("vas_type") == "bill"
    if (row["transaction_status"] == Transaction.SUCCESS
            and not row["under_review"] and txn.direction == Transaction.OUT
            and (bank_bill or txn.service.casefold().startswith("electricity"))):

        def receipt_text(key, maximum=160):
            value = meta.get(key)
            if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
                return ""
            return str(value).strip()[:maximum]

        row.update(
            receipt_pending=meta.get("receipt_pending") is True,
            meter=receipt_text("meter", 40),
            meter_type=receipt_text("meter_type", 20),
            customer_name=receipt_text("customer_name") or receipt_text("customer"),
            customer_address=receipt_text("customer_address", 320) or receipt_text("address", 320),
            electricity_units=receipt_text("electricity_units", 40) or receipt_text("units", 40),
        )
        if row["meter_type"] != "postpaid":
            # A provider reference is not proof of a recharge token. Only the
            # explicitly parsed and persisted token can be typed into a meter.
            row["token"] = receipt_text("token", 256)
    response = ok(success=True, transaction=row)
    response["Cache-Control"] = "no-store"
    return response


# --------------------------- WALLET FUNDING ---------------------------
def _fund_initialize_reference(user_id: int, key: str) -> str:
    """Return one opaque merchant reference per user + funding request key."""
    digest = hashlib.sha256(f"wallet-fund|{user_id}|{key}".encode()).hexdigest().upper()
    return f"ZPAY{digest[:40]}"


@db_transaction.atomic
def _fund_initialize_replay(intent: FundingIntent):
    """Replay a persisted checkout initialization without calling the rail."""
    # Re-read under a row lock so a concurrent settlement either completes before
    # this decision (and leaves its immutable ledger proof), or observes the review
    # hold written below.  A stale in-memory PAID flag must never become a receipt.
    intent = FundingIntent.objects.select_for_update().get(pk=intent.pk)
    meta = intent.meta if isinstance(intent.meta, dict) else {}
    state = str(meta.get("initialize_state") or "").lower()
    reference = intent.reference
    review = meta.get("funding_review")
    if isinstance(review, dict) and review.get("active") is True:
        # A later provider/callback conflict is newer evidence than the
        # terminal-looking flags. Preserve any existing credit, but do not tell
        # the customer the disputed outcome is settled.
        return ok(
            pending=True, code="funding_review", reference=reference,
            duplicate=True,
            message="This funding request is under review. Your wallet outcome has not been confirmed yet.",
        )
    if intent.credited or intent.status == FundingIntent.PAID:
        credit_exists = Transaction.objects.filter(
            reference=reference,
            user_id=intent.user_id,
            direction=Transaction.IN,
            transaction_status=Transaction.SUCCESS,
            currency="NGN",
            amount=intent.amount,
        ).exists()
        if credit_exists:
            return ok(
                success=True,
                funded=True,
                reference=reference,
                message="Wallet funding already confirmed",
                duplicate=True,
            )
        hold_funding_review(
            reference,
            reason="credited_without_ledger",
            observed_amount=intent.amount,
            observed_currency="NGN",
            evidence={"source": "fund_initialize_replay"},
        )
        return ok(
            pending=True,
            code="funding_review",
            reference=reference,
            duplicate=True,
            message=(
                "This funding request requires review. Your wallet outcome has "
                "not been confirmed. Do not retry this payment."
            ),
        )
    if intent.status == FundingIntent.FAILED or state == "failed":
        return fail(
            str(meta.get("initialize_message") or
                "This funding request already failed. Start a new request."),
            status=409,
            code="duplicate",
            duplicate=True,
            reference=reference,
        )
    if state == "started":
        return ok(
            success=True,
            reference=reference,
            authorization_url=str(meta.get("authorization_url") or ""),
            mock=bool(meta.get("mock", False)),
            duplicate=True,
        )
    # ``starting`` covers the window while the first provider request is in
    # flight; ``pending`` is an explicitly unknown provider outcome. In either
    # state another POST could create a second charge, so wait for confirmation.
    return ok(
        pending=True,
        reference=reference,
        duplicate=True,
        message=str(
            meta.get("initialize_message")
            or "This funding request is still processing. Its final status will be updated after confirmation."
        ),
    )


@db_transaction.atomic
def _claim_fund_initialize(user, amount, provider: str, key):
    """Create or lock the single checkout attempt represented by ``key``."""
    reference = _fund_initialize_reference(user.id, str(key))
    fingerprint = str(getattr(key, "fingerprint", "") or "")
    intent, created = FundingIntent.objects.select_for_update().get_or_create(
        reference=reference,
        defaults={
            "user": user,
            "amount": amount,
            "meta": {
                "provider": provider,
                "idempotency_key": str(key),
                "idempotency_fingerprint": fingerprint,
                # Commit the claim before the provider call. A retry arriving
                # during that call must not create a second checkout/charge.
                "initialize_state": "starting",
            },
        },
    )
    if intent.user_id != user.id:
        return intent, False, "conflict"
    meta = intent.meta if isinstance(intent.meta, dict) else {}
    stored = str(meta.get("idempotency_fingerprint") or "")
    if stored and fingerprint and not hmac.compare_digest(stored, fingerprint):
        return intent, False, "conflict"
    # Explicit fields protect legacy/incomplete rows whose fingerprint is absent.
    if intent.amount != amount or str(meta.get("provider") or "") != provider:
        return intent, False, "conflict"
    return intent, created, ""


@db_transaction.atomic
def _record_fund_initialize_result(intent_id: int, result: dict, state: str) -> FundingIntent:
    """Merge a provider result without clobbering a fast settlement callback."""
    intent = FundingIntent.objects.select_for_update().get(pk=intent_id)
    meta = dict(intent.meta or {})
    meta["initialize_state"] = state
    meta["provider_reference"] = str(result.get("reference") or intent.reference)
    if result.get("authorization_url") is not None:
        meta["authorization_url"] = str(result.get("authorization_url") or "")
    if result.get("message"):
        meta["initialize_message"] = str(result["message"])[:500]
    if result.get("mock"):
        meta["mock"] = True
    intent.meta = meta
    update_fields = ["meta", "updated"]
    if state == "failed" and not intent.credited and intent.status != FundingIntent.PAID:
        intent.status = FundingIntent.FAILED
        update_fields.append("status")
    intent.save(update_fields=update_fields)
    return intent


@api
@ratelimit("fund_initialize", limit=20, window=60)
@require_user
def fund_initialize(request):
    """POST /api/fund/initialize/ {access_token, amount, idempotency_key}
    -> {success, reference, authorization_url}

    The app opens authorization_url in a browser. The wallet is credited only
    after the payment rail confirms payment (verify endpoint and/or webhook).
    Retrying one idempotency key replays its persisted initialization; it never
    sends a second provider POST.
    """
    user = request.user_obj
    amount = parse_amount(request.data.get("amount"))
    if amount is None:
        return fail("Enter a valid amount")
    if amount < 100:
        return fail("Minimum funding amount is ₦100")

    # Stamp the rail that started this charge so verify uses the same one even if
    # PAYMENT_PROVIDER is flipped before the user returns from checkout.
    provider = payment_provider()
    raw_key = request.data.get("idempotency_key")
    if raw_key is None:
        raw_key = request.headers.get("Idempotency-Key", "")
    if not isinstance(raw_key, str):
        return fail("Invalid idempotency key")
    if not raw_key.strip():
        # A time-bucket fallback is unsafe for provider initialization: a retry
        # delayed beyond the bucket could create a second checkout or charge.
        return fail(
            "An idempotency key is required for wallet funding",
            code="idempotency_key_required",
        )
    key = spend_key(raw_key, user, "wallet-fund", provider, amount)
    intent, created, claim_error = _claim_fund_initialize(
        user, amount, provider, key,
    )
    if claim_error:
        return fail(
            "That retry key belongs to a different funding request. Start a new request.",
            status=409,
            code="idempotency_conflict",
            duplicate=True,
        )
    if not created:
        return _fund_initialize_replay(intent)

    reference = intent.reference
    email = user.email or f"{user.phone}@zitch.app"
    name = (user.get_full_name() or user.phone or "").strip()
    try:
        result = funding_initialize(email, amount, reference, name=name)
    except Exception as exc:  # noqa: BLE001 - POST outcome is financially ambiguous
        log.exception("fund_initialize_outcome_unknown ref=%s", reference)
        result = {
            "success": False,
            "pending": True,
            "reference": reference,
            "message": "Funding request is processing; its outcome is not yet confirmed.",
            "error_type": type(exc).__name__,
        }
    if not isinstance(result, dict):
        log.error("fund_initialize_invalid_result ref=%s type=%s",
                  reference, type(result).__name__)
        result = {
            "success": False,
            "pending": True,
            "reference": reference,
            "message": "Funding request is processing; its outcome is not yet confirmed.",
        }
    if result.get("pending"):
        _record_fund_initialize_result(intent.id, result, "pending")
        return ok(
            pending=True,
            reference=reference,
            message=(result.get("message") or
                     "Funding request is processing; its outcome is not yet confirmed."),
        )
    if not result.get("success"):
        _record_fund_initialize_result(intent.id, result, "failed")
        return fail(result.get("message", "Could not start payment"), status=422,
                    code="funding_initialize_failed", reference=reference)
    _record_fund_initialize_result(intent.id, result, "started")
    return ok(
        success=True,
        # Always expose our merchant reference: it owns the FundingIntent and is
        # therefore the stable reference that /fund/verify can safely settle.
        reference=reference,
        authorization_url=result.get("authorization_url", ""),
        mock=result.get("mock", False),
    )


@api
@require_user
def fund_verify(request):
    """POST /api/fund/verify/ {access_token, reference}
    -> {success, wallet} — confirms with the rail and credits once.
    """
    raw_reference = request.data.get("reference")
    if not isinstance(raw_reference, str):
        return fail("Reference is required")
    reference = raw_reference.strip()
    if not reference:
        return fail("Reference is required")

    # Require an intent owned by this caller before touching the provider.  Calling
    # verify for an unbound reference can expose provider state and, historically,
    # was followed by a false "Wallet funded" response even though settlement was
    # a no-op.
    intent = FundingIntent.objects.filter(
        reference=reference, user=request.user_obj,
    ).first()
    if intent is None:
        return fail("Reference not found", status=404)

    def credited_transaction():
        return Transaction.objects.filter(
            reference=intent.reference,
            user=intent.user,
            direction=Transaction.IN,
            transaction_status=Transaction.SUCCESS,
            amount=intent.amount,
        ).first()

    meta = intent.meta if isinstance(intent.meta, dict) else {}
    review = meta.get("funding_review")
    if isinstance(review, dict) and review.get("active") is True:
        return ok(
            pending=True, code="funding_review", reference=reference,
            message="This funding request is under review. Your wallet outcome has not been confirmed yet.",
        )

    # An idempotent replay succeeds only when both state and the immutable ledger
    # prove the credit exists.  A corrupt PAID/credited flag without its ledger row
    # is held for review rather than shown to the customer as money they do not have.
    if intent.credited:
        prior = credited_transaction()
        if prior is not None:
            wallet = get_or_create_wallet(request.user_obj)
            return ok(
                success=True, funded=True, duplicate=True, reference=reference,
                wallet=str(wallet.balance), message="Wallet funding already confirmed",
            )
        hold_funding_review(
            reference, reason="credited_without_ledger",
            observed_currency="NGN", evidence={"source": "wallet_verify"},
        )
        return ok(
            pending=True, code="funding_review", reference=reference,
            message="Funding confirmation requires review before it can be shown in your wallet.",
        )

    provider = meta.get("provider", "")
    # Some hosted rails return their own verification reference. Keep exposing
    # our merchant reference to the client (it owns the FundingIntent), but use
    # the stored provider reference when asking that rail to verify.
    provider_reference = str(meta.get("provider_reference") or reference)
    try:
        result = funding_verify(provider_reference, provider=provider)
    except Exception as exc:  # noqa: BLE001 - verification outcome is ambiguous
        log.exception("fund_verify_outcome_unknown ref=%s", reference)
        hold_funding_review(
            reference, reason="verify_outcome_unknown", observed_currency="NGN",
            evidence={"source": "wallet_verify",
                      "provider_reference": provider_reference},
        )
        return ok(
            pending=True, code="funding_review", reference=reference,
            message="Payment verification is still pending. Your wallet has not been credited yet.",
        )
    if not isinstance(result, dict):
        hold_funding_review(
            reference, reason="invalid_verify_response", observed_currency="NGN",
            evidence={"source": "wallet_verify",
                      "provider_reference": provider_reference},
        )
        return ok(
            pending=True, code="funding_review", reference=reference,
            message="Payment verification requires review. Your wallet has not been credited yet.",
        )
    if not result.get("success"):
        if result.get("pending"):
            return ok(
                pending=True, reference=reference,
                message=result.get("message", "Payment verification is still pending."),
            )
        return fail(result.get("message", "Payment not successful"), status=402)

    try:
        verified_amount = Decimal(str(result.get("amount_naira")))
        if not verified_amount.is_finite() or verified_amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        hold_funding_review(
            reference, reason="invalid_verified_amount",
            observed_amount=result.get("amount_naira"),
            observed_currency=result.get("currency"),
            evidence={"source": "wallet_verify",
                      "provider_reference": provider_reference},
        )
        return ok(
            pending=True, code="funding_review", reference=reference,
            message="Payment amount requires review. Your wallet has not been credited yet.",
        )
    currency = str(result.get("currency") or "").strip().upper()
    if verified_amount != intent.amount or currency != "NGN":
        reason = "amount_mismatch" if verified_amount != intent.amount else "currency_mismatch"
        hold_funding_review(
            reference, reason=reason, observed_amount=verified_amount,
            observed_currency=currency,
            evidence={"source": "wallet_verify",
                      "provider_reference": provider_reference},
        )
        return ok(
            pending=True, code="funding_review", reference=reference,
            message="Payment details require review. Your wallet has not been credited yet.",
        )

    settled = settle_funding(
        reference, verified_amount=verified_amount, verified_currency=currency,
        evidence={"source": "wallet_verify",
                  "provider_reference": provider_reference},
    )
    if settled is None:
        # A concurrent callback may have won the row lock.  Re-read both intent and
        # ledger before calling that a successful replay; every other no-op remains
        # visibly pending for review.
        intent.refresh_from_db()
        prior = credited_transaction() if intent.credited else None
        if prior is None:
            hold_funding_review(
                reference, reason="settlement_noop",
                observed_amount=verified_amount, observed_currency=currency,
                evidence={"source": "wallet_verify",
                          "provider_reference": provider_reference},
            )
            return ok(
                pending=True, code="funding_review", reference=reference,
                message="Funding requires review. Your wallet has not been credited yet.",
            )
    wallet = get_or_create_wallet(request.user_obj)
    return ok(
        success=True, funded=True, duplicate=settled is None, reference=reference,
        wallet=str(wallet.balance), message="Wallet funded",
    )



@api
@ratelimit("statement_request", limit=6, window=3600)
@require_user
def statement_request(request):
    """POST /api/wallet/statement/request/
    {access_token, from, to, file_type: pdf|excel, include_address?, email?}
    -> {success, message}

    Renders the customer's own Zitch ledger for a date range and EMAILS it as a
    file. Emailed rather than returned inline on purpose: a statement is the one
    export people forward to a landlord, an embassy or an accountant, and a
    mailbox is where it needs to end up anyway. It also keeps a potentially large
    render off the response path.

    The address is included only when the customer asked for it — it is on the
    document for visa and loan applications that require it, and printing a home
    address on every statement someone forwards is a privacy leak, not a feature.
    """
    import re
    from datetime import datetime, timedelta

    from django.utils import timezone

    from utility.providers import send_email

    from .statement import build_statement_xlsx

    user = request.user_obj
    wallet = get_or_create_wallet(user)

    file_type = str(request.data.get("file_type") or "pdf").lower()
    if file_type not in ("pdf", "excel"):
        return fail("Choose either a PDF or an Excel file", status=400)

    def _day(value, default):
        # Missing dates retain the documented default. An explicit invalid date
        # must never silently select another period or escape as a server error.
        value = str(value if value is not None else "").strip() or default
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Invalid statement date format")
        return value, datetime.strptime(value, "%Y-%m-%d")

    today = timezone.localdate()
    try:
        date_to, end_day = _day(request.data.get("to"), today.strftime("%Y-%m-%d"))
        date_from, start_day = _day(request.data.get("from"), (today - timedelta(days=30)).strftime("%Y-%m-%d"))
        start = timezone.make_aware(start_day)
        # Add the inclusive end day inside the guard too: 9999-12-31 cannot
        # represent the following midnight and otherwise raises OverflowError.
        end = timezone.make_aware(end_day + timedelta(days=1))
    except (ValueError, OverflowError):
        return fail("Enter valid start and end dates in YYYY-MM-DD format", status=400)
    if date_from > date_to:
        return fail("The end date can't come before the start date", status=400)

    # The customer may send the statement somewhere other than their sign-in
    # address (an accountant, a landlord's agent), so an explicit `email` wins —
    # but it is only ever a destination, never a change to the account.
    email = str(request.data.get("email") or user.email or "").strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", email):
        return fail("Add a valid email address to send the statement to", status=400)

    # Inclusive of the end DAY, not the end instant: "to 14 Aug" that silently
    # dropped everything after midnight on the 14th would look like missing money.
    txns = list(customer_visible_transactions(
        user.transactions.filter(created__gte=start, created__lt=end)
    ).order_by("-created")[:1000])
    reviews = _transaction_reviews(txns)

    from .models import Transaction

    def state(t) -> str:
        if t.pk in reviews:
            return "under_review"
        if t.transaction_status == Transaction.SUCCESS:
            return "success"
        if t.transaction_status == Transaction.FAILED:
            return "failed"
        return "pending"

    rows = []
    for t in txns:
        credit = t.direction == Transaction.IN
        rows.append({
            "date": timezone.localtime(t.created).strftime("%d %b %Y, %I:%M %p"),
            "label": (t.service or "").strip() or ("Credit" if credit else "Debit"),
            "amount": f"₦{t.amount:,.2f}",
            "signed_amount": f"{'' if credit else '-'}{t.amount}",
            "direction": "in" if credit else "out",
            "sign": "＋" if credit else "－",
            "status": state(t),
            "reference": t.reference or "",
        })

    period = f"{date_from} to {date_to}"
    if file_type == "excel":
        data = build_statement_xlsx(rows)
        filename = f"Zitch-Statement-{date_from}_{date_to}.xlsx"
    else:
        from whatsapp.receipt import render_statement_pdf

        address = ""
        if str(request.data.get("include_address") or "").lower() in ("1", "true", "yes"):
            address = " ".join(x for x in (
                getattr(user, "address", "") or "", getattr(user, "city", "") or "",
                getattr(user, "state", "") or "") if x).strip()
        data = render_statement_pdf(
            rows,
            balance=f"₦{wallet.balance:,.2f}",
            generated=timezone.localtime().strftime("%d %b %Y, %I:%M %p"),
            heading=f"{len(rows)} transaction{'s' if len(rows) != 1 else ''}",
            holder=(user.get_full_name() or "").strip() or user.email,
            account=wallet.account_number or "",
            period=period,
            address=address,
        )
        filename = f"Zitch-Statement-{date_from}_{date_to}.pdf"

    sent = send_email(
        email,
        f"Your Zitch account statement ({period})",
        f"Your Zitch account statement for {period} is attached.\n\n"
        f"{len(rows)} transaction{'s' if len(rows) != 1 else ''}. "
        "If you didn't request this, please contact support@zitch.ng immediately.",
        attachments=[{"filename": filename, "content": data}],
    )
    if not sent.get("success"):
        return fail("We couldn't email your statement just now. Please try again.", status=502)
    return ok(success=True, message=f"Statement sent to {email}",
              from_date=date_from, to_date=date_to, count=len(rows))


# --------------------------- ZITCH-TO-ZITCH TRANSFER ---------------------------
def _find_recipient(identifier: str):
    """Resolve a Zitch recipient by phone (or @tag/email)."""
    from accounts.models import User
    from django.db.models import Q

    identifier = (identifier or "").strip().lstrip("@")
    if not identifier:
        return None
    return User.objects.filter(
        Q(phone=identifier) | Q(username=identifier) | Q(email__iexact=identifier)
    ).first()


@api
@ratelimit("resolve_recipient", limit=20, window=60)
@require_user
def resolve_recipient(request):
    """POST /api/transfer/resolve/ {access_token, identifier}
    -> {success, name, phone, recipient_key} — name confirmation before sending.

    Rate-limited: without a throttle this is an unauthenticated-cost enumeration
    oracle that confirms whether any phone/@tag/email maps to a Zitch user and
    discloses the holder's name.
    """
    recipient = _find_recipient(request.data.get("identifier", ""))
    if recipient is None:
        return fail("No Zitch user found with that detail", status=404)
    if recipient.id == request.user_obj.id:
        return fail("You can't send money to yourself", status=400)
    name = (recipient.get_full_name() or recipient.phone or "Zitch user").strip()
    # The client uses this opaque, immutable identity only to bind a durable
    # idempotency attempt to the resolved recipient. Phone/email/username are
    # mutable aliases and two aliases for the same customer must not mint two
    # transfer keys after an ambiguous response. The UUID is persisted on the
    # user, so it also survives SECRET_KEY rotation and regional migration.
    from accounts.views import spend_account_namespace
    recipient_key = f"user_{spend_account_namespace(recipient).replace('-', '')}"
    return ok(
        success=True,
        name=name,
        phone=recipient.phone or "",
        recipient_key=recipient_key,
    )


@api
@ratelimit("transfer_send", limit=12, window=60)
@require_user
def transfer_send(request):
    """POST /api/transfer/send/ {access_token, identifier, amount, transaction_pin, note?}
    -> {success, wallet, reference}
    """
    sender = request.user_obj
    data = request.data
    amount = parse_amount(data.get("amount"))
    if amount is None:
        return fail("Enter a valid amount")
    if amount < MIN_TRANSFER:
        return fail(f"Minimum transfer is ₦{MIN_TRANSFER:,.0f}")

    raw_key = data.get("idempotency_key")
    if not isinstance(raw_key, str) or not raw_key.strip():
        return fail(
            "A stable idempotency key is required for Zitch transfers",
            status=400,
            code="idempotency_key_required",
        )

    recipient = None
    opaque_recipient = str(data.get("recipient_key") or "").strip()
    if opaque_recipient:
        match = re.fullmatch(r"user_([0-9a-fA-F]{32})", opaque_recipient)
        if match is None:
            return fail(
                "Invalid recipient key. Resolve the recipient again before sending.",
                status=400,
                code="invalid_recipient_key",
            )
        from accounts.models import User

        recipient = User.objects.filter(
            spend_namespace=match.group(1),
        ).first()
    if recipient is None and not opaque_recipient:
        recipient = _find_recipient(data.get("identifier", ""))
    if recipient is None:
        return fail("No Zitch user found with that detail", status=404)
    if recipient.id == sender.id:
        return fail("You can't send money to yourself", status=400)

    key = spend_key(raw_key, sender, "p2p", recipient.id, amount)
    replay = idempotent_replay(existing_for_key(sender, key))
    if replay:
        return replay

    # For a NEW transfer, the alias shown to the customer must still resolve to
    # the opaque account they confirmed. A retry already returned above, so a
    # later username/email change cannot hide its durable result.
    if opaque_recipient:
        alias_recipient = _find_recipient(data.get("identifier", ""))
        if alias_recipient is None or alias_recipient.id != recipient.id:
            return fail(
                "The recipient detail changed. Resolve the recipient again before sending.",
                status=409,
                code="recipient_changed",
            )

    pin_err = verify_transaction_pin(sender, data.get("transaction_pin"))
    if pin_err:
        return pin_err
    limit_err = check_send_limits(sender, amount)
    if limit_err:
        return limit_err

    # Daily transfer cap (after replay so a retried transfer replays cleanly).
    daily_err = check_daily_limit(sender, amount, "transfer")
    if daily_err:
        return daily_err

    from transfers.services import PayoutError

    try:
        debit_txn, _ = transfer(sender, recipient, amount, note=data.get("note", ""),
                                idempotency_key=key, channel="app", expected_fee=data.get("expected_fee"))
    except DuplicateTransaction:
        return idempotent_replay(existing_for_key(sender, key)) or fail("Duplicate request", status=409)
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)
    except LimitExceeded as exc:
        return fail(str(exc), status=403, code="limit_exceeded")
    except PayoutError as exc:
        if exc.kind == "duplicate":
            return idempotent_replay(existing_for_key(sender, key)) or fail(
                "This transfer was already submitted. Check your transaction history.",
                status=409, code="duplicate", duplicate=True,
            )
        if exc.kind == "insufficient":
            return fail("Insufficient wallet balance", status=402)
        if exc.kind == "limit_exceeded":
            return fail(exc.message, status=403, code="limit_exceeded")
        prior = existing_for_key(sender, key)
        if exc.kind == "state_conflict":
            return ok(pending=True, under_review=True,
                      reference=prior.reference if prior else "",
                      message="Your transfer is under review. Do not send it again; we will update its final status.")
        return fail(
            (customer_safe_failure({"message": exc.message}, service="Transfer")
             if exc.kind == "provider" else exc.message),
            status=422, code=f"transfer_{exc.kind}",
            reference=prior.reference if prior else "",
            **({"refunded": True} if exc.kind == "provider" else {"not_charged": True}),
        )

    wallet = get_or_create_wallet(sender)
    quarantine = (debit_txn.meta or {}).get("wema_reversal_quarantine") or {}
    under_review = isinstance(quarantine, dict) and quarantine.get("active") is True
    if debit_txn.transaction_status == Transaction.PENDING or under_review:
        return ok(pending=True, under_review=under_review, wallet=str(wallet.balance),
                  reference=debit_txn.reference,
                  narration=(debit_txn.meta or {}).get("narration", ""),
                  message=("Your transfer is under review. Do not send it again."
                           if under_review else
                           "Your transfer is processing. We will update its final status after the bank confirms it."))
    return ok(success=True, wallet=str(wallet.balance), reference=debit_txn.reference,
              narration=(debit_txn.meta or {}).get("narration", ""), message="Money sent")
