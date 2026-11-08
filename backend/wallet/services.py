"""Wallet ledger operations — the heart of the money logic.

Every debit/credit goes through here so balance changes and ledger rows are
always written together, atomically, with row locking to prevent double-spend.
"""
import hashlib
import json
import logging
import re
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction as db_transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from .models import (
    BillFundingBinding,
    BillFundingRefund,
    FundingIntent,
    ReversalEvidence,
    ReversalEvidenceObservation,
    ReversalEvidenceResolution,
    Transaction,
    Wallet,
)

log = logging.getLogger("wallet")
INTERNAL_EVIDENCE_META_KEY = "internal_evidence"


class InsufficientFunds(Exception):
    pass


class LimitExceeded(Exception):
    """A spend cap was breached, detected while holding the wallet row lock.

    Views check the same caps up front and answer with a proper 403, so reaching
    this means two spends raced: both read the same "spent today", both passed, and
    the lock serialised them here. Carries the user-facing message so the caller can
    surface it verbatim rather than inventing a second wording.
    """


class DuplicateTransaction(Exception):
    """A spend was retried with an idempotency key already used — the caller
    should replay the original outcome instead of debiting again."""


def existing_for_key(user, key: str) -> Transaction | None:
    """The prior ledger row for this user + idempotency key, if any."""
    if not key:
        return None
    prior = Transaction.objects.filter(user=user, idempotency_key=str(key)).first()
    if prior is not None:
        prior._requested_idempotency_fingerprint = getattr(key, "fingerprint", "")
    return prior


def with_idempotency_fingerprint(meta: dict | None, key) -> dict:
    """Copy metadata and bind it to the material request represented by ``key``."""
    out = dict(meta or {})
    fingerprint = str(getattr(key, "fingerprint", "") or "")
    if fingerprint:
        out["idempotency_fingerprint"] = fingerprint
    return out


def customer_visible_transactions(queryset):
    """Remove internal control/evidence rows from customer-facing queries."""
    # ``exclude(meta__internal_evidence=True)`` is subtly wrong on JSON columns:
    # a missing key evaluates to SQL NULL, and ``NOT (NULL = true)`` is still NULL,
    # so SQLite (and some Postgres query shapes) drop ordinary rows too.  Select
    # missing/null keys explicitly, plus values that are present but not true.
    lookup = f"meta__{INTERNAL_EVIDENCE_META_KEY}"
    return queryset.filter(
        Q(**{f"{lookup}__isnull": True}) | ~Q(**{lookup: True})
    )


@db_transaction.atomic
def merge_transaction_meta(txn: Transaction, updates: dict, *, remove=()) -> dict:
    """Merge metadata into the latest locked ledger row.

    Provider calls deliberately run without a database lock.  A callback or
    reconciler can therefore add safety metadata while that network request is in
    flight.  Saving a caller's stale ``txn.meta`` afterwards would erase those
    fields — including an active reversal quarantine.  Every post-network or
    annotation-only metadata write should use this helper so it locks, rereads and
    merges instead of replacing the current JSON document.
    """
    current = Transaction.objects.select_for_update().get(pk=txn.pk)
    meta = dict(current.meta or {})
    for key in remove:
        meta.pop(key, None)
    meta.update(dict(updates or {}))
    current.meta = meta
    current.save(update_fields=["meta"])
    txn.meta = meta
    return meta


def make_reference(prefix: str = "ZTCH") -> str:
    return f"{prefix}{secrets.token_hex(6).upper()}"


def get_or_create_wallet(user) -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


BANK_HISTORY_REVIEW_MESSAGE = (
    "Your account balance needs review before you can spend. "
    "Please contact Zitch Support. Incoming transfers and refunds "
    "can still reach your account.")


def bank_history_review_required(wallet) -> bool:
    from banking.models import BankAccount
    bank = BankAccount.objects.filter(wallet=wallet).first()
    return bool(bank and bank.status == "review")


def customer_funding_account(user) -> dict:
    from banking.accounts import account_payload
    return account_payload(user)


def wallet_expected_balance(user_id) -> Decimal:
    """The balance implied by the append-only ledger for this user.

    The ledger state machine:  expected = sum(IN, Successful) - sum(OUT, Pending
    or Successful). Debits deduct at PENDING (a FAILED debit is refunded back);
    credits are only ever written Successful. This is the single source of truth
    for both integrity checks: the internal one (ledger vs stored balance) and the
    external one (ledger vs the bank's NUBAN balance).

    Filtered to NGN, matching settlement_report._owed. Both callers compare this
    against a NAIRA figure — Wallet.balance and the Wema NUBAN balance — so summing
    an FX row into it is comparing two different currencies as though they were one
    number. The first customer to convert any currency would make integrity_check
    and reconcile_balances go red permanently, and a permanently-red alarm hides the
    real double-credit it exists to catch. Non-NGN holdings live in their own
    per-currency wallets (see CurrencyWallet).
    """
    credits = (Transaction.objects
               .filter(user_id=user_id, direction=Transaction.IN,
                       transaction_status=Transaction.SUCCESS, currency="NGN")
               .aggregate(s=Sum("amount"))["s"] or Decimal("0"))
    debits = (Transaction.objects
              .filter(user_id=user_id, direction=Transaction.OUT, currency="NGN")
              .filter(Q(transaction_status=Transaction.PENDING)
                      | Q(transaction_status=Transaction.SUCCESS))
              .aggregate(s=Sum("amount"))["s"] or Decimal("0"))
    return credits - debits


def wallet_balance_payload(user, wallet=None) -> dict:
    from banking.accounts import account_for, account_payload, spendable, historical_balance
    from wallet.models import RetiredFundingArchive
    wallet = wallet or get_or_create_wallet(user)
    bank = account_for(user)
    book_available = spendable(wallet) if bank else Decimal("0.00")
    available = Decimal("0.00")
    if bank and account_payload(user)["spending_available"]:
        available = max(Decimal("0.00"), min(book_available, bank.available_balance))
    held_archive = RetiredFundingArchive.objects.filter(funding__user=user).aggregate(
        total=Sum("funding__amount"))["total"] or Decimal("0.00")
    historical = historical_balance(wallet) + held_archive
    return {"balance": wallet.balance + held_archive, "available_balance": available,
            "historical_balance": historical, "legacy_funding_balance": historical,
            "restricted_balance": max(Decimal("0.00"), book_available - available)}


def customer_spendable_balance(user, *, wallet=None) -> Decimal:
    return wallet_balance_payload(user, wallet=wallet)["available_balance"]



@db_transaction.atomic
def debit(user, amount, service: str, meta: dict | None = None, reference: str | None = None,
          idempotency_key: str = "", enforce_limits: bool = True) -> Transaction:
    """Atomically debit the wallet and write a PENDING ledger row.

    Raises InsufficientFunds if the balance can't cover `amount`. With an
    `idempotency_key`, a duplicate (same user + key) raises DuplicateTransaction
    and the debit is rolled back, so a retried/raced request never debits twice.
    The caller flips the row to Successful/Failed after the provider responds.

    Raises LimitExceeded when a spend cap is breached. That check runs HERE, inside
    the row lock, and not only in the view: read outside a lock, the daily caps and
    the velocity brake are advisory, because two concurrent requests both read the
    same "spent today" and both pass. The lock the balance check already relies on
    serialises them, so the same reasoning that prevents an overdraw now also
    prevents a cap being raced. Pass enforce_limits=False only for a movement that
    is not customer-initiated spend (a reversal, a settlement, an operator action).
    """
    amount = Decimal(str(amount))
    wallet = Wallet.objects.select_for_update().get(user=user)
    bill_source = None
    if enforce_limits:
        if is_biller_service(service):
            bill_source = _biller_funding_context(user, amount, wallet=wallet)
        else:
            assert_customer_spending_available(user)
    if wallet.balance < amount:
        raise InsufficientFunds("Insufficient wallet balance")
    if enforce_limits:
        if customer_spendable_balance(user, wallet=wallet) < amount:
            raise InsufficientFunds("Insufficient available balance")
    if enforce_limits:
        from common.http import spend_limit_error   # local: common.http imports from here
        breach = spend_limit_error(user, amount, service)
        if breach:
            raise LimitExceeded(breach)
    wallet.balance -= amount
    wallet.save(update_fields=["balance", "updated"])
    try:
        with db_transaction.atomic():  # savepoint: contain the unique violation
            txn = Transaction.objects.create(
                user=user,
                service=service,
                amount=amount,
                direction=Transaction.OUT,
                transaction_status=Transaction.PENDING,
                reference=reference or make_reference(),
                meta=with_idempotency_fingerprint({**(meta or {}), **({"provider": "9psb"} if enforce_limits else {})}, idempotency_key),
                idempotency_key=idempotency_key,
            )
            if bill_source is not None:
                source, approval = bill_source
                BillFundingBinding.objects.create(transaction=txn, source_account=source,
                                                  approval_reference=approval)
            return txn
    except IntegrityError:
        if idempotency_key:
            raise DuplicateTransaction(idempotency_key)
        raise


@db_transaction.atomic
def credit(user, amount, service: str, meta: dict | None = None, reference: str | None = None,
           idempotency_key: str = "") -> Transaction:
    """Atomically credit the wallet and write a Successful inbound ledger row.

    With an `idempotency_key`, a duplicate (same user + key) raises
    DuplicateTransaction and the credit is rolled back, so a retried/raced
    request never credits twice. Server-originated credits (settlements,
    funding) pass no key and are unconstrained.
    """
    amount = Decimal(str(amount))
    wallet = Wallet.objects.select_for_update().get(user=user)
    wallet.balance += amount
    wallet.save(update_fields=["balance", "updated"])
    try:
        with db_transaction.atomic():  # savepoint: contain the unique violation
            txn = Transaction.objects.create(
                user=user,
                service=service,
                amount=amount,
                direction=Transaction.IN,
                transaction_status=Transaction.SUCCESS,
                reference=reference or make_reference("ZFND"),
                meta=with_idempotency_fingerprint(meta, idempotency_key),
                idempotency_key=idempotency_key,
            )
            from banking.accounts import retain_historical_refund
            retain_historical_refund(txn)
            return txn
    except IntegrityError:
        if idempotency_key:
            raise DuplicateTransaction(idempotency_key)
        raise


@db_transaction.atomic
def refund(txn: Transaction) -> bool:
    """Reverse a PENDING debit exactly once and mark it Failed.

    The transaction row is the state-machine lock. Locking only the wallet lets
    two failure handlers both credit it, and lets a stale request refund a row a
    callback already settled Successful. Returns True only for the caller that
    actually performed the transition.
    """
    _lock_bill_funding_wallet(txn.pk)
    current = Transaction.objects.select_for_update().select_related("user").get(pk=txn.pk)
    if current.transaction_status != Transaction.PENDING:
        return False
    wallet = Wallet.objects.select_for_update().get(user=current.user)
    from banking.accounts import retain_historical_refund
    retain_historical_refund(current)
    wallet.balance += current.amount
    wallet.save(update_fields=["balance", "updated"])
    current.transaction_status = Transaction.FAILED
    current.save(update_fields=["transaction_status"])
    _release_bill_funding(current)
    txn.transaction_status = Transaction.FAILED
    return True


@db_transaction.atomic
def settle_or_refund(txn: Transaction, result: dict) -> str:
    """Resolve a PENDING provider-backed debit from the provider's result.

    Returns one of:
      "success" — provider delivered; row marked Successful.
      "pending" — outcome unknown (e.g. a send timeout); row left Pending and
                  flagged ``meta.reconcile`` so the reconcile job requeries it.
                  The money stays debited — we never refund a maybe-delivered
                  purchase, which would leak money if it actually went through.
      "failed"  — definitive failure; wallet refunded, row marked Failed.

    Locks the row and guards on its status, so a later reconcile call can't
    double-settle (credit twice / mark a delivered purchase failed).
    """
    _lock_bill_funding_wallet(txn.pk)
    txn = Transaction.objects.select_for_update().get(pk=txn.pk)
    if _active_reversal_quarantine(txn):
        # A correlated bank-history row says this payout cannot safely be
        # settled/refunded automatically (partial return or a return previously
        # credited as funding).  Callback, portal and cron paths all converge on
        # this state-machine guard, so none can bypass the hold.
        return "quarantined"
    if txn.transaction_status == Transaction.SUCCESS:
        return "success"
    if txn.transaction_status == Transaction.FAILED:
        if result.get("success") and is_bank_payout(txn):
            # A refund (including an exact bank-history return) won the row lock,
            # then an in-flight provider call reported delivery. Neither fact may
            # overwrite the other. Re-open/create a durable conflict and keep the
            # retry key non-terminal until two operators classify it.
            _hold_provider_success_after_refund_locked(txn)
            return "quarantined"
        return "failed"

    meta = dict(txn.meta or {})
    if result.get("success"):
        meta.pop("reconcile", None)
        meta.update({k: v for k, v in result.items() if k != "raw"})
        txn.meta = meta
        txn.transaction_status = Transaction.SUCCESS
        txn.save(update_fields=["transaction_status", "meta"])
        return "success"
    if result.get("pending"):
        changed = False
        if not meta.get("reconcile"):
            meta["reconcile"] = True
            changed = True
        # Persist the fulfilling rail so reconcile requeries against the SAME rail
        # against the partner-bank VAS status endpoint.
        for k in ("vas_rail", "vas_type"):
            if k in result and meta.get(k) != result[k]:
                meta[k] = result[k]
                changed = True
        if changed:
            txn.meta = meta
            txn.save(update_fields=["meta"])
        return "pending"

    # Definitive failure: refund and mark Failed.
    wallet = Wallet.objects.select_for_update().get(user=txn.user)
    from banking.accounts import retain_historical_refund
    retain_historical_refund(txn)
    wallet.balance += txn.amount
    wallet.save(update_fields=["balance", "updated"])
    meta.pop("reconcile", None)
    meta["failure"] = result.get("message", "")
    if result.get("status"):
        meta["failure_status"] = result["status"]
    txn.meta = meta
    txn.transaction_status = Transaction.FAILED
    txn.save(update_fields=["transaction_status", "meta"])
    _release_bill_funding(txn)
    return "failed"


def _release_bill_funding(txn):
    """Called only within the existing once-only refund transaction."""
    binding = BillFundingBinding.objects.filter(transaction=txn).first()
    if binding is not None:
        BillFundingRefund.objects.get_or_create(binding=binding)


def _lock_bill_funding_wallet(transaction_id):
    """Bound bill paths share wallet -> transaction order with bank authorization."""
    user_id = BillFundingBinding.objects.filter(transaction_id=transaction_id).values_list(
        "transaction__user_id", flat=True).first()
    if user_id is not None:
        Wallet.objects.select_for_update().get(user_id=user_id)


#: A provider saying "insufficient balance" is ALWAYS talking about our float.
#:
#: run_provider_purchase debits the customer BEFORE it calls the provider, and
#: debit() raises InsufficientFunds when their wallet cannot cover the amount. So
#: by the time a provider answers at all, the customer's money has already moved.
#: A balance complaint coming back from that call is therefore, by construction,
#: about the balance WE hold with them — it cannot be the customer's.
_PROVIDER_FLOAT_RE = re.compile(
    r"""(
        insufficient                    # "insufficient balance" / "...funds"
      | \blow\s+balance\b
      | \bbalance\s+is\s+(too\s+)?low\b
      | \bwallet\s+is\s+empty\b
      | \btop[\s-]?up\s+your\b
    )""",
    re.I | re.X,
)

#: What the customer is told instead. It does three things the raw text did not:
#: names it as ours, does not assert any balance, and does not imply they did
#: anything wrong. The caller appends "You were not charged."
PROVIDER_FLOAT_MESSAGE = (
    "this is temporarily unavailable on our side, not a problem with your account"
)

#: The gateway refusing us the PRODUCT, in the second person again. ALAT answers an
#: un-entitled product with "You've not been profiled to use this service" — "you"
#: being Zitch, not the customer, who reads it as their own account being ineligible
#: and has no way to act on it. Same harm as the float sentence above and the same
#: remedy: it is an entitlement fault only we can clear, so say so plainly.
_PROVIDER_REFUSED_RE = re.compile(
    r"""(
        \bnot\s+(been\s+)?profiled\b
      | \bnot\s+subscribed\b
      | \bsubscription\s+(key\s+)?(is\s+)?(invalid|not\s+found)\b
      | \b(access\s+denied|unauthori[sz]ed|not\s+authori[sz]ed|forbidden)\b
      # "Authentication Failed" reached a customer verbatim on a ₦55 top-up. It is
      # OUR credentials the bank is rejecting, but it reads as the customer's own
      # sign-in having failed on a screen they reached by passing their PIN — so it
      # is the most alarming of the lot and the least actionable.
      | \bauthentication\s+fail(ed|ure)\b
      | \binvalid\s+credentials?\b
    )""",
    re.I | re.X,
)
PROVIDER_REFUSED_MESSAGE = (
    "this service is temporarily unavailable on our side, not a problem with your "
    "account"
)


def customer_safe_failure(result: dict, *, service: str = "",
                          fallback: str = "please try again") -> str:
    """The reason a purchase failed, in terms that are TRUE FOR THE CUSTOMER.

    Relaying the provider's own sentence verbatim was actively harmful here.
    partner-bank VAS phrases an empty float in the second person — "Your wallet balance
    (NGN12.25) is insufficient to make this airtime purchase of NGN100" — so a
    customer who had just been shown "Available balance ₦1,000.00" on the confirm
    card was told, seconds later and by their bank, that they had ₦12.25. Both
    numbers were real; only one was theirs. On a money product there is very
    little worse to say by accident, and it was said on every attempt while our
    provider float sat empty.

    The raw text is not lost: settle_or_refund keeps it on the row as
    meta["failure"], the operator console renders that, and an exhausted float
    pages from here, because it is an outage someone has to act on — a top-up,
    not a code change. It is only the CUSTOMER who must never be handed it.

    Every other provider message still passes through unchanged. "Invalid phone
    number for MTN" or "meter not found" is the customer's to act on, and
    replacing those with something vague would trade one bad failure for another.
    """
    message = str((result or {}).get("message") or "").strip()
    if message and _PROVIDER_FLOAT_RE.search(message):
        rail = service or (result or {}).get("vas_rail") or "?"
        # Paged, not just logged. An exhausted float fails EVERY airtime, data and
        # bill purchase on the platform, and it fails them quietly: each customer
        # is refunded and sees one message, so nothing accumulates into a signal
        # and the first real report is a customer complaining. It is also the one
        # class of failure no code change can clear — somebody has to top the
        # account up. Same helper the WhatsApp dead-letter path pages through, and
        # the same rule: alerting must never break the purchase that found it.
        try:
            from utility.alerts import alert

            alert("VAS provider float is exhausted - every airtime/data/bill "
                  "purchase is failing until the provider account is topped up",
                  level="error", rail=rail, provider_said=message[:200])
        except Exception:  # noqa: BLE001
            log.exception("vas_float_alert_failed rail=%s", rail)
        return PROVIDER_FLOAT_MESSAGE
    if message and _PROVIDER_REFUSED_RE.search(message):
        # Paged for the same reason the float is: it fails EVERY purchase of the
        # product, silently, one refunded customer at a time — and no code change
        # clears it, someone has to get the tenant entitled for the product.
        try:
            from utility.alerts import alert

            alert("VAS product is not entitled for this tenant - the gateway is "
                  "refusing every purchase until the subscription is provisioned",
                  level="error", service=service or "?", provider_said=message[:200])
        except Exception:  # noqa: BLE001
            log.exception("vas_refused_alert_failed service=%s", service)
        return PROVIDER_REFUSED_MESSAGE
    return message or fallback


def run_provider_purchase(user, amount, service: str, meta: dict, provider_call,
                          idempotency_key: str = ""):
    """Debit the wallet (PENDING) → call the provider → settle the row.

    ``provider_call(reference)`` receives the ledger reference to use as the
    provider's idempotency key and returns the provider result dict. The network
    call runs OUTSIDE the debit transaction, so no row lock is held during I/O.
    With an `idempotency_key`, a duplicate request raises DuplicateTransaction
    before any debit or provider call. Returns ``(status, txn, result)`` where
    status is the settle_or_refund code. Raises InsufficientFunds (-> 402).

    The debit is flagged ``meta.reconcile`` up front — committed atomically with
    the wallet deduction, BEFORE the provider call. The debit commits on its own
    (this function isn't wrapped in an outer transaction, by design, so no lock is
    held across I/O), so if the worker dies during the provider call or the
    settle write (a window up to the provider timeout), the committed PENDING row
    would otherwise carry no reconcile flag and the sweep — which filters on
    ``meta__reconcile=True`` — would never find it: money debited, never settled
    or refunded, stuck forever. Pre-flagging makes every orphan discoverable; the
    happy path clears the flag in ``settle_or_refund`` on a definite outcome.
    """
    # Replay an existing request even if the bank's current entitlement is down.
    # debit still arbitrates concurrent requests via its unique idempotency key.
    if existing_for_key(user, idempotency_key) is not None:
        raise DuplicateTransaction(idempotency_key)
    if is_biller_service(service):
        from utility.providers import _VAS_UNAVAILABLE, vas_can_settle

        kind = re.match(r"[a-z]+", str(service).strip(), re.I).group().lower()
        product = "airtime" if kind in {"airtime", "data"} else (
            "remita" if kind == "remita" else "bills")
        # A read-only status probe runs before any ledger mutation and outside
        # wallet locks. The provider boundary checks again before sending money.
        can_settle, _reason = vas_can_settle(product)
        if not can_settle:
            raise LimitExceeded(_VAS_UNAVAILABLE)
    reconcile_meta = {**(meta or {}), "reconcile": True,
                      "provider_purchase": True}
    txn = debit(user, amount, service, meta=reconcile_meta, idempotency_key=idempotency_key)
    result = provider_call(txn.reference)
    status = settle_or_refund(txn, result)
    return status, txn, result


# The retired partner bank's mock rail stamped the NUBANs it invented with this,
# which is the only durable trace that an account number was never minted at the bank.
DEMO_ACCOUNT_MARKER = "(demo)"


def is_demo_account(wallet) -> bool:
    """True when this wallet's NUBAN was invented by the MOCK rail.

    Such a number exists nowhere at the bank. It is harmless while the rail is
    mocked, but once live keys are set it is still sitting on the wallet and gets
    sent as the `sourceAccountNumber` of every payout — where the rail's own
    enquiry rejects it, reporting (as ever) that an account number is invalid.
    Nothing about the destination is wrong in that case, which makes it a
    thoroughly misleading failure to debug.
    """
    return DEMO_ACCOUNT_MARKER in (getattr(wallet, "bank_name", "") or "").lower()



BANK_PAYOUT_META_FILTER = (
    Q(meta__has_key="bank")
    | Q(meta__has_key="wema_transfer")
    | Q(meta__has_key="account")
    | Q(meta__has_key="recipient_account")
    | Q(meta__has_key="recipient_account_number")
)


def is_bank_payout(txn) -> bool:
    """True for a bank-transfer (Wema payout) payout, as opposed to a VAS purchase.

    Payout rows used to be identified only by ``meta.bank``. That stranded real
    pending transfers whenever another caller or older deploy persisted the bank
    details under a different durable key, and it also let those rows fall into
    the VAS requery sweep. Treat the Wema transfer marker and recipient account
    fields as bank-payout evidence too; terminal state changes remain idempotent.
    """
    meta = txn.meta or {}
    return bool(
        meta.get("bank")
        or meta.get("wema_transfer")
        or meta.get("account")
        or meta.get("recipient_account")
        or meta.get("recipient_account_number")
    )


VAS_SERVICE_PREFIXES = (
    "airtime", "data", "cable", "electricity", "remita", "betting", "exam",
)


def is_vas_purchase(txn) -> bool:
    """Whether this pending provider debit belongs to the VTU/VAS status rail."""
    meta = txn.meta if isinstance(getattr(txn, "meta", None), dict) else {}
    if meta.get("provider_purchase") is True:
        return True
    # Backward-compatible classifier for rows created before provider_purchase
    # was stamped. Keep it explicit: a generic reconcile flag is shared by bank
    # payouts, card loads and funding and is never sufficient evidence by itself.
    service = str(getattr(txn, "service", "") or "").strip().lower()
    return service.startswith(VAS_SERVICE_PREFIXES)


def pending_vas_purchases(cutoff):
    """PENDING outbound partner-bank VAS purchases due for requery, EXCLUDING bank-transfer
    payouts. The reconcile sweep (cron + on-demand) requeries each row via
    partner-bank VAS requery, which is only correct for partner-bank VAS purchases; bank payouts are
    settled by the reconcile_wema poller, so they must not be swept here."""
    legacy_services = Q()
    for prefix in VAS_SERVICE_PREFIXES:
        legacy_services |= Q(service__istartswith=prefix)
    queryset = Transaction.objects.filter(
        transaction_status=Transaction.PENDING,
        direction=Transaction.OUT,
        meta__reconcile=True,
        created__lte=cutoff,
    ).filter(Q(meta__provider_purchase=True) | legacy_services)
    return queryset.exclude(BANK_PAYOUT_META_FILTER)


def pending_card_fundings(cutoff):
    """Ambiguous card-load POSTs awaiting issuer/operator confirmation."""
    return Transaction.objects.filter(
        transaction_status=Transaction.PENDING,
        direction=Transaction.OUT,
        meta__reconcile=True,
        meta__card_funding=True,
        created__lte=cutoff,
    )


@db_transaction.atomic
def reverse_transfer(reference: str) -> Transaction | None:
    """Refund a settled outbound transfer the provider later failed/reversed.

    Bank payouts are settled optimistically on send, so the disbursement webhook
    is the safety net. Locks the row and guards on status, so only the first
    call (while the row is still Successful/Pending) credits the money back and
    marks it Failed — duplicate webhooks can't double-refund. Returns the row if
    this call performed the reversal, else None.
    """
    txn = (
        Transaction.objects.select_for_update()
        .filter(reference=reference, direction=Transaction.OUT)
        .first()
    )
    if (txn is None or txn.transaction_status == Transaction.FAILED
            or _active_reversal_quarantine(txn)):
        return None
    wallet = Wallet.objects.select_for_update().get(user=txn.user)
    from banking.accounts import retain_historical_refund
    retain_historical_refund(txn)
    wallet.balance += txn.amount
    wallet.save(update_fields=["balance", "updated"])
    txn.transaction_status = Transaction.FAILED
    txn.save(update_fields=["transaction_status"])
    return txn


@db_transaction.atomic
def settle_payout(reference: str) -> Transaction | None:
    """Mark a PENDING outbound transfer Successful once the rail confirms it.

    Payouts the provider returns as PENDING (queued / awaiting authorization) are
    kept PENDING rather than optimistically settled, so the user is never told
    "sent" for money that might not have moved. The disbursement webhook calls
    this on a success/completed event. Locks the row and guards on status, so only
    the first call settles it (a duplicate webhook is a no-op) and a row that was
    already reversed (Failed) is never resurrected. Returns the row if this call
    settled it, else None.
    """
    txn = (
        Transaction.objects.select_for_update()
        .filter(reference=reference, direction=Transaction.OUT)
        .first()
    )
    if txn is None:
        return None
    if txn.transaction_status == Transaction.FAILED:
        if is_bank_payout(txn):
            # The status poll fetched success before a bank-history refund won
            # this row lock. Retain both facts as a conflict instead of silently
            # discarding the provider success or resurrecting a refunded debit.
            _hold_provider_success_after_refund_locked(txn)
        return None
    if (txn.transaction_status != Transaction.PENDING
            or _active_reversal_quarantine(txn)):
        return None
    meta = dict(txn.meta or {})
    meta.pop("reconcile", None)
    txn.meta = meta
    txn.transaction_status = Transaction.SUCCESS
    txn.save(update_fields=["transaction_status", "meta"])
    return txn


FUNDING_REVIEW_DISPOSITIONS = frozenset({
    "confirm_paid",
    "mark_failed",
    # Classify a late conflict on a payment whose exact credit already exists.
    # This disposition records evidence only; it never moves wallet money.
    "confirm_existing_credit",
})


def _funding_evidence(evidence) -> dict:
    """Keep only bounded, non-secret settlement correlation fields."""
    if not isinstance(evidence, dict):
        return {}
    return {
        key: str(evidence.get(key) or "")[:160]
        for key in ("source", "provider_reference", "event_id", "evidence_reference")
        if evidence.get(key) is not None
    }


def _verified_funding_amount(value) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount.is_finite() and amount > 0 else None


def _mark_funding_review_locked(intent: FundingIntent, *, reason: str,
                                observed_amount=None, observed_currency="",
                                evidence=None) -> FundingIntent:
    """Persist why a locked funding intent was refused automatic settlement."""
    meta = dict(intent.meta or {})
    marker = meta.get("funding_review")
    marker = dict(marker) if isinstance(marker, dict) else {}
    reasons = list(marker.get("reasons") or [])
    clean_reason = str(reason or "funding_verification_failed")[:80]
    if clean_reason not in reasons:
        reasons.append(clean_reason)
    marker.update({
        "active": True,
        "reason": clean_reason,
        "reasons": reasons,
        "expected_amount": str(intent.amount),
        "observed_amount": (str(observed_amount)[:80]
                            if observed_amount is not None else ""),
        "currency": str(observed_currency or "")[:12],
        "last_event_at": timezone.now().isoformat(),
        "event_count": int(marker.get("event_count") or 0) + 1,
    })
    clean_evidence = _funding_evidence(evidence)
    if clean_evidence:
        marker["evidence"] = clean_evidence
    meta["funding_review"] = marker
    intent.meta = meta
    intent.save(update_fields=["meta", "updated"])
    return intent


@db_transaction.atomic
def hold_funding_review(reference: str, *, reason: str, observed_amount=None,
                        observed_currency="", evidence=None) -> FundingIntent | None:
    """Public fail-closed path for a provider verification that cannot settle."""
    intent = (FundingIntent.objects.select_for_update()
              .filter(reference=str(reference or "")).first())
    if intent is None:
        return intent
    return _mark_funding_review_locked(
        intent, reason=reason, observed_amount=observed_amount,
        observed_currency=observed_currency, evidence=evidence,
    )


def funding_review_evidence(intent: FundingIntent) -> dict:
    """Stable snapshot bound into a maker/checker funding resolution."""
    meta = intent.meta if isinstance(intent.meta, dict) else {}
    review = meta.get("funding_review")
    return {
        "reference": intent.reference,
        "user_id": intent.user_id,
        "amount": str(intent.amount),
        "status": intent.status,
        "credited": bool(intent.credited),
        "provider": str(meta.get("provider") or ""),
        "provider_reference": str(meta.get("provider_reference") or ""),
        "review": dict(review) if isinstance(review, dict) else {},
    }


def _settle_funding_locked(intent: FundingIntent, *, verified_amount,
                           verified_currency, evidence=None,
                           allow_active_review=False) -> Transaction | None:
    if intent.credited:
        return None

    if intent.status == FundingIntent.FAILED and not allow_active_review:
        # A prior definitive rejection or an operator's mark-failed decision is
        # part of the state machine.  A delayed success is conflicting evidence,
        # not permission to resurrect the intent automatically after the customer
        # may already have started another charge.
        _mark_funding_review_locked(
            intent, reason="success_after_failed",
            observed_amount=verified_amount,
            observed_currency=verified_currency,
            evidence=evidence,
        )
        return None

    existing_review = (intent.meta or {}).get("funding_review") or {}
    if (isinstance(existing_review, dict)
            and existing_review.get("active") is True
            and not allow_active_review):
        # Conflicting provider evidence is sticky.  A later automatic callback
        # cannot silently overrule it; only the maker/checker resolver below may
        # settle while this hold is active.
        log.warning("funding_settlement_held_for_review ref=%s", intent.reference)
        return None

    amount = _verified_funding_amount(verified_amount)
    currency = str(verified_currency or "").strip().upper()
    if amount is None:
        log.warning("funding_settlement_invalid_amount ref=%s amount=%r",
                    intent.reference, verified_amount)
        _mark_funding_review_locked(
            intent, reason="invalid_verified_amount",
            observed_amount=verified_amount, observed_currency=currency,
            evidence=evidence,
        )
        return None
    if currency != "NGN":
        log.warning("funding_settlement_invalid_currency ref=%s currency=%r",
                    intent.reference, verified_currency)
        _mark_funding_review_locked(
            intent, reason="currency_mismatch", observed_amount=amount,
            observed_currency=currency, evidence=evidence,
        )
        return None
    if amount != intent.amount:
        log.warning("funding_settlement_amount_mismatch ref=%s expected=%s observed=%s",
                    intent.reference, intent.amount, amount)
        _mark_funding_review_locked(
            intent, reason="amount_mismatch", observed_amount=amount,
            observed_currency=currency, evidence=evidence,
        )
        return None

    meta = dict(intent.meta or {})
    clean_evidence = _funding_evidence(evidence)
    settlement = {
        "provider": str(meta.get("provider") or ""),
        "provider_reference": str(
            clean_evidence.get("provider_reference")
            or meta.get("provider_reference") or intent.reference
        )[:160],
        "verified_amount": str(amount),
        "currency": currency,
        "settled_at": timezone.now().isoformat(),
        **clean_evidence,
    }
    txn = credit(
        intent.user, amount, "Wallet top-up",
        meta={"reference": intent.reference, "funding_settlement": settlement},
        reference=intent.reference,
    )

    review = meta.get("funding_review")
    if isinstance(review, dict):
        review = dict(review)
        review["active"] = False
        review["resolved_at"] = timezone.now().isoformat()
        review["resolution"] = "verified_exact_payment"
        meta["funding_review"] = review
    meta["funding_settlement"] = settlement
    intent.meta = meta
    intent.status = FundingIntent.PAID
    intent.credited = True
    # ``amount`` is the customer's immutable requested amount.  Never rewrite
    # it with provider input; exact equality above is the settlement boundary.
    intent.save(update_fields=["status", "credited", "meta", "updated"])
    return txn


@db_transaction.atomic
def settle_funding(reference: str, verified_amount=None, *,
                   verified_currency=None, evidence=None) -> Transaction | None:
    """Credit one intent only for an exact, verified NGN payment, exactly once."""
    intent = (FundingIntent.objects.select_for_update()
              .filter(reference=str(reference or "")).first())
    if intent is None:
        return None
    return _settle_funding_locked(
        intent, verified_amount=verified_amount,
        verified_currency=verified_currency, evidence=evidence,
    )


@db_transaction.atomic
def resolve_funding_review(reference: str, *, disposition: str, reason: str,
                           confirmed_amount=None, evidence_reference: str,
                           evidence_snapshot: dict, actor,
                           approval_id: int) -> dict:
    """Resolve held wallet funding only after maker/checker evidence review."""
    disposition = str(disposition or "").strip()
    reason = str(reason or "").strip()
    evidence_reference = str(evidence_reference or "").strip()[:160]
    if disposition not in FUNDING_REVIEW_DISPOSITIONS:
        raise ValueError("Invalid funding-review disposition")
    if len(reason) < 12:
        raise ValueError("Resolution reason must be at least 12 characters")
    if len(evidence_reference) < 4:
        raise ValueError("A provider evidence reference is required")
    if not isinstance(approval_id, int) or approval_id <= 0:
        raise ValueError("A valid approval id is required")

    intent = (FundingIntent.objects.select_for_update()
              .filter(reference=str(reference or "")).first())
    if intent is None:
        raise ValueError("Funding intent no longer exists")
    if funding_review_evidence(intent) != (evidence_snapshot or {}):
        raise ValueError(
            "Funding evidence changed after submission; create a new approval"
        )
    review = (intent.meta or {}).get("funding_review") or {}
    if not isinstance(review, dict) or review.get("active") is not True:
        raise ValueError("This funding intent is no longer awaiting resolution")

    already_credited = bool(intent.credited or intent.status == FundingIntent.PAID)
    if already_credited and disposition != "confirm_existing_credit":
        raise ValueError(
            "An already-credited funding conflict requires confirm_existing_credit"
        )
    if not already_credited and disposition == "confirm_existing_credit":
        raise ValueError("No existing wallet credit is available to confirm")

    actor_label = (getattr(actor, "email", "") or getattr(actor, "username", "")
                   or str(getattr(actor, "pk", actor)))
    resolution = {
        "disposition": disposition,
        "reason": reason[:300],
        "evidence_reference": evidence_reference,
        "actor": actor_label,
        "approval_id": approval_id,
        "resolved_at": timezone.now().isoformat(),
    }
    txn = None
    if disposition == "confirm_paid":
        amount = _verified_funding_amount(confirmed_amount)
        if amount is None or amount != intent.amount:
            raise ValueError("Confirmed amount must exactly match the funding request")
        txn = _settle_funding_locked(
            intent, verified_amount=amount, verified_currency="NGN",
            evidence={
                "source": "operator_resolution",
                "evidence_reference": evidence_reference,
            },
            allow_active_review=True,
        )
        if txn is None:
            raise ValueError("Funding could not be credited")
        intent.refresh_from_db()
    elif disposition == "mark_failed":
        intent.status = FundingIntent.FAILED
    else:  # confirm_existing_credit
        amount = _verified_funding_amount(confirmed_amount)
        if amount is None or amount != intent.amount:
            raise ValueError("Confirmed amount must exactly match the funding request")
        if not intent.credited or intent.status != FundingIntent.PAID:
            raise ValueError(
                "Funding flags are inconsistent; reconcile them before confirming the credit"
            )
        txn = (Transaction.objects.select_for_update()
               .filter(reference=intent.reference, user_id=intent.user_id,
                       direction=Transaction.IN,
                       transaction_status=Transaction.SUCCESS,
                       amount=intent.amount)
               .first())
        if txn is None:
            raise ValueError(
                "The existing wallet credit ledger row was not found; no-balance confirmation is unsafe"
            )
        resolution["balance_movement"] = "0.00"
        resolution["existing_credit_reference"] = txn.reference

    meta = dict(intent.meta or {})
    marker = dict(meta.get("funding_review") or {})
    marker["active"] = False
    marker["resolution"] = resolution
    meta["funding_review"] = marker
    intent.meta = meta
    update_fields = ["meta", "updated"]
    if disposition == "mark_failed":
        update_fields.append("status")
    intent.save(update_fields=update_fields)
    return {
        "reference": intent.reference,
        "disposition": disposition,
        "credited": bool(intent.credited),
        "transaction_reference": txn.reference if txn is not None else "",
        "balance_movement": "0.00" if disposition == "confirm_existing_credit" else "",
    }


@db_transaction.atomic
def settle_reserved_funding(transaction_reference: str, amount, user) -> Transaction | None:
    """Credit a wallet for an inbound bank transfer to its reserved account, once.

    Keyed on the provider's transaction_reference (unique per payment): the ledger
    row's unique `reference` is the idempotency guard, so a redelivered webhook
    is a no-op rather than a double-credit. Returns the credit row, or None if
    the payment was already applied (or the inputs are incomplete).
    """
    if not transaction_reference or amount is None:
        log.warning("reserved_funding_incomplete txref=%r amount=%r", transaction_reference, amount)
        return None
    existing = (
        Transaction.objects
        .filter(reference=transaction_reference)
        .select_related("user")
        .only("id", "user_id", "amount", "direction", "transaction_status", "service", "meta")
        .first()
    )
    if existing is not None:
        meta = existing.meta or {}
        log.warning(
            "reserved_funding_duplicate txref=%s target_user=%s existing_user=%s "
            "existing_amount=%s incoming_amount=%s existing_direction=%s "
            "existing_status=%s existing_service=%s existing_channel=%s",
            transaction_reference,
            getattr(user, "id", None),
            existing.user_id,
            existing.amount,
            amount,
            existing.direction,
            existing.transaction_status,
            existing.service,
            meta.get("channel") or meta.get("provider") or "",
        )
        return None
    try:
        return credit(
            user, amount, "Wallet funding",
            meta={"reference": transaction_reference, "channel": "reserved_account"},
            reference=transaction_reference,
        )
    except IntegrityError:
        # Raced duplicate webhook slipped past the exists() check — the unique
        # reference rejected it; the balance bump is rolled back to the savepoint.
        return None







# Provider history rows are small JSON objects.  When a row misses the bounded
# recent-reference set above, extract at most this many reference-shaped tokens
# from its VALUES (never its field names) and resolve them through the globally
# indexed Transaction.reference column.  This is bounded by the row, rather than
# by the lifetime number of payouts on the account.
_REFERENCE_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9][A-Za-z0-9_-]{3,63}(?![A-Za-z0-9_-])")
# Production references made by ``make_reference`` are a four-letter prefix plus
# twelve hex characters.  Find that exact fragment even when the provider glues a
# label to it (``REV-ZTCH...`` / ``REF_ZTCH...``), where the generic token above
# quite correctly sees one larger token that would not equal the indexed ledger
# reference.
_GENERATED_REFERENCE_FRAGMENT = re.compile(
    r"(?:ZTCH|ZTRF|ZPAY|ZFND)[A-F0-9]{12}", re.IGNORECASE)
_MAX_REFERENCE_CANDIDATES = 64
_MAX_REFERENCE_VALUE_CHARS = 4096


def _reference_candidates(tx: dict) -> list[str]:
    """Return a bounded set of exact-reference candidates found in row values."""
    candidates: list[str] = []
    seen: set[str] = set()
    stack = [tx]
    containers_seen: set[int] = set()

    while stack and len(candidates) < _MAX_REFERENCE_CANDIDATES:
        value = stack.pop()
        if isinstance(value, dict):
            marker = id(value)
            if marker in containers_seen:
                continue
            containers_seen.add(marker)
            stack.extend(reversed(list(value.values())))
            continue
        if isinstance(value, (list, tuple, set)):
            marker = id(value)
            if marker in containers_seen:
                continue
            containers_seen.add(marker)
            stack.extend(reversed(list(value)))
            continue
        if value is None:
            continue

        text = str(value)[:_MAX_REFERENCE_VALUE_CHARS]
        for match in _GENERATED_REFERENCE_FRAGMENT.finditer(text):
            candidate = match.group(0).upper()
            if candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)
                if len(candidates) >= _MAX_REFERENCE_CANDIDATES:
                    break
        if len(candidates) >= _MAX_REFERENCE_CANDIDATES:
            break
        for match in _REFERENCE_TOKEN.finditer(text):
            raw = match.group(0)
            # Zitch-generated references are uppercase.  Retain the provider's
            # spelling as well so exact, indexed lookup also handles legacy rows.
            for candidate in (raw, raw.upper()):
                if candidate in seen:
                    continue
                seen.add(candidate)
                candidates.append(candidate)
                if len(candidates) >= _MAX_REFERENCE_CANDIDATES:
                    break
    return candidates



_WEMA_REVERSAL_MARKER = re.compile(
    r"\b(?:REVERSAL|REVERSED|BOUNCED)\b|BOUNCE\s+BACK|RETURN\s+OF\s+FUNDS|RETURNED\s+TRANSFER",
    re.IGNORECASE,
)



def _active_reversal_quarantine(txn: Transaction) -> bool:
    marker = (txn.meta or {}).get("wema_reversal_quarantine") or {}
    if isinstance(marker, dict) and marker.get("active") is True:
        return True
    if not getattr(txn, "pk", None):
        return False
    return ReversalEvidence.objects.filter(
        Q(payout_id=txn.pk) | Q(associated_payouts__pk=txn.pk),
        state__in=(ReversalEvidence.ACTIVE, ReversalEvidence.CONFLICT),
    ).distinct().exists()


def _quarantine_evidence(marker: dict) -> list[dict]:
    """Return normalized per-bank-row evidence, including legacy markers."""
    if not isinstance(marker, dict):
        return []
    entries = [dict(item) for item in (marker.get("evidence") or [])
               if isinstance(item, dict)]
    if entries:
        return entries
    # Markers written before evidence became a list carried one row at the top
    # level.  Upgrade it in memory so those live holds remain resolvable.
    if marker.get("ledger_reference") or marker.get("inbound_reference"):
        return [{
            "reason": str(marker.get("reason") or "legacy_quarantine"),
            "inbound_reference": str(marker.get("inbound_reference") or ""),
            "ledger_reference": str(marker.get("ledger_reference") or ""),
            "received_amount": str(marker.get("received_amount") or ""),
            "detected_at": marker.get("detected_at"),
            "last_seen_at": marker.get("last_seen_at"),
            "resolved": marker.get("active") is False and bool(marker.get("resolution")),
            "resolution": marker.get("resolution") or {},
        }]
    return []


def _evidence_snapshot(entry: dict) -> dict:
    """Immutable fields bound into a maker/checker request."""
    snapshot = {
        "reason": str(entry.get("reason") or ""),
        "inbound_reference": str(entry.get("inbound_reference") or ""),
        "ledger_reference": str(entry.get("ledger_reference") or ""),
        "received_amount": str(entry.get("received_amount") or ""),
        "detected_at": str(entry.get("detected_at") or ""),
    }
    # New approvals bind to the indexed evidence row and its material version.
    # Keep the legacy five-field shape above so an active pre-deploy JSON marker
    # can be adopted without making the operator recreate it by hand.
    if entry.get("evidence_id") is not None:
        snapshot.update({
            "evidence_id": int(entry["evidence_id"]),
            "version": int(entry.get("version") or 0),
            "state": str(entry.get("state") or ""),
            "initial_reason": str(entry.get("initial_reason") or
                                  entry.get("reason") or ""),
            "payout_reference": str(entry.get("payout_reference") or ""),
            "associated_payout_references": sorted(
                str(value) for value in
                (entry.get("associated_payout_references") or [])
            ),
            "observed_amounts": [str(value) for value in
                                 (entry.get("observed_amounts") or [])],
        })
    return snapshot


def _same_money(left, right) -> bool:
    """Compare provider/JSON money strings without formatting sensitivity."""
    try:
        left_value = Decimal(str(left))
        right_value = Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return (left_value.is_finite() and right_value.is_finite()
            and left_value == right_value)


_ACTIVE_REVERSAL_STATES = (ReversalEvidence.ACTIVE, ReversalEvidence.CONFLICT)
_MAX_REVERSAL_AMOUNT = Decimal("999999999999.99")
_MAX_BIGINT = (2 ** 63) - 1
_CENT = Decimal("0.01")


def _reversal_amount(value) -> Decimal | None:
    """Return only values representable by the reversal/ledger money columns."""
    try:
        amount = Decimal(str(value))
        rounded = amount.quantize(_CENT)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if (not amount.is_finite() or amount <= 0 or amount > _MAX_REVERSAL_AMOUNT
            or rounded != amount):
        return None
    return rounded


def _approval_id(value) -> int | None:
    # Approval ids are serialized through JSON and must stay exact.  In
    # particular, bool is a subclass of int and int(1.2) silently truncates;
    # accepting either could bind a resolution to the wrong approval row.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        parsed = int(value.strip())
    else:
        return None
    return parsed if 0 < parsed <= _MAX_BIGINT else None


def _reversal_provider_hash(inbound_reference: str) -> str:
    normalized = str(inbound_reference or "").strip().upper()
    return hashlib.sha256(f"wema\0{normalized}".encode()).hexdigest()


def _reversal_ledger_reference(inbound_reference: str) -> str:
    """A globally unique ledger key that always fits Transaction.reference."""
    readable = f"WEMA-CR-{str(inbound_reference or '').strip()}"
    if len(readable) <= 64:
        return readable
    digest = hashlib.sha256(str(inbound_reference or "").strip().encode()).hexdigest().upper()
    return f"WEMA-CR-H{digest[:47]}"


def _model_evidence_snapshot(evidence: ReversalEvidence) -> dict:
    observations = list(
        evidence.observations.order_by("amount").values_list("amount", flat=True)
    )
    payout_references = list(
        evidence.associated_payouts.order_by("reference").values_list(
            "reference", flat=True)
    )
    return _evidence_snapshot({
        "evidence_id": evidence.pk,
        "version": evidence.version,
        "state": evidence.state,
        "initial_reason": evidence.initial_reason,
        "reason": evidence.reason,
        "inbound_reference": evidence.provider_reference,
        "ledger_reference": evidence.ledger_reference,
        "received_amount": str(evidence.amount),
        "detected_at": evidence.first_seen.isoformat() if evidence.first_seen else "",
        "payout_reference": evidence.payout.reference if evidence.payout_id else "",
        "associated_payout_references": payout_references,
        "observed_amounts": [str(amount) for amount in observations],
    })


def _observe_reversal_amount(evidence: ReversalEvidence, amount: Decimal) -> bool:
    """Record a distinct provider amount; return True only for a new value."""
    observation, created = ReversalEvidenceObservation.objects.get_or_create(
        evidence=evidence,
        amount=amount,
    )
    if not created:
        ReversalEvidenceObservation.objects.filter(pk=observation.pk).update(
            sightings=F("sightings") + 1,
            last_seen=timezone.now(),
        )
    return created


def _mark_reversal_conflict(evidence: ReversalEvidence, reason: str) -> bool:
    """Reopen a case without erasing its immutable origin or resolution history."""
    if evidence.state == ReversalEvidence.CONFLICT and evidence.reason == reason:
        return False
    evidence.state = ReversalEvidence.CONFLICT
    evidence.reason = str(reason or "reversal_conflict")[:64]
    evidence.version += 1
    evidence.resolved_amount = None
    evidence.resolution_disposition = ""
    evidence.resolution_reason = ""
    evidence.resolution_approval_id = None
    evidence.resolved_by = None
    evidence.resolved_at = None
    evidence.save(update_fields=[
        "state", "reason", "version", "resolved_amount",
        "resolution_disposition", "resolution_reason",
        "resolution_approval_id", "resolved_by", "resolved_at", "last_seen",
    ])
    return True


@db_transaction.atomic
def _adopt_legacy_reversal_evidence(payout: Transaction) -> None:
    """Move a live pre-migration JSON hold into the indexed case ledger lazily."""
    payout = Transaction.objects.select_for_update().get(pk=payout.pk)
    if ReversalEvidence.objects.filter(
            Q(payout=payout) | Q(associated_payouts=payout)).exists():
        return
    marker = (payout.meta or {}).get("wema_reversal_quarantine") or {}
    for index, item in enumerate(_quarantine_evidence(marker)):
        amount = _reversal_amount(item.get("received_amount"))
        if amount is None:
            continue
        ledger_reference = str(item.get("ledger_reference") or "").strip()
        inbound_reference = str(item.get("inbound_reference") or "").strip()
        if not inbound_reference and ledger_reference.startswith("WEMA-CR-"):
            inbound_reference = ledger_reference[len("WEMA-CR-"):]
        if not inbound_reference:
            inbound_reference = f"legacy:{payout.reference}:{index}"
        provider_hash = _reversal_provider_hash(inbound_reference)
        ledger_row = (Transaction.objects.select_for_update().filter(
            reference=ledger_reference, user=payout.user,
        ).first() if ledger_reference else None)
        resolved = item.get("resolved") is True
        resolution = item.get("resolution") if isinstance(item.get("resolution"), dict) else {}
        approval_id = _approval_id(resolution.get("approval_id"))
        initial_reason = str(item.get("initial_reason") or item.get("reason") or
                             marker.get("reason") or "legacy_quarantine")[:64]
        ledger_owner = (ReversalEvidence.objects.select_for_update().filter(
            ledger_transaction=ledger_row,
        ).first() if ledger_row is not None else None)
        ledger_conflict = bool(
            ledger_owner is not None
            and ledger_owner.provider_reference_hash != provider_hash
        )
        created_state = (ReversalEvidence.CONFLICT if ledger_conflict else
                         ReversalEvidence.RESOLVED if resolved else
                         ReversalEvidence.ACTIVE)
        created_reason = ("ledger_reference_reused" if ledger_conflict
                          else initial_reason)
        evidence, created = ReversalEvidence.objects.get_or_create(
            provider=ReversalEvidence.WEMA,
            provider_reference_hash=provider_hash,
            defaults={
                "provider_reference": inbound_reference[:255],
                "ledger_reference": ledger_reference[:64],
                "user": payout.user,
                "payout": payout,
                "ledger_transaction": None if ledger_conflict else ledger_row,
                "amount": amount,
                "initial_reason": initial_reason,
                "reason": created_reason,
                "state": created_state,
                "resolved_amount": amount if created_state == ReversalEvidence.RESOLVED else None,
                "resolution_disposition": str(
                    resolution.get("disposition") or "legacy_resolved")[:48]
                    if created_state == ReversalEvidence.RESOLVED else "",
                "resolution_reason": (str(resolution.get("reason") or "")[:300]
                                      if created_state == ReversalEvidence.RESOLVED else ""),
                "resolution_approval_id": (approval_id
                                           if created_state == ReversalEvidence.RESOLVED
                                           else None),
                "resolved_at": (timezone.now()
                                if created_state == ReversalEvidence.RESOLVED else None),
            },
        )
        conflict_reason = ""
        if (evidence.user_id != payout.user_id
                or evidence.payout_id not in (None, payout.pk)):
            conflict_reason = "provider_reference_reused"
        elif not _same_money(evidence.amount, amount):
            conflict_reason = "evidence_amount_changed"
        elif ledger_conflict:
            conflict_reason = "ledger_reference_reused"
        if conflict_reason:
            evidence.state = ReversalEvidence.CONFLICT
            evidence.reason = conflict_reason
            evidence.version += 1
            evidence.resolved_amount = None
            evidence.resolution_disposition = ""
            evidence.resolution_reason = ""
            evidence.resolution_approval_id = None
            evidence.resolved_by = None
            evidence.resolved_at = None
            evidence.save(update_fields=[
                "state", "reason", "version", "resolved_amount",
                "resolution_disposition", "resolution_reason",
                "resolution_approval_id", "resolved_by", "resolved_at", "last_seen",
            ])
        elif not created:
            update_fields = []
            if evidence.payout_id is None:
                evidence.payout = payout
                update_fields.append("payout")
            if (evidence.ledger_transaction_id is None and ledger_row is not None
                    and not ReversalEvidence.objects.exclude(pk=evidence.pk).filter(
                        ledger_transaction=ledger_row).exists()):
                evidence.ledger_transaction = ledger_row
                evidence.ledger_reference = ledger_reference[:64]
                update_fields.extend(["ledger_transaction", "ledger_reference"])
            if update_fields:
                evidence.save(update_fields=[*update_fields, "last_seen"])
        if ledger_conflict and ledger_owner is not None:
            ledger_owner.state = ReversalEvidence.CONFLICT
            ledger_owner.reason = "ledger_reference_reused"
            ledger_owner.version += 1
            ledger_owner.resolved_amount = None
            ledger_owner.resolution_disposition = ""
            ledger_owner.resolution_reason = ""
            ledger_owner.resolution_approval_id = None
            ledger_owner.resolved_by = None
            ledger_owner.resolved_at = None
            ledger_owner.save(update_fields=[
                "state", "reason", "version", "resolved_amount",
                "resolution_disposition", "resolution_reason",
                "resolution_approval_id", "resolved_by", "resolved_at", "last_seen",
            ])
        evidence.associated_payouts.add(payout)
        _observe_reversal_amount(evidence, amount)
        if evidence.state == ReversalEvidence.RESOLVED and not ReversalEvidenceResolution.objects.filter(
                evidence=evidence).exists():
            ReversalEvidenceResolution.objects.create(
                evidence=evidence,
                payout=payout,
                disposition=evidence.resolution_disposition or "legacy_resolved",
                reason=evidence.resolution_reason,
                confirmed_amount=amount,
                approval_id=approval_id,
                payout_status_before=payout.transaction_status,
                payout_status_after=payout.transaction_status,
            )


def reversal_quarantine_evidence(payout: Transaction | None,
                                 evidence_reference: str = "") -> dict:
    """Select one unresolved evidence item for an operator resolution request.

    When multiple bank rows are held, callers must name one by its inbound or
    ledger reference; silently selecting the newest would make the approval UI
    apply a different amount than the operator intended.
    """
    wanted = str(evidence_reference or "").strip()
    if payout is not None:
        _adopt_legacy_reversal_evidence(payout)
        scope = Q(payout=payout) | Q(associated_payouts=payout)
        # An unmatched case can only be attached deliberately by naming it. Do
        # not silently offer the customer's sole unrelated case when an operator
        # opens a payout that has no evidence of its own.
        if wanted:
            scope |= Q(payout__isnull=True, user=payout.user)
        unresolved = list(
            ReversalEvidence.objects.select_related("payout")
            .filter(scope, state__in=_ACTIVE_REVERSAL_STATES)
            .distinct()
            .order_by("first_seen", "pk")
        )
    else:
        unresolved = list(
            ReversalEvidence.objects.select_related("payout")
            .filter(payout__isnull=True, state__in=_ACTIVE_REVERSAL_STATES)
            .order_by("first_seen", "pk")
        )
    if wanted:
        unresolved = [item for item in unresolved if wanted in {
            str(item.pk), item.ledger_reference, item.provider_reference,
        }]
    elif len(unresolved) > 1:
        raise ValueError("Multiple returned credits are awaiting review; choose an evidence reference")
    if len(unresolved) != 1:
        raise ValueError("Unresolved reversal evidence not found")
    return _model_evidence_snapshot(unresolved[0])


def _evidence_was_resolved(payout: Transaction, *, inbound_reference: str,
                           ledger_reference: str, received_amount: Decimal) -> bool:
    evidence = ReversalEvidence.objects.filter(
        provider=ReversalEvidence.WEMA,
        provider_reference_hash=_reversal_provider_hash(inbound_reference),
    ).filter(
        Q(payout=payout) | Q(associated_payouts=payout),
        state=ReversalEvidence.RESOLVED,
    ).distinct().first()
    if evidence is not None:
        return _same_money(evidence.resolved_amount or evidence.amount, received_amount)
    # A resolved marker created before this table existed is adopted on demand.
    _adopt_legacy_reversal_evidence(payout)
    return ReversalEvidence.objects.filter(
        provider=ReversalEvidence.WEMA,
        provider_reference_hash=_reversal_provider_hash(inbound_reference),
    ).filter(
        Q(payout=payout) | Q(associated_payouts=payout),
        state=ReversalEvidence.RESOLVED,
        resolved_amount=received_amount,
    ).distinct().exists()


def _upsert_reversal_evidence(*, user, payout: Transaction | None, reason: str,
                              inbound_reference: str, ledger_reference: str,
                              received_amount: Decimal,
                              ledger_transaction: Transaction | None = None,
                              force_conflict: bool = False) -> tuple[ReversalEvidence, bool]:
    """Claim one provider row and retain every conflicting amount immutably.

    The surrounding caller is atomic and holds the payout/wallet locks.  The
    provider-reference unique constraint is the final race backstop.
    """
    received_amount = _reversal_amount(received_amount)
    if received_amount is None:
        raise ValueError("Reversal evidence amount cannot be represented safely")
    provider_hash = _reversal_provider_hash(inbound_reference)
    evidence = (ReversalEvidence.objects.select_for_update()
                .filter(provider=ReversalEvidence.WEMA,
                        provider_reference_hash=provider_hash)
                .first())
    ledger_owner = (ReversalEvidence.objects.select_for_update().filter(
        ledger_transaction=ledger_transaction,
    ).first() if ledger_transaction is not None else None)
    ledger_conflict = bool(
        ledger_owner is not None
        and (evidence is None or ledger_owner.pk != evidence.pk)
    )
    if ledger_conflict:
        # One immutable ledger row cannot substantiate two different provider
        # events. Keep the readable ledger reference on both cases, but leave the
        # second OneToOne unset and hold both for provenance review.
        ledger_transaction = None
    changed = False
    if evidence is None:
        initial_reason = str(reason or "reversal_review")[:64]
        conflict_reason = (
            "ledger_reference_reused" if ledger_conflict else
            "provider_success_after_refund"
            if force_conflict and initial_reason == "provider_success_after_refund" else
            "provider_reference_reused" if force_conflict else
            initial_reason
        )
        try:
            # Savepoint is required: catching a uniqueness error directly in the
            # outer money transaction would leave that transaction unusable.
            with db_transaction.atomic():
                evidence = ReversalEvidence.objects.create(
                    provider=ReversalEvidence.WEMA,
                    provider_reference=str(inbound_reference or "")[:255],
                    provider_reference_hash=provider_hash,
                    ledger_reference=str(ledger_reference or "")[:64],
                    user=user,
                    payout=payout,
                    ledger_transaction=ledger_transaction,
                    amount=received_amount,
                    initial_reason=initial_reason,
                    reason=conflict_reason,
                    state=(ReversalEvidence.CONFLICT if force_conflict or ledger_conflict
                           else ReversalEvidence.ACTIVE),
                )
        except IntegrityError:
            evidence = (ReversalEvidence.objects.select_for_update()
                        .get(provider=ReversalEvidence.WEMA,
                             provider_reference_hash=provider_hash))
        else:
            if payout is not None:
                evidence.associated_payouts.add(payout)
            _observe_reversal_amount(evidence, received_amount)
            if ledger_conflict and ledger_owner is not None:
                _mark_reversal_conflict(ledger_owner, "ledger_reference_reused")
            return evidence, True

    if payout is not None:
        evidence.associated_payouts.add(payout)
    update_fields: set[str] = {"last_seen"}
    identity_conflict = evidence.user_id != user.pk
    if payout is not None:
        if evidence.payout_id is None and not identity_conflict:
            evidence.payout = payout
            update_fields.add("payout")
            changed = True
        elif evidence.payout_id not in (None, payout.pk):
            identity_conflict = True
    if ledger_transaction is not None:
        if evidence.ledger_transaction_id is None and not identity_conflict:
            evidence.ledger_transaction = ledger_transaction
            evidence.ledger_reference = ledger_transaction.reference
            update_fields.update({"ledger_transaction", "ledger_reference"})
            changed = True
        elif evidence.ledger_transaction_id not in (None, ledger_transaction.pk):
            identity_conflict = True

    new_amount = _observe_reversal_amount(evidence, received_amount)
    amount_conflict = not _same_money(evidence.amount, received_amount)
    if identity_conflict or amount_conflict or force_conflict or ledger_conflict:
        conflict_reason = (
            "provider_reference_reused" if identity_conflict else
            "ledger_reference_reused" if ledger_conflict else
            "provider_success_after_refund" if force_conflict else
            "resolved_evidence_changed" if evidence.state == ReversalEvidence.RESOLVED else
            "evidence_amount_changed"
        )
        if evidence.state != ReversalEvidence.CONFLICT or evidence.reason != conflict_reason:
            evidence.state = ReversalEvidence.CONFLICT
            evidence.reason = conflict_reason
            evidence.version += 1
            evidence.resolved_amount = None
            evidence.resolution_disposition = ""
            evidence.resolution_reason = ""
            evidence.resolution_approval_id = None
            evidence.resolved_by = None
            evidence.resolved_at = None
            update_fields.update({
                "state", "reason", "version", "resolved_amount",
                "resolution_disposition", "resolution_reason",
                "resolution_approval_id", "resolved_by", "resolved_at",
            })
            changed = True
        elif new_amount:
            # A distinct observation is material even when the case was already
            # conflicted; pending approvals must bind to the new evidence set.
            evidence.version += 1
            update_fields.add("version")
            changed = True
    elif evidence.state == ReversalEvidence.ACTIVE:
        # Preserve the first review reason on harmless repeat polling.  Relabeling
        # partial evidence as "existing quarantine" would invalidate approvals and
        # page on every sweep without adding information.
        pass
    evidence.save(update_fields=sorted(update_fields))
    if ledger_conflict and ledger_owner is not None:
        _mark_reversal_conflict(ledger_owner, "ledger_reference_reused")
    return evidence, changed


def _sync_reversal_quarantine_summary(payout: Transaction,
                                      latest: ReversalEvidence | None = None,
                                      *, resolution: dict | None = None) -> dict:
    """Keep only a bounded compatibility/status summary on the payout row."""
    active_qs = ReversalEvidence.objects.filter(
        Q(payout=payout) | Q(associated_payouts=payout),
        state__in=_ACTIVE_REVERSAL_STATES,
    ).distinct()
    active_count = active_qs.count()
    latest = latest or active_qs.order_by("-last_seen", "-pk").first()
    # A globally reused provider reference may be canonically attached to the
    # first payout while this payout is a second claimant. The explicit case still
    # makes this payout unsafe to settle; do not write an inactive summary merely
    # because the FK can point at only one payout.
    if latest is not None and latest.state in _ACTIVE_REVERSAL_STATES and not active_count:
        active_count = 1
    if latest is None:
        latest = ReversalEvidence.objects.filter(
            Q(payout=payout) | Q(associated_payouts=payout)
        ).distinct().order_by("-last_seen", "-pk").first()
    prior = (payout.meta or {}).get("wema_reversal_quarantine") or {}
    marker = {
        "active": bool(active_count),
        "active_count": active_count,
        "payout_amount": str(payout.amount),
    }
    if latest is not None:
        marker.update({
            "evidence_id": latest.pk,
            "evidence_version": latest.version,
            "state": latest.state,
            "initial_reason": latest.initial_reason,
            "reason": latest.reason,
            "inbound_reference": latest.provider_reference,
            "ledger_reference": latest.ledger_reference,
            "received_amount": str(latest.amount),
            "detected_at": latest.first_seen.isoformat() if latest.first_seen else "",
            "last_seen_at": latest.last_seen.isoformat() if latest.last_seen else "",
        })
    if isinstance(prior, dict) and prior.get("failed_refund_correction_applied"):
        marker["failed_refund_correction_applied"] = True
    if resolution:
        marker["resolved_at"] = resolution.get("resolved_at", "")
        marker["resolution"] = resolution
    elif isinstance(prior, dict) and isinstance(prior.get("resolution"), dict):
        marker["resolution"] = prior["resolution"]
        marker["resolved_at"] = prior.get("resolved_at", "")
    meta = dict(payout.meta or {})
    meta["wema_reversal_quarantine"] = marker
    payout.meta = meta
    payout.save(update_fields=["meta"])
    return marker


def _resolve_reversal_evidence_automatically(evidence: ReversalEvidence, *,
                                             payout: Transaction,
                                             amount: Decimal,
                                             status_before: str = "") -> None:
    """Close an exact bank return while retaining an append-only audit result."""
    before = status_before or payout.transaction_status
    now = timezone.now()
    evidence.state = ReversalEvidence.RESOLVED
    evidence.version += 1
    evidence.resolved_amount = amount
    evidence.resolution_disposition = "automatic_full_reversal"
    evidence.resolution_reason = "Exact reference-bound bank return"
    evidence.resolution_approval_id = None
    evidence.resolved_by = None
    evidence.resolved_at = now
    evidence.save(update_fields=[
        "state", "version", "resolved_amount", "resolution_disposition",
        "resolution_reason", "resolution_approval_id", "resolved_by",
        "resolved_at", "last_seen",
    ])
    if not ReversalEvidenceResolution.objects.filter(
            evidence=evidence,
            disposition="automatic_full_reversal",
            confirmed_amount=amount).exists():
        ReversalEvidenceResolution.objects.create(
            evidence=evidence,
            payout=payout,
            disposition="automatic_full_reversal",
            reason="Exact reference-bound bank return",
            confirmed_amount=amount,
            movement_amount=amount,
            movement_direction=Transaction.IN,
            payout_status_before=before,
            payout_status_after=Transaction.FAILED,
        )


def _hold_provider_success_after_refund_locked(payout: Transaction) -> ReversalEvidence:
    """Persist a provider-success/refund race without guessing which side is true.

    ``settle_or_refund`` already owns the payout row lock. The wallet remains in
    its refunded state and the payout remains FAILED until maker/checker review.
    """
    evidence = (ReversalEvidence.objects.select_for_update()
                .filter(payout=payout)
                .order_by("-last_seen", "-pk").first())
    if evidence is not None:
        inbound_reference = evidence.provider_reference
        ledger_reference = evidence.ledger_reference
        amount = evidence.resolved_amount or evidence.amount
        ledger_row = evidence.ledger_transaction
    else:
        inbound_reference = f"provider-success:{payout.reference}"
        ledger_reference = ""
        amount = payout.amount
        ledger_row = None
    evidence, _ = _upsert_reversal_evidence(
        user=payout.user,
        payout=payout,
        reason="provider_success_after_refund",
        inbound_reference=inbound_reference,
        ledger_reference=ledger_reference,
        received_amount=amount,
        ledger_transaction=ledger_row,
        force_conflict=True,
    )
    _sync_reversal_quarantine_summary(payout, evidence)
    return evidence


@db_transaction.atomic
def _apply_matched_reversal(wallet: Wallet, *, inbound_reference: str,
                            received_amount: Decimal,
                            payout_reference: str) -> dict:
    """Apply one reference-bound bank-history reversal under durable locks.

    A FAILED inbound evidence row claims ``WEMA-CR-<provider ref>`` without
    crediting the wallet.  That gives the reversal path the same durable
    idempotency key as ordinary funding and, crucially, coordinates a rolling
    deploy: an older process can neither credit the row after this refund nor
    race a refund after it already credited the row.
    """
    payout = (Transaction.objects.select_for_update()
              .filter(user=wallet.user, reference=payout_reference,
                      direction=Transaction.OUT)
              .first())
    if payout is None or not is_bank_payout(payout):
        return {"outcome": "missing", "ledger_reference": ""}

    # Funding credits lock the wallet before writing their ledger row.  Use the
    # same lock here, after the payout-row lock used by every payout transition,
    # so the existence check and evidence claim are atomic against old/new code.
    locked_wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
    _adopt_legacy_reversal_evidence(payout)
    ledger_ref = _reversal_ledger_reference(inbound_reference)
    existing = Transaction.objects.select_for_update().filter(reference=ledger_ref).first()
    if existing is not None:
        if _evidence_was_resolved(
                payout, inbound_reference=inbound_reference,
                ledger_reference=ledger_ref, received_amount=received_amount):
            # Bank history is polled repeatedly.  A checked evidence row is a
            # permanent idempotency tombstone, not an orphan to quarantine anew.
            return {"outcome": "resolved_duplicate", "ledger_reference": ledger_ref,
                    "payout": payout}
        evidence = (existing.meta or {}).get("wema_reversal_evidence") or {}
        if (existing.transaction_status == Transaction.FAILED
                and isinstance(evidence, dict)
                and evidence.get("payout_reference") == payout.reference
                and existing.user_id == payout.user_id):
            active_case = ReversalEvidence.objects.filter(
                provider=ReversalEvidence.WEMA,
                provider_reference_hash=_reversal_provider_hash(inbound_reference),
                state__in=_ACTIVE_REVERSAL_STATES,
            ).first()
            already_reversed = bool((payout.meta or {}).get("wema_reversal"))
            reason = (
                active_case.reason if active_case is not None else
                "payout_already_reversed" if already_reversed else
                "orphaned_evidence"
            )
            case, changed = _upsert_reversal_evidence(
                user=payout.user,
                payout=payout,
                reason=reason,
                inbound_reference=inbound_reference,
                ledger_reference=ledger_ref,
                received_amount=received_amount,
                ledger_transaction=existing,
            )
            # Adopt an exact automatic reversal written by the immediately prior
            # release: payout metadata proves the balance/status transition and
            # the FAILED evidence row proves this provider event was claimed.
            reversal = (payout.meta or {}).get("wema_reversal") or {}
            if (already_reversed
                    and _same_money(received_amount, payout.amount)
                    and str(reversal.get("inbound_reference") or "")
                        == str(inbound_reference)):
                _resolve_reversal_evidence_automatically(
                    case, payout=payout, amount=received_amount)
                _sync_reversal_quarantine_summary(payout, case)
                return {"outcome": "resolved_duplicate", "ledger_reference": ledger_ref,
                        "payout": payout}
            marker = _sync_reversal_quarantine_summary(payout, case)
            return {"outcome": "quarantined", "reason": case.reason,
                    "ledger_reference": ledger_ref, "payout": payout,
                    "quarantine": marker, "quarantine_changed": changed}
        case, changed = _upsert_reversal_evidence(
            user=payout.user,
            payout=payout,
            reason="already_credited",
            inbound_reference=inbound_reference,
            ledger_reference=ledger_ref,
            received_amount=received_amount,
            ledger_transaction=existing if existing.user_id == payout.user_id else None,
            force_conflict=existing.user_id != payout.user_id,
        )
        marker = _sync_reversal_quarantine_summary(payout, case)
        return {"outcome": "quarantined", "reason": case.reason,
                "ledger_reference": ledger_ref, "payout": payout,
                "quarantine": marker, "quarantine_changed": changed}

    mismatch = received_amount != payout.amount
    active_hold = _active_reversal_quarantine(payout)
    evidence_meta = {
        "channel": "reserved_account",
        INTERNAL_EVIDENCE_META_KEY: True,
        "suppress_transaction_alert": True,
        "wema_reversal_evidence": {
            "payout_reference": payout.reference,
            "inbound_reference": str(inbound_reference)[:255],
            "inbound_reference_hash": _reversal_provider_hash(inbound_reference),
            "received_amount": str(received_amount),
            "payout_amount": str(payout.amount),
            "matched": True,
            "created_at": timezone.now().isoformat(),
        },
    }
    # This FAILED row records/claims the provider event but is deliberately not a
    # wallet credit.  It prevents a mixed-version funding sweep from later
    # interpreting the same bank-history row as fresh money.
    evidence_row = Transaction.objects.create(
        user=payout.user,
        service="Payout reversal evidence",
        amount=received_amount,
        direction=Transaction.IN,
        transaction_status=Transaction.FAILED,
        reference=ledger_ref,
        meta=evidence_meta,
    )

    already_fully_reversed = bool((payout.meta or {}).get("wema_reversal"))
    reason = ("payout_already_reversed" if already_fully_reversed else
              "partial_amount" if mismatch else
              "existing_quarantine" if active_hold else
              "payout_already_reversed" if payout.transaction_status == Transaction.FAILED else
              "exact_return")
    case, changed = _upsert_reversal_evidence(
        user=payout.user,
        payout=payout,
        reason=reason,
        inbound_reference=inbound_reference,
        ledger_reference=ledger_ref,
        received_amount=received_amount,
        ledger_transaction=evidence_row,
    )

    if mismatch or active_hold or payout.transaction_status == Transaction.FAILED:
        marker = _sync_reversal_quarantine_summary(payout, case)
        return {"outcome": "quarantined", "reason": case.reason,
                "ledger_reference": ledger_ref, "payout": payout,
                "quarantine": marker, "quarantine_changed": changed}

    original_status = payout.transaction_status
    from banking.accounts import retain_historical_refund
    retain_historical_refund(payout)
    locked_wallet.balance += payout.amount
    locked_wallet.save(update_fields=["balance", "updated"])
    meta = dict(payout.meta or {})
    meta.pop("reconcile", None)
    meta["wema_reversal"] = {
        "inbound_reference": inbound_reference,
        "ledger_reference": ledger_ref,
        "amount": str(received_amount),
        "applied_at": timezone.now().isoformat(),
    }
    payout.meta = meta
    payout.transaction_status = Transaction.FAILED
    payout.save(update_fields=["transaction_status", "meta"])
    _resolve_reversal_evidence_automatically(
        case, payout=payout, amount=received_amount,
        status_before=original_status)
    _sync_reversal_quarantine_summary(payout, case)
    return {"outcome": "reversed", "ledger_reference": ledger_ref,
            "payout": payout}


@db_transaction.atomic
def _record_unmatched_reversal(wallet: Wallet, *, inbound_reference: str,
                               received_amount: Decimal) -> tuple[ReversalEvidence, bool]:
    """Claim an unmatched payout-shaped credit without moving customer money."""
    locked_wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
    ledger_ref = _reversal_ledger_reference(inbound_reference)
    ledger_row = (Transaction.objects.select_for_update()
                  .filter(reference=ledger_ref).first())
    if ledger_row is None:
        try:
            with db_transaction.atomic():
                ledger_row = Transaction.objects.create(
                    user=locked_wallet.user,
                    service="Payout reversal evidence",
                    amount=received_amount,
                    direction=Transaction.IN,
                    transaction_status=Transaction.FAILED,
                    reference=ledger_ref,
                    meta={
                        "channel": "reserved_account",
                        INTERNAL_EVIDENCE_META_KEY: True,
                        "suppress_transaction_alert": True,
                        "wema_reversal_evidence": {
                            "payout_reference": "",
                            "inbound_reference": str(inbound_reference)[:255],
                            "inbound_reference_hash": _reversal_provider_hash(inbound_reference),
                            "received_amount": str(received_amount),
                            "matched": False,
                            "created_at": timezone.now().isoformat(),
                        },
                    },
                )
        except IntegrityError:
            # Another account/sweep claimed the globally unique provider row.
            # Re-read it and let the evidence case become an ownership conflict.
            ledger_row = (Transaction.objects.select_for_update()
                          .get(reference=ledger_ref))
    same_owner = ledger_row.user_id == locked_wallet.user_id
    reason = ("already_credited_unmatched"
              if same_owner and ledger_row.transaction_status == Transaction.SUCCESS
              else "unmatched_reversal")
    return _upsert_reversal_evidence(
        user=locked_wallet.user,
        payout=None,
        reason=reason,
        inbound_reference=inbound_reference,
        ledger_reference=ledger_ref,
        received_amount=received_amount,
        ledger_transaction=ledger_row if same_owner else None,
        force_conflict=not same_owner,
    )



def pending_bank_payouts(cutoff):
    """PENDING outbound bank-transfer payouts due for settlement reconciliation.

    Wema exposes NO payout webhook, so a Wema transfer returned PENDING/PROCESSING
    would otherwise sit debited forever. Include all durable bank-payout metadata
    shapes, not only the newest ``meta.bank`` field, so old WhatsApp/app rows do
    not get stuck showing Processing.
    """
    return Transaction.objects.filter(
        BANK_PAYOUT_META_FILTER,
        transaction_status=Transaction.PENDING,
        direction=Transaction.OUT,
        created__lte=cutoff,
    )


def quarantined_bank_payouts():
    """Outbound payouts on a durable manual-review hold, in every status.

    A partial return can quarantine a PENDING payout, while an inbound row that
    older code already credited can quarantine one that is already SUCCESS or
    FAILED.  Restricting the operator reminder to ``pending_bank_payouts`` would
    make those terminal-status holds disappear from the review signal.
    """
    return Transaction.objects.filter(
        direction=Transaction.OUT,
    ).filter(
        Q(meta__wema_reversal_quarantine__active=True)
        | Q(reversal_evidence__state__in=_ACTIVE_REVERSAL_STATES)
        | Q(reversal_evidence_associations__state__in=_ACTIVE_REVERSAL_STATES)
    ).distinct().order_by("created")


def unmatched_reversal_evidence():
    """Durable unmatched returned-credit cases still awaiting operations."""
    return ReversalEvidence.objects.filter(
        payout__isnull=True,
        state__in=_ACTIVE_REVERSAL_STATES,
    ).select_related("user", "ledger_transaction").order_by("first_seen", "pk")


REVERSAL_RESOLUTION_DISPOSITIONS = {
    "credit_as_deposit",
    "retain_existing_as_deposit",
    "correct_duplicate_credit",
    "dismiss_duplicate_evidence",
    "confirm_provider_success",
    "confirm_partial_return",
    "confirm_existing_credit",
    "confirm_full_reversal",
}


@db_transaction.atomic
def resolve_reversal_quarantine(reference: str, *, disposition: str, reason: str,
                                evidence_snapshot: dict, actor, approval_id: int,
                                confirmed_amount=None) -> dict:
    """Resolve one indexed reversal case under immutable maker/checker evidence."""
    disposition = str(disposition or "").strip()
    reason = str(reason or "").strip()
    if disposition not in REVERSAL_RESOLUTION_DISPOSITIONS:
        raise ValueError("Invalid reversal-resolution disposition")
    if len(reason) < 12:
        raise ValueError("Resolution reason must be at least 12 characters")
    parsed_approval_id = _approval_id(approval_id)
    if parsed_approval_id is None:
        raise ValueError("A valid approval id is required")
    approval_id = parsed_approval_id
    if not isinstance(evidence_snapshot, dict):
        raise ValueError("The approved evidence snapshot is missing")
    evidence_id = _approval_id(evidence_snapshot.get("evidence_id"))
    if evidence_id is None:
        raise ValueError("The approved evidence snapshot is missing its case id")

    reference = str(reference or "").strip()
    hint = (ReversalEvidence.objects.filter(pk=evidence_id)
            .values("user_id", "payout_id").first())
    if hint is None:
        raise ValueError("This reversal evidence is no longer awaiting review")

    attach_dispositions = {
        "confirm_provider_success", "confirm_partial_return",
        "confirm_existing_credit", "confirm_full_reversal",
    }
    associated_ids = set(Transaction.objects.filter(
        reversal_evidence_associations__pk=evidence_id,
        user_id=hint["user_id"], direction=Transaction.OUT,
    ).values_list("pk", flat=True))
    if hint["payout_id"]:
        associated_ids.add(hint["payout_id"])

    selected_id = None
    if reference:
        selected_id = (Transaction.objects.filter(
            reference=reference, user_id=hint["user_id"], direction=Transaction.OUT,
        ).values_list("pk", flat=True).first())
        if selected_id is None:
            raise ValueError("Quarantined bank payout not found")
        if selected_id not in associated_ids:
            if associated_ids or disposition not in attach_dispositions:
                raise ValueError("The approved payout no longer matches this evidence")
            associated_ids.add(selected_id)
    elif hint["payout_id"]:
        selected_id = hint["payout_id"]
    elif len(associated_ids) == 1:
        selected_id = next(iter(associated_ids))

    # Lock every implicated payout in a stable order before the wallet/evidence.
    # This keeps two reviewers selecting different associations from deadlocking
    # or each applying the same provider event to a different payout.
    locked_payouts = list(
        Transaction.objects.select_for_update()
        .filter(pk__in=associated_ids).order_by("pk")
    )
    payout_by_id = {row.pk: row for row in locked_payouts}
    payout = payout_by_id.get(selected_id)
    if selected_id is not None and (payout is None or not is_bank_payout(payout)):
        raise ValueError("Quarantined bank payout not found")

    # Match the payout/reversal paths' lock order: payout -> wallet -> evidence.
    # The previous evidence -> payout -> wallet order could deadlock an automatic
    # bank sweep holding the payout/wallet while this resolver held the case.
    locked_wallet = Wallet.objects.select_for_update().get(user_id=hint["user_id"])
    evidence = (ReversalEvidence.objects.select_for_update()
                .filter(pk=evidence_id).first())
    if (evidence is None or evidence.state not in _ACTIVE_REVERSAL_STATES
            or evidence.user_id != hint["user_id"]):
        raise ValueError("This reversal evidence is no longer awaiting review")
    if _model_evidence_snapshot(evidence) != _evidence_snapshot(evidence_snapshot):
        raise ValueError(
            "Reversal evidence changed after this request was submitted; "
            "review the current evidence and create a new approval."
        )
    current_associated_ids = set(evidence.associated_payouts.filter(
        user_id=evidence.user_id, direction=Transaction.OUT,
    ).values_list("pk", flat=True))
    if evidence.payout_id:
        current_associated_ids.add(evidence.payout_id)
    if payout is not None and current_associated_ids and payout.pk not in current_associated_ids:
        raise ValueError(
            "Reversal evidence changed after this request was submitted; "
            "review the current evidence and create a new approval."
        )
    if evidence.payout_id is None and payout is not None:
        if disposition not in attach_dispositions:
            raise ValueError(
                "Do not attach this unmatched evidence to a payout for the chosen treatment"
            )
        evidence.payout = payout
        evidence.associated_payouts.add(payout)
        evidence.version += 1

    observed_amounts = set(evidence.observations.values_list("amount", flat=True))
    if evidence.state == ReversalEvidence.CONFLICT:
        received = _reversal_amount(confirmed_amount)
        if received is None:
            raise ValueError(
                "Conflicting evidence requires an explicitly confirmed observed amount"
            )
        if received not in observed_amounts:
            raise ValueError("Confirmed amount must match one of the observed bank amounts")
    else:
        received = evidence.amount
        if confirmed_amount not in (None, "") and not _same_money(confirmed_amount, received):
            raise ValueError("Confirmed amount does not match the reviewed evidence")

    ledger_reference = evidence.ledger_reference
    evidence_row = None
    if evidence.ledger_transaction_id:
        evidence_row = (Transaction.objects.select_for_update()
                        .filter(pk=evidence.ledger_transaction_id,
                                user=evidence.user, direction=Transaction.IN).first())
    elif ledger_reference:
        evidence_row = (Transaction.objects.select_for_update()
                        .filter(reference=ledger_reference, user=evidence.user,
                                direction=Transaction.IN).first())
    existing_credit = (evidence_row if evidence_row is not None
                       and evidence_row.transaction_status == Transaction.SUCCESS
                       else None)
    if evidence.reason.startswith("already_credited"):
        if existing_credit is None or not _same_money(existing_credit.amount, evidence.amount):
            raise ValueError(
                "The durable credited transaction no longer matches the approved evidence"
            )
    elif evidence_row is not None:
        evidence_meta = (evidence_row.meta or {}).get("wema_reversal_evidence")
        if (evidence_row.transaction_status != Transaction.FAILED
                or evidence_row.service != "Payout reversal evidence"
                or not _same_money(evidence_row.amount, evidence.amount)
                or not isinstance(evidence_meta, dict)
                or str(evidence_meta.get("inbound_reference_hash")
                       or _reversal_provider_hash(
                           evidence_meta.get("inbound_reference") or ""))
                    != evidence.provider_reference_hash):
            raise ValueError(
                "The durable reversal evidence no longer matches the approved evidence"
            )
    elif evidence.reason != "provider_success_after_refund":
        raise ValueError("The durable reversal evidence transaction is missing")

    resolution_digest = hashlib.sha256(
        f"{evidence.pk}|{approval_id}|{disposition}".encode()
    ).hexdigest().upper()
    resolution_reference = f"ZREV{resolution_digest[:40]}"
    if ReversalEvidenceResolution.objects.filter(approval_id=approval_id).exists():
        raise ValueError("This reversal approval has already been applied")

    movement = Decimal("0")
    movement_direction = ""
    movement_txn = None
    original_status = payout.transaction_status if payout else ""
    meta = dict(payout.meta or {}) if payout else {}
    prior_resolutions = ReversalEvidenceResolution.objects.none()
    other_unresolved = False
    prior_return_total = Decimal("0")
    fully_returned = False
    failed_refund_corrected = False
    if payout is not None:
        prior_resolutions = ReversalEvidenceResolution.objects.filter(
            Q(payout=payout)
            | Q(payout__isnull=True, evidence__payout=payout),
        ).distinct()
        fully_returned = bool(meta.get("wema_reversal")) or prior_resolutions.filter(
            disposition__in=("confirm_full_reversal", "automatic_full_reversal"),
        ).exists()
        prior_return_total = prior_resolutions.filter(
            disposition__in=("confirm_partial_return", "confirm_existing_credit"),
        ).aggregate(total=Sum("confirmed_amount"))["total"] or Decimal("0")
        other_unresolved = ReversalEvidence.objects.filter(
            Q(payout=payout) | Q(associated_payouts=payout),
            state__in=_ACTIVE_REVERSAL_STATES,
        ).exclude(pk=evidence.pk).exists()
        marker = meta.get("wema_reversal_quarantine") or {}
        failed_refund_corrected = (
            isinstance(marker, dict)
            and marker.get("failed_refund_correction_applied") is True
        ) or prior_resolutions.filter(
            disposition__in=("confirm_partial_return", "confirm_existing_credit"),
            movement_direction=Transaction.OUT,
        ).exists()

    def _record_adjustment(amount: Decimal, direction: str, service: str) -> None:
        nonlocal movement, movement_direction, movement_txn
        if amount <= 0:
            return
        if direction == Transaction.OUT:
            from banking.accounts import historical_balance
            from banking.models import PreviousBankArchive
            if (PreviousBankArchive.objects.filter(wallet=locked_wallet).exists()
                    and historical_balance(locked_wallet) < amount):
                raise InsufficientFunds("The archived balance cannot cover this historical correction; keep the hold for review")
            if locked_wallet.balance < amount:
                raise InsufficientFunds(
                    "The wallet no longer contains enough funds to apply this "
                    "reversal correction; keep the hold active and escalate recovery."
                )
            locked_wallet.balance -= amount
        else:
            locked_wallet.balance += amount
        locked_wallet.save(update_fields=["balance", "updated"])
        movement_txn = Transaction.objects.create(
            user=evidence.user,
            service=service,
            amount=amount,
            direction=direction,
            transaction_status=Transaction.SUCCESS,
            reference=resolution_reference,
            meta={
                "channel": "admin",
                "internal_movement": True,
                "reversal_resolution": True,
                "payout_reference": payout.reference if payout else "",
                "evidence_id": evidence.pk,
                "evidence_reference": ledger_reference,
                "disposition": disposition,
                "approval_id": approval_id,
            },
        )
        movement = amount
        movement_direction = direction
        from banking.accounts import retain_historical_refund
        retain_historical_refund(movement_txn, amount if direction == Transaction.IN else -amount)

    payout_required = {
        "confirm_provider_success", "confirm_partial_return",
        "confirm_existing_credit", "confirm_full_reversal",
    }
    if disposition in payout_required and payout is None:
        raise ValueError("Attach this unmatched evidence to its payout before confirming a return")

    if disposition == "credit_as_deposit":
        if existing_credit is not None:
            raise ValueError("The returned credit is already present in the ledger")
        _record_adjustment(received, Transaction.IN,
                           "Bank credit released from reversal review")
    elif disposition == "retain_existing_as_deposit":
        if existing_credit is None or not _same_money(existing_credit.amount, received):
            raise ValueError("No matching successful ledger credit is recorded")
    elif disposition == "correct_duplicate_credit":
        if existing_credit is None or not _same_money(existing_credit.amount, received):
            raise ValueError("No matching successful ledger credit is recorded")
        _record_adjustment(received, Transaction.OUT,
                           "Duplicate returned-credit correction")
    elif disposition == "dismiss_duplicate_evidence":
        if existing_credit is not None:
            raise ValueError(
                "A successful ledger credit cannot be dismissed without an accounting correction"
            )
    elif disposition == "confirm_provider_success":
        if evidence.reason != "provider_success_after_refund":
            raise ValueError("This evidence is not a provider-success/refund conflict")
        if payout.transaction_status != Transaction.FAILED:
            raise ValueError("The payout refund is no longer present")
        from banking.accounts import historical_balance, retain_historical_refund
        from banking.models import PreviousBankArchive
        archived = ((payout.meta or {}).get("provider") != "9psb"
                    and PreviousBankArchive.objects.filter(wallet=locked_wallet).exists())
        if archived and historical_balance(locked_wallet) < payout.amount:
            raise InsufficientFunds(
                "The archived balance cannot cover this historical correction; keep the hold for review"
            )
        if locked_wallet.balance < payout.amount:
            raise InsufficientFunds(
                "The refunded funds are no longer available; keep the hold active "
                "and escalate customer recovery."
            )
        # Do not add a second OUT ledger row: changing the original payout from
        # FAILED back to SUCCESS makes that original debit count again. The direct
        # wallet correction mirrors the status transition exactly once.
        locked_wallet.balance -= payout.amount
        locked_wallet.save(update_fields=["balance", "updated"])
        if archived:
            # The original payout's status restores the ledger debit. This
            # non-posting evidence only reduces the separate historical hold;
            # it must neither charge the ledger twice nor consume new-bank funds.
            movement_txn = Transaction.objects.create(
                user=evidence.user, service="Historical debit restoration evidence",
                amount=payout.amount, direction=Transaction.OUT,
                transaction_status=Transaction.FAILED, reference=resolution_reference,
                meta={"internal_evidence": True, "payout_reference": payout.reference,
                      "evidence_id": evidence.pk, "approval_id": approval_id},
            )
            retain_historical_refund(movement_txn, -payout.amount)
        payout.transaction_status = Transaction.SUCCESS
        movement = payout.amount
        movement_direction = Transaction.OUT
        historical_return = meta.pop("wema_reversal", None)
        if historical_return:
            meta["wema_reversal_reviewed"] = {
                "evidence_id": evidence.pk,
                "reviewed_at": timezone.now().isoformat(),
            }
        meta.pop("reconcile", None)
    elif disposition == "confirm_partial_return":
        if fully_returned:
            raise ValueError(
                "This payout was already fully returned; classify the later row separately"
            )
        if received >= payout.amount:
            raise ValueError("Partial-return resolution requires less than the payout amount")
        if prior_return_total + received > payout.amount:
            raise ValueError(
                "Confirmed payout returns would exceed the original payout; "
                "classify any excess as a separate deposit."
            )
        if existing_credit is not None:
            raise ValueError("Use confirm_existing_credit for an already-credited return")
        if payout.transaction_status == Transaction.FAILED:
            if failed_refund_corrected:
                _record_adjustment(received, Transaction.IN,
                                   "Additional payout partial return")
            else:
                _record_adjustment(payout.amount - received, Transaction.OUT,
                                   "Payout partial-return correction")
                marker = meta.get("wema_reversal_quarantine") or {}
                marker = dict(marker) if isinstance(marker, dict) else {}
                marker["failed_refund_correction_applied"] = True
                meta["wema_reversal_quarantine"] = marker
        else:
            _record_adjustment(received, Transaction.IN, "Payout partial return")
            payout.transaction_status = Transaction.SUCCESS
            meta.pop("reconcile", None)
    elif disposition == "confirm_existing_credit":
        if fully_returned:
            raise ValueError(
                "This payout was already fully returned; the later credit needs a separate treatment"
            )
        if (existing_credit is None
                or not evidence.reason.startswith("already_credited")
                or not _same_money(existing_credit.amount, received)):
            raise ValueError("No matching successful ledger credit is recorded")
        if prior_return_total + received > payout.amount:
            raise ValueError(
                "Confirmed payout returns would exceed the original payout; "
                "classify any excess as a separate deposit."
            )
        if payout.transaction_status == Transaction.FAILED:
            if not failed_refund_corrected:
                _record_adjustment(payout.amount, Transaction.OUT,
                                   "Duplicate payout-refund correction")
                marker = meta.get("wema_reversal_quarantine") or {}
                marker = dict(marker) if isinstance(marker, dict) else {}
                marker["failed_refund_correction_applied"] = True
                meta["wema_reversal_quarantine"] = marker
        else:
            payout.transaction_status = Transaction.SUCCESS
            meta.pop("reconcile", None)
    elif disposition == "confirm_full_reversal":
        if fully_returned:
            raise ValueError(
                "This payout was already fully returned; use duplicate-evidence classification"
            )
        if received != payout.amount:
            raise ValueError("Full-reversal resolution requires the exact payout amount")
        if existing_credit is not None:
            raise ValueError("Use confirm_existing_credit for an already-credited return")
        if other_unresolved or prior_return_total:
            raise ValueError(
                "Resolve the payout's other returned-credit evidence before a full reversal"
            )
        if payout.transaction_status != Transaction.FAILED:
            from banking.accounts import retain_historical_refund
            retain_historical_refund(payout)
            locked_wallet.balance += payout.amount
            locked_wallet.save(update_fields=["balance", "updated"])
            payout.transaction_status = Transaction.FAILED
            movement = payout.amount
            movement_direction = Transaction.IN
        meta["wema_reversal"] = {
            "inbound_reference": evidence.provider_reference,
            "ledger_reference": ledger_reference,
            "amount": str(received),
            "applied_at": timezone.now().isoformat(),
            "approval_id": approval_id,
        }
        meta.pop("reconcile", None)

    actor_label = (getattr(actor, "email", "") or getattr(actor, "username", "")
                   or str(getattr(actor, "pk", actor)))
    resolved_time = timezone.now()
    resolution = {
        "disposition": disposition,
        "reason": reason[:300],
        "actor": actor_label,
        "approval_id": approval_id,
        "reference": movement_txn.reference if movement_txn else "",
        "movement": str(movement),
        "direction": movement_direction,
        "original_status": original_status,
        "final_status": payout.transaction_status if payout else "",
        "selected_payout": payout.reference if payout else "",
        "resolved_at": resolved_time.isoformat(),
    }
    ReversalEvidenceResolution.objects.create(
        evidence=evidence,
        payout=payout,
        disposition=disposition,
        reason=reason[:300],
        confirmed_amount=received,
        actor=actor,
        approval_id=approval_id,
        movement_transaction=movement_txn,
        movement_amount=movement if movement else None,
        movement_direction=movement_direction,
        payout_status_before=original_status,
        payout_status_after=payout.transaction_status if payout else "",
    )
    evidence.state = ReversalEvidence.RESOLVED
    evidence.version += 1
    evidence.resolved_amount = received
    evidence.resolution_disposition = disposition
    evidence.resolution_reason = reason[:300]
    evidence.resolution_approval_id = approval_id
    evidence.resolved_by = actor
    evidence.resolved_at = resolved_time
    evidence.save(update_fields=[
        "payout", "state", "version", "resolved_amount",
        "resolution_disposition", "resolution_reason", "resolution_approval_id",
        "resolved_by", "resolved_at", "last_seen",
    ])

    active = False
    if payout is not None:
        payout.meta = meta
        payout.save(update_fields=["transaction_status", "meta"])
    # Resolve compatibility holds for every same-wallet payout implicated by this
    # one provider event. Only the explicitly selected payout's balance/status was
    # changed; the others are released as alternate associations, not movements.
    for linked_payout in locked_payouts:
        if payout is not None and linked_payout.pk == payout.pk:
            linked_payout = payout
        marker = _sync_reversal_quarantine_summary(
            linked_payout, resolution=resolution)
        if payout is not None and linked_payout.pk == payout.pk:
            active = marker["active"]
    return {
        "reference": payout.reference if payout else evidence.provider_reference,
        "evidence_id": evidence.pk,
        "disposition": disposition,
        "status": payout.transaction_status if payout else "resolved",
        "movement": str(movement),
        "direction": movement_direction,
        "resolution_reference": movement_txn.reference if movement_txn else "",
        "resolved_at": resolved_time.isoformat(),
        "quarantine_active": active,
    }


def transfer(sender, recipient, amount, note: str = "", idempotency_key: str = "",
             channel: str = "", expected_fee=None) -> tuple[Transaction, Transaction | None]:
    """Send through the bank; only a confirmed notification credits the recipient."""
    from banking.accounts import account_for, same_name
    from banking.adapter import resolve_account
    from transfers.models import Bank
    from transfers.services import PayoutError, execute_payout, bank_name_keys
    destination = account_for(recipient)
    if (not destination or destination.status != "active" or not recipient.is_active
            or not recipient.email_verified or not recipient.phone_verified):
        raise PayoutError("recipient_unavailable", "This recipient has not completed bank account setup.")
    candidates = [row for row in Bank.objects.filter(active=True).exclude(bank_code="")
        if "alias:9psb" in bank_name_keys(row.name)]
    bank = candidates[0] if len(candidates) == 1 else None
    if not bank:
        raise PayoutError("rail_unavailable", "The bank directory needs to be refreshed.")
    resolved = resolve_account(destination.number, bank.bank_code)
    if (not resolved.get("success") or resolved.get("mock")
            or resolved.get("bank_code", bank.bank_code) != bank.bank_code
            or not same_name(destination.name, resolved.get("name", ""))):
        raise PayoutError("recipient_unavailable", "We could not confirm this recipient's bank account.")
    recipient.refresh_from_db()
    current = account_for(recipient)
    if (not current or current.pk != destination.pk or current.number != destination.number
            or current.status != "active" or not recipient.is_active
            or not recipient.phone_verified or not recipient.email_verified):
        raise PayoutError("recipient_unavailable", "This recipient's bank account is no longer available.")
    outgoing = execute_payout(sender, Decimal(str(amount)), destination.number, bank,
        resolved["name"], note=note, idempotency_key=idempotency_key, channel=channel, expected_fee=expected_fee)
    return outgoing, None


@db_transaction.atomic
def _local_transfer(*args, **kwargs):
    raise LimitExceeded("Bank payments require a bank instruction and confirmed settlement")



def sync_bank_tier(wallet) -> int:
    from banking.accounts import update_account
    from banking.models import BankAccount
    bank = BankAccount.objects.filter(wallet=wallet).first()
    if not bank:
        return 0
    return update_account(bank).tier


def bank_spent_today(user) -> Decimal:
    """Total already sent OUT of the NUBAN today, against the bank's daily cap.

    Counts payouts and partner-bank VAS purchases: both debit the customer's
    NUBAN. Retired provider purchases and internal evidence do not consume that
    bank limit. Each original outbound ledger row is counted once; inbound bank
    history used to confirm it cannot add a second spend.

    PENDING rows count: a payout in flight can still settle, and excluding it would
    let a burst of concurrent transfers each see an empty day. FAILED rows do not —
    those are already refunded.

    The day boundary is local (Africa/Lagos), matching the bank's own.
    """
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = Transaction.objects.filter(
        user=user, direction=Transaction.OUT, currency="NGN", created__gte=start,
        transaction_status__in=(Transaction.PENDING, Transaction.SUCCESS),
    ).only("amount", "meta", "service")
    funding = set(BillFundingBinding.objects.filter(transaction__in=rows).values_list(
        "transaction_id", flat=True))
    def debits_bank(row):
        if row.pk in funding:
            return True
        meta = row.meta if isinstance(row.meta, dict) else {}
        if meta.get("internal_evidence") or meta.get("internal_movement"):
            return False
        partner_vas = (is_vas_purchase(row)
                       and (meta.get("vas_rail") == "wema"
                            or (meta.get("provider_purchase") is True
                                and not meta.get("vas_rail"))))
        return is_bank_payout(row) or partner_vas

    return sum((r.amount for r in rows if debits_bank(r)), Decimal("0"))


def assert_customer_spending_available(user) -> None:
    from banking.payments import spending_account
    spending_account(user)


def is_biller_service(service) -> bool:
    """Only retained utility products; never cards, transfers or savings."""
    return bool(re.match(r"^(airtime|data|cable|electricity|remita|betting|exam|utility bill)(?:\b|\s)",
                         str(service or "").strip(), re.I))


def _biller_funding_context(user, amount=Decimal("0"), *, wallet=None):
    from banking.client import config
    from banking.payments import spending_account
    if not config().get("VAS_ENABLED"):
        raise LimitExceeded("Bill payments are temporarily unavailable.")
    bank = spending_account(user)
    return bank.number, ""


def biller_spending_available(user) -> bool:
    try:
        _biller_funding_context(user)
    except (LimitExceeded, Wallet.DoesNotExist):
        return False
    return True


@db_transaction.atomic
def biller_source_for_transaction(reference, *, amount, source_account="") -> str:
    """Authorize only a canonical pending bill reservation, never mutable meta."""
    try:
        amount = Decimal(str(amount))
        if not amount.is_finite() or amount <= 0:
            raise ValueError
    except (InvalidOperation, TypeError, ValueError):
        raise LimitExceeded("Invalid bill amount.") from None
    binding = BillFundingBinding.objects.select_related("transaction__user").filter(
        transaction__reference=reference).first()
    if binding is None:
        raise LimitExceeded("A verified bill funding reservation is required.")
    txn = binding.transaction
    wallet = Wallet.objects.select_for_update().get(user_id=txn.user_id)
    txn.refresh_from_db(fields=["transaction_status"])
    if (txn.transaction_status != Transaction.PENDING or txn.direction != Transaction.OUT
            or txn.currency != "NGN" or not is_biller_service(txn.service)
            or txn.amount != amount or BillFundingRefund.objects.filter(binding=binding).exists()
            or (source_account and source_account != binding.source_account)):
        raise LimitExceeded("Bill funding does not match this payment.")
    current = _biller_funding_context(txn.user, wallet=wallet)
    if (current is None or current[0] != binding.source_account
            or current[1] != binding.approval_reference):
        raise LimitExceeded("Bill funding configuration changed; retry after review.")
    return binding.source_account


def bank_spend_error(user, amount, *, biller=False) -> str | None:
    from banking.payments import bank_limit_error
    return bank_limit_error(user, amount)
