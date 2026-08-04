"""Wallet ledger operations — the heart of the money logic.

Every debit/credit goes through here so balance changes and ledger rows are
always written together, atomically, with row locking to prevent double-spend.
"""
import json
import logging
import re
import secrets
from decimal import Decimal

from django.db import IntegrityError, transaction as db_transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .models import FundingIntent, Transaction, Wallet

log = logging.getLogger("wallet")


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
    return Transaction.objects.filter(user=user, idempotency_key=key).first()


def make_reference(prefix: str = "ZTCH") -> str:
    return f"{prefix}{secrets.token_hex(6).upper()}"


def get_or_create_wallet(user) -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


def wallet_expected_balance(user_id) -> Decimal:
    """The balance implied by the append-only ledger for this user.

    The ledger state machine:  expected = sum(IN, Successful) - sum(OUT, Pending
    or Successful). Debits deduct at PENDING (a FAILED debit is refunded back);
    credits are only ever written Successful. This is the single source of truth
    for both integrity checks: the internal one (ledger vs stored balance) and the
    external one (ledger vs the bank's NUBAN balance).
    """
    credits = (Transaction.objects
               .filter(user_id=user_id, direction=Transaction.IN,
                       transaction_status=Transaction.SUCCESS)
               .aggregate(s=Sum("amount"))["s"] or Decimal("0"))
    debits = (Transaction.objects
              .filter(user_id=user_id, direction=Transaction.OUT)
              .filter(Q(transaction_status=Transaction.PENDING)
                      | Q(transaction_status=Transaction.SUCCESS))
              .aggregate(s=Sum("amount"))["s"] or Decimal("0"))
    return credits - debits


def ensure_reserved_account(user, bvn: str = "", nin: str = "") -> Wallet:
    """Reserve a dedicated virtual account for the user's wallet, exactly once.

    Idempotent: returns immediately if the wallet already carries a number, so it
    is safe to call from every KYC path. Best-effort — a provider failure leaves
    the wallet numberless (the caller logs) and it is retried on the next KYC
    action. Wema needs a BVN/NIN to mint a dedicated account, so pass the raw value
    while it is still in hand at verification time.
    """
    from utility.providers import funding_account_get, funding_account_reserve

    wallet = get_or_create_wallet(user)
    if wallet.account_number:
        return wallet

    reference = f"ZITCH-WALLET-{user.id}"
    name = (user.get_full_name() or user.phone or "Zitch user").strip()
    email = user.email or f"{user.phone}@zitch.app"

    result = funding_account_reserve(reference, name, email, name, bvn=bvn, nin=nin)
    if not result.get("success"):
        # A duplicate accountReference means a prior attempt reserved the account
        # but we never persisted it — fetch rather than re-create (which the rail
        # rejects). If that also fails, leave the wallet numberless to retry later.
        existing = funding_account_get(reference)
        if not existing.get("success"):
            # Stash the provider's real reason on the (unsaved) instance so the caller can
            # surface it — a bad key vs a BVN/name mismatch vs "not configured"
            # turns a dead end into a fixable signal.
            wallet.reserve_error = result.get("message", "") or existing.get("message", "")
            return wallet
        result = existing

    wallet.account_number = result.get("account_number", "")
    wallet.bank_name = result.get("bank_name", "")
    wallet.account_name = result.get("account_name", "") or name
    wallet.account_reference = result.get("reference", "") or reference
    wallet.bank_accounts = result.get("accounts", []) or []
    wallet.save(update_fields=[
        "account_number", "bank_name", "account_name", "account_reference",
        "bank_accounts", "updated",
    ])
    return wallet


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
    if wallet.balance < amount:
        raise InsufficientFunds("Insufficient wallet balance")
    if enforce_limits:
        from common.http import spend_limit_error   # local: common.http imports from here
        breach = spend_limit_error(user, amount, service)
        if breach:
            raise LimitExceeded(breach)
    wallet.balance -= amount
    wallet.save(update_fields=["balance", "updated"])
    try:
        with db_transaction.atomic():  # savepoint: contain the unique violation
            return Transaction.objects.create(
                user=user,
                service=service,
                amount=amount,
                direction=Transaction.OUT,
                transaction_status=Transaction.PENDING,
                reference=reference or make_reference(),
                meta=meta or {},
                idempotency_key=idempotency_key,
            )
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
            return Transaction.objects.create(
                user=user,
                service=service,
                amount=amount,
                direction=Transaction.IN,
                transaction_status=Transaction.SUCCESS,
                reference=reference or make_reference("ZFND"),
                meta=meta or {},
                idempotency_key=idempotency_key,
            )
    except IntegrityError:
        if idempotency_key:
            raise DuplicateTransaction(idempotency_key)
        raise


@db_transaction.atomic
def refund(txn: Transaction) -> None:
    """Reverse a failed debit and mark the row Failed."""
    wallet = Wallet.objects.select_for_update().get(user=txn.user)
    wallet.balance += txn.amount
    wallet.save(update_fields=["balance", "updated"])
    txn.transaction_status = Transaction.FAILED
    txn.save(update_fields=["transaction_status"])


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
    txn = Transaction.objects.select_for_update().get(pk=txn.pk)
    if txn.transaction_status == Transaction.SUCCESS:
        return "success"
    if txn.transaction_status == Transaction.FAILED:
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
        # (a Wema VAS purchase must not be requeried on VTU.ng, or vice versa).
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
    wallet.balance += txn.amount
    wallet.save(update_fields=["balance", "updated"])
    meta.pop("reconcile", None)
    meta["failure"] = result.get("message", "")
    txn.meta = meta
    txn.transaction_status = Transaction.FAILED
    txn.save(update_fields=["transaction_status", "meta"])
    return "failed"


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
    reconcile_meta = {**(meta or {}), "reconcile": True}
    txn = debit(user, amount, service, meta=reconcile_meta, idempotency_key=idempotency_key)
    result = provider_call(txn.reference)
    status = settle_or_refund(txn, result)
    return status, txn, result


# The mock rail stamps the NUBANs it invents with this (utility.wema._mock_account),
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


def attach_existing_bank_account(user, *, using_bvn: bool | None = None) -> tuple:
    """Attach the NUBAN the rail ALREADY holds for `user`. Returns (wallet, detail).

    `wallet` is None when nothing could be attached; `detail` always explains why,
    for an operator or an API caller to relay.

    The rail refuses to create a customer it already has, so an account that exists
    on their side but not on ours can only be recovered by reading it back. Looked
    up by the user's OWN phone number — the same key creation would have used — so
    it can only ever adopt that customer's account.

    `using_bvn` picks the wallet product to ask; None tries BVN and falls back to
    NIN, since either could have created the account and the operator running this
    has no way to know which.

    Deliberately does NOT touch the KYC tier or mark the identity verified: the OTP
    round-trip is what attests identity, and this path has no OTP.
    """
    from utility import wema as wema_provider

    wallet = get_or_create_wallet(user)
    if wallet.account_number:
        return wallet, "This wallet already has an account number."
    products = (True, False) if using_bvn is None else (using_bvn,)
    acct, product = {}, True
    for product in products:
        acct = wema_provider.get_account_details(user.phone or "", bvn=product)
        if acct.get("success") and str(acct.get("account_number") or "").strip():
            break
    number = str(acct.get("account_number") or "").strip()
    if not number:
        return None, (acct.get("message") or "").strip() or "The rail holds no account for this phone number."

    wallet, outcome = provision_wema_account(
        user, account_number=number, account_name=acct.get("account_name", ""),
        bank_name=acct.get("bank_name", ""), source="adopt-existing")
    if outcome.startswith("conflict"):
        log.warning("wema_adopt_conflict user=%s outcome=%s", user.id, outcome)
        return None, f"Could not attach it ({outcome})."
    # A partnership NUBAN is created under a Post-No-Debit hold, and a payout debits
    # this very account, so an adopted one has to have the hold lifted too.
    # Best-effort, exactly as the OTP path treats it.
    pnd = wema_provider.lift_debit_restriction(number, bvn=product)
    if not pnd.get("success"):
        log.warning("wema_pnd_lift_failed user=%s account=%s msg=%s",
                    user.id, number, pnd.get("message", ""))
    log.info("wema_adopted_existing_account user=%s outcome=%s", user.id, outcome)
    return wallet, "Reconnected the account the bank already held."


def is_bank_payout(txn) -> bool:
    """True for a bank-transfer (Wema payout) payout, as opposed to a VTU.ng
    purchase.

    Both leave a PENDING + ``meta.reconcile`` outbound row, but a payout is
    settled by the transfer webhook (settle_payout / reverse_transfer) and has
    NO VTU.ng record — requerying it via vtu_requery would query the wrong provider
    for a reference VTU.ng never saw. Bank payouts are the only such rows that
    carry a ``bank`` in meta (set in transfers.services.execute_payout)."""
    return bool((txn.meta or {}).get("bank"))


def pending_vtu_purchases(cutoff):
    """PENDING outbound VTU.ng purchases due for requery, EXCLUDING bank-transfer
    payouts. The reconcile sweep (cron + on-demand) requeries each row via
    vtu_requery, which is only correct for VTU.ng purchases; bank payouts are
    settled by the reconcile_wema poller, so they must not be swept here."""
    return Transaction.objects.filter(
        transaction_status=Transaction.PENDING,
        direction=Transaction.OUT,
        meta__reconcile=True,
        created__lte=cutoff,
    ).exclude(meta__has_key="bank")


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
    if txn is None or txn.transaction_status == Transaction.FAILED:
        return None
    wallet = Wallet.objects.select_for_update().get(user=txn.user)
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
    if txn is None or txn.transaction_status != Transaction.PENDING:
        return None
    meta = dict(txn.meta or {})
    meta.pop("reconcile", None)
    txn.meta = meta
    txn.transaction_status = Transaction.SUCCESS
    txn.save(update_fields=["transaction_status", "meta"])
    return txn


@db_transaction.atomic
def settle_funding(reference: str, verified_amount=None) -> Transaction | None:
    """Credit the wallet for a verified funding reference, exactly once.

    Locks the FundingIntent row so concurrent calls (the app's verify request
    AND the reconcile_wema poller running at the same time) can't double-credit.
    Returns the credit Transaction if this call performed the credit, else None.
    """
    try:
        intent = FundingIntent.objects.select_for_update().get(reference=reference)
    except FundingIntent.DoesNotExist:
        return None

    if intent.credited:
        return None  # already funded — idempotent no-op

    amount = Decimal(str(verified_amount)) if verified_amount is not None else intent.amount
    txn = credit(intent.user, amount, "Wallet top-up", meta={"reference": reference}, reference=reference)

    intent.status = FundingIntent.PAID
    intent.credited = True
    intent.amount = amount
    intent.save(update_fields=["status", "credited", "amount", "updated"])
    return txn


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
    if Transaction.objects.filter(reference=transaction_reference).exists():
        log.info("reserved_funding_duplicate txref=%s (already applied)", transaction_reference)
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


# Account-reference prefix that marks a wallet as provisioned on Wema/ALAT. Wema
# has no inbound-credit webhook, so these
# wallets are the ones the reconcile_wema poller sweeps for deposits.
WEMA_ACCOUNT_REF_PREFIX = "WEMA-WALLET-"


def wema_account_reference(user) -> str:
    return f"{WEMA_ACCOUNT_REF_PREFIX}{user.id}"


def wema_provisioned_wallets():
    """Wallets whose funding account lives on Wema (have a NUBAN + our Wema ref).

    These are polled for inbound credits because ALAT exposes no funding webhook."""
    return (Wallet.objects
            .filter(account_reference__startswith=WEMA_ACCOUNT_REF_PREFIX)
            .exclude(account_number=""))


def self_payout_references(user) -> list[str]:
    """References of this user's outbound bank-transfer payouts (rows carrying a
    ``bank`` in meta) — the set an inbound polled credit row is matched against
    to spot a payout that BOUNCED BACK into the sender's own NUBAN."""
    return [r for r in
            Transaction.objects.filter(user=user, direction=Transaction.OUT,
                                       meta__has_key="bank")
            .values_list("reference", flat=True) if r]


def _reversal_reference(tx: dict, references) -> str | None:
    """The payout reference this credit-history row is a reversal of, or None.

    Matched by substring over the WHOLE raw row (any field — referenceId,
    narration, remarks…), so it doesn't depend on Wema's undocumented reversal
    shape. Ledger references are long unique tokens, so a hit can only mean the
    row relates to that payout. Only the wallet owner's OWN payout references are
    ever passed in: an inbound credit carrying ANOTHER user's payout reference is
    a genuine deposit (their payout arriving here) and must still credit."""
    if not references:
        return None
    try:
        blob = json.dumps(tx, default=str).upper()
    except (TypeError, ValueError):
        blob = str(tx).upper()
    for ref in references:
        if ref.upper() in blob:
            return ref
    return None


_WEMA_REVERSAL_MARKER = re.compile(
    r"\b(?:REVERSAL|REVERSED|BOUNCED)\b|BOUNCE\s+BACK|RETURN\s+OF\s+FUNDS|RETURNED\s+TRANSFER",
    re.IGNORECASE,
)


def _looks_like_unmatched_reversal(tx: dict) -> bool:
    """Whether a Wema credit row carries an explicit reversal marker.

    Some Wema reversal rows omit the original payout reference. When the wallet
    has outbound payouts, treating such a row as fresh funding can double-credit
    the user once the payout poller also refunds it. These rows are quarantined
    for manual reconciliation instead of moving money automatically.
    """
    try:
        blob = json.dumps(tx, default=str)
    except (TypeError, ValueError):
        blob = str(tx)
    return bool(_WEMA_REVERSAL_MARKER.search(blob))


def apply_wema_credit(wallet, tx: dict, self_refs: list[str] | None = None) -> Transaction | None:
    """Credit `wallet` for one inbound Wema transaction-history row, exactly once.

    Skips non-credit / zero rows. Idempotent on Wema's per-transaction referenceId,
    stored under a ``WEMA-CR-`` prefix so the ledger key can NEVER collide with a
    payout (``ZTRF…``), funding (``ZPAY…``/``ZFND…``) or internal-transfer reference
    in the shared, globally-unique ``Transaction.reference`` namespace — a collision
    would otherwise make settle_reserved_funding treat a real deposit as 'already
    credited' and silently drop it. Returns the credit row if applied, else None.

    A credit row that references one of the user's OWN outbound payouts is a
    payout REVERSAL (the money bounced back into the sender's NUBAN), not a
    deposit. It is routed through ``reverse_transfer`` — which refunds at most
    once, ever, across this sweep and the payout-status poller — instead of being
    credited as funding. Without this, a bounced payout was counted twice: once
    here and once when the payout poller reversed the FAILED transfer.
    ``self_refs`` lets the sweep pass the user's payout references once per
    wallet; when omitted they are looked up.
    """
    from utility import wema

    norm = wema.normalize_transaction(tx)
    if not norm["is_credit"] or not norm["reference"]:
        return None
    if not norm["settled"]:
        # A Failed/Pending inbound row per Wema's status legend: the money hasn't
        # actually landed. Crediting a Pending row now (before it settles) would
        # leak float if it later fails; a Failed row must never credit. A Pending
        # deposit is picked up on a later sweep once it flips to Successfull
        # (idempotent on referenceId), so holding it back loses nothing.
        log.info("wema_credit_unsettled ref=%s status=%s account=%s",
                 norm["reference"], norm["status"], wallet.account_number)
        return None
    if norm["amount_naira"] is None:
        # A credit row we can't price (unparseable amount) — never silently lose it.
        log.warning("wema_credit_unparseable_amount ref=%s raw_amount=%r account=%s",
                    norm["reference"], tx.get("amount"), wallet.account_number)
        return None
    if norm["amount_naira"] <= Decimal("0"):
        return None
    refs = self_payout_references(wallet.user) if self_refs is None else self_refs
    matched = _reversal_reference(tx, refs)
    if matched:
        reversed_txn = reverse_transfer(matched)
        log.warning("wema_credit_payout_reversal ref=%s payout=%s reversed=%s account=%s",
                    norm["reference"], matched, bool(reversed_txn), wallet.account_number)
        return None
    if refs and _looks_like_unmatched_reversal(tx):
        log.error(
            "wema_credit_unmatched_reversal_quarantined ref=%s account=%s amount=%s",
            norm["reference"], wallet.account_number, norm["amount_naira"],
        )
        return None
    ledger_ref = f"WEMA-CR-{norm['reference']}"
    return settle_reserved_funding(ledger_ref, norm["amount_naira"], wallet.user)


def pending_bank_payouts(cutoff):
    """PENDING outbound bank-transfer payouts (rows carrying a ``bank`` in meta),
    due for settlement reconciliation.

    Wema exposes NO payout
    webhook, so a Wema transfer returned PENDING/PROCESSING would otherwise sit
    debited forever. reconcile_wema polls confirm_transfer_status for these and
    settles/reverses them. The mirror of pending_vtu_purchases (which EXCLUDES
    bank payouts)."""
    return Transaction.objects.filter(
        transaction_status=Transaction.PENDING,
        direction=Transaction.OUT,
        meta__reconcile=True,
        meta__has_key="bank",
        created__lte=cutoff,
    )


@db_transaction.atomic
def transfer(sender, recipient, amount, note: str = "", idempotency_key: str = "") -> tuple[Transaction, Transaction]:
    """Move funds between two Zitch wallets atomically.

    Both wallet rows are locked (in a stable order to avoid deadlocks) so the
    debit and credit either both happen or neither does. Raises InsufficientFunds
    if the sender can't cover the amount. With an `idempotency_key`, a duplicate
    send (same sender + key) raises DuplicateTransaction with nothing moved.
    Returns (debit_txn, credit_txn).
    """
    amount = Decimal(str(amount))

    # Make sure both wallet rows exist before we lock them. A recipient only gets
    # a wallet when they first authenticate, so a transfer to a user who exists
    # but has never signed in (admin-created/seeded account) would otherwise miss
    # from the locked read below and raise KeyError -> 500 with nothing moved.
    get_or_create_wallet(sender)
    get_or_create_wallet(recipient)

    # Lock both wallets in a deterministic order (by user id) to prevent
    # deadlocks when two users transfer to each other simultaneously.
    first, second = sorted([sender.id, recipient.id])
    wallets = {w.user_id: w for w in Wallet.objects.select_for_update().filter(user_id__in=[first, second])}
    sw = wallets[sender.id]
    rw = wallets[recipient.id]

    if sw.balance < amount:
        raise InsufficientFunds("Insufficient wallet balance")

    ref = make_reference("ZTRF")
    sw.balance -= amount
    rw.balance += amount
    sw.save(update_fields=["balance", "updated"])
    rw.save(update_fields=["balance", "updated"])

    recipient_name = (recipient.get_full_name() or recipient.phone or "Zitch user").strip()
    sender_name = (sender.get_full_name() or sender.phone or "Zitch user").strip()

    try:
        with db_transaction.atomic():  # savepoint: contain the unique violation
            debit_txn = Transaction.objects.create(
                user=sender, service=f"Transfer to {recipient_name}", amount=amount,
                direction=Transaction.OUT, transaction_status=Transaction.SUCCESS,
                reference=ref, meta={"to": recipient.phone, "note": note},
                idempotency_key=idempotency_key,
            )
            credit_txn = Transaction.objects.create(
                user=recipient, service=f"Transfer from {sender_name}", amount=amount,
                direction=Transaction.IN, transaction_status=Transaction.SUCCESS,
                reference=f"{ref}-C", meta={"from": sender.phone, "note": note},
            )
    except IntegrityError:
        if idempotency_key:
            raise DuplicateTransaction(idempotency_key)
        raise
    return debit_txn, credit_txn



# ---------------------------------------------------------------------------
# Wema account provisioning — shared by the OTP flow and the bank's
# Account Creation callback, so the two can never drift apart.
# ---------------------------------------------------------------------------
def provision_wema_account(user, *, account_number: str, account_name: str = "",
                           bank_name: str = "", source: str = "otp") -> tuple:
    """Attach a Wema NUBAN to ``user``'s wallet. Returns ``(wallet, outcome)``.

    outcome is one of:
      "provisioned" — newly attached
      "already"     — this wallet already had this exact NUBAN (idempotent replay)
      "conflict:owned"    — the NUBAN belongs to a DIFFERENT wallet
      "conflict:replaced" — this wallet already has a different NUBAN

    Both conflicts are refusals: silently overwriting a funding account would
    strand money already sent to the old NUBAN, and stealing one from another
    wallet would misdirect deposits. The caller decides how to surface them.

    ``account_reference`` is what makes the reconcile poller sweep the wallet for
    deposits (wema_provisioned_wallets), so it is always written together with the
    number — a NUBAN without it is invisible to reconciliation.
    """
    number = (account_number or "").strip()
    if not number:
        return None, "conflict:blank"
    with db_transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(pk=get_or_create_wallet(user).pk)
        if wallet.account_number:
            return wallet, "already" if wallet.account_number == number else "conflict:replaced"
        if Wallet.objects.filter(account_number=number).exclude(pk=wallet.pk).exists():
            return wallet, "conflict:owned"
        wallet.account_number = number
        # Never persist a blank holder name — a funding account with no name can't be
        # safely paid into. Prefer the bank's name, else the registered legal name.
        wallet.account_name = ((account_name or "").strip()
                               or (user.get_full_name() or "").strip())
        wallet.bank_name = (bank_name or "").strip() or "Wema Bank"
        wallet.account_reference = wema_account_reference(user)
        try:
            wallet.save(update_fields=["account_number", "account_name", "bank_name",
                                       "account_reference", "updated"])
        except IntegrityError:
            log.warning("wema_account_persist_conflict user=%s account=%s source=%s",
                        user.id, number, source)
            return wallet, "conflict:owned"
    log.info("wema_account_provisioned user=%s account=%s source=%s", user.id, number, source)
    return wallet, "provisioned"


def sync_bank_tier(wallet) -> int:
    """Read the partner bank's tier for this NUBAN and store it. Returns the tier
    (0 when unknown/unreadable, which asserts no bank cap).

    The bank runs its own tier ladder with its own inflow/spend/balance caps, and it
    enforces them on the account regardless of our KYC tier — so knowing the real
    value is what lets us refuse a transfer the gateway would refuse anyway, with a
    useful message instead of a failed payout.
    """
    from utility import wema as wema_provider
    if not wallet.account_number:
        return 0
    res = wema_provider.get_kyc_status(wallet.account_number)
    if not res.get("success"):
        return wallet.bank_tier or 0
    raw = str(res.get("tier") or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    tier = int(digits) if digits and int(digits) in (1, 2, 3) else 0
    if tier and tier != wallet.bank_tier:
        wallet.bank_tier = tier
        wallet.save(update_fields=["bank_tier", "updated"])
        log.info("wema_bank_tier_synced wallet=%s account=%s tier=%s",
                 wallet.pk, wallet.account_number, tier)
    return tier or (wallet.bank_tier or 0)


def bank_spent_today(user) -> Decimal:
    """Total already sent OUT of the NUBAN today, against the bank's daily cap.

    Counts bank payouts only (``meta.bank``, the same predicate the authorisation
    callback uses) — a VTU purchase settles with the VAS provider and never debits
    the NUBAN, so counting it would restrict the customer for spend the bank never
    saw.

    PENDING rows count: a payout in flight can still settle, and excluding it would
    let a burst of concurrent transfers each see an empty day. FAILED rows do not —
    those are already refunded.

    The day boundary is local (Africa/Lagos), matching the bank's own.
    """
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = Transaction.objects.filter(
        user=user, direction=Transaction.OUT, created__gte=start,
        transaction_status__in=(Transaction.PENDING, Transaction.SUCCESS),
    ).only("amount", "meta")
    return sum((r.amount for r in rows if is_bank_payout(r)), Decimal("0"))


def bank_spend_error(user, amount) -> str | None:
    """The partner bank's own DAILY ceiling on outbound spend, or None.

    Checked in ADDITION to our KYC-tier limit, never instead of it: the two ladders
    are independent and the customer is bound by whichever is tighter. A provisioned
    account whose tier has not synced yet is treated as Tier 1 (the conservative bank
    default), rather than silently allowing a transfer the bank is likely to reject.

    The cap is CUMULATIVE — ALAT publishes it as "Daily Max Spend", not a per-transfer
    limit. Comparing only the single amount would let N transfers each under the cap
    sum past it, and the gateway would refuse whichever one crossed: the debit-then-
    reverse this check exists to prevent. So today's spend counts toward it.
    """
    from utility import wema as wema_provider
    wallet = Wallet.objects.filter(user=user).only("bank_tier", "account_number").first()
    if wallet is None or not wallet.account_number:
        return None
    bank_tier = wallet.bank_tier or 1
    cap = wema_provider.bank_tier_limit(bank_tier, "daily_spend")
    if cap is None:
        return None
    amount = Decimal(str(amount))
    remaining = cap - bank_spent_today(user)
    if amount > remaining:
        if remaining <= 0:
            return (f"You've reached your bank account's daily limit of ₦{cap:,.0f}. "
                    "Complete the next verification step to raise it.")
        return (f"This would pass your bank account's daily limit of ₦{cap:,.0f} — "
                f"₦{remaining:,.0f} left today. "
                "Complete the next verification step to raise it.")
    return None
