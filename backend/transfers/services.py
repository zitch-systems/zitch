"""Bank payout orchestration — shared by the HTTP API and the WhatsApp channel.

Keeps a single implementation of the money movement (debit -> provider payout ->
settle/refund -> save beneficiary) so both entry points behave identically.
Callers do their own auth / PIN / tier-limit checks and name-enquiry first, then
hand a resolved account name to `execute_payout`.
"""
from decimal import Decimal

from utility.providers import payout_send
from wallet.models import Transaction
from wallet.services import (
    DuplicateTransaction,
    InsufficientFunds,
    LimitExceeded,
    debit,
    refund,
)

from .models import Bank, Beneficiary


def detect_account_banks(account_number: str) -> list[dict]:
    """Auto-detect which bank(s) a 10-digit NUBAN belongs to.

    A NUBAN can't be mapped to a bank offline, so we run a name-enquiry across the
    active banks in parallel and keep the ones that resolve, returning
    ``[{"bank", "bank_name", "name"}]`` (``bank`` is our slug code). Usually one
    match; a number that's a valid account at two banks (different holders) returns
    both. Cached briefly per account number so a re-resolve/retry doesn't re-sweep.

    MOCK mode (no payout keys) returns a single deterministic match — fanning out
    there would make every bank "match" the stub — so the flow stays testable.
    """
    from concurrent.futures import ThreadPoolExecutor

    from django.core.cache import cache

    from utility.providers import payout_live, payout_resolve_account

    ckey = f"acctdetect:{account_number}"
    cached = cache.get(ckey)
    if cached is not None:
        return cached

    # Sweep only the `popular` banks (the high-volume set covering nearly all
    # transfer traffic): each probe is a paid provider name-enquiry, and the
    # picker now lists ~40+ banks — fanning out to all of them for every typed
    # account would be slow and expensive. An account at a non-popular bank
    # just isn't auto-detected; the user picks the bank manually (which
    # resolves at that one bank). Falls back to every active bank if no bank
    # is flagged popular (e.g. a stale seed).
    banks = list(Bank.objects.filter(active=True, popular=True).exclude(bank_code=""))
    if not banks:
        banks = list(Bank.objects.filter(active=True).exclude(bank_code=""))
    if not payout_live():
        # No live name-enquiry rail: this is a placeholder, NOT a real detection.
        # Flag it `mock` so callers don't present the stub bank/name as verified.
        matches = ([{"bank": banks[0].code, "bank_name": banks[0].name,
                     "name": "ADEYEMI WILLIAM", "mock": True}]
                   if banks else [])
        cache.set(ckey, matches, 60)
        return matches

    def probe(b):
        res = payout_resolve_account(account_number, b.bank_code)
        if res.get("success") and res.get("name"):
            return {"bank": b.code, "bank_name": b.name, "name": res["name"]}
        return None

    matches = []
    if banks:
        with ThreadPoolExecutor(max_workers=min(8, len(banks))) as ex:
            matches = [r for r in ex.map(probe, banks) if r]
    cache.set(ckey, matches, 600)
    return matches


class PayoutError(Exception):
    """A payout could not be completed.

    `kind` is one of: ``duplicate`` (idempotency key already used),
    ``insufficient`` (balance too low), or ``provider`` (the rail rejected it,
    wallet already refunded). `message` is safe to show the user.
    """

    def __init__(self, kind: str, message: str):
        self.kind = kind
        self.message = message
        super().__init__(message)


def execute_payout(user, amount: Decimal, account_number: str, bank, name: str,
                   note: str = "", idempotency_key: str = "") -> Transaction:
    """Debit the wallet, send the payout, settle/refund, and save the beneficiary.

    The caller must already have verified the PIN + tier limits and resolved
    `name` via the provider's name-enquiry. Raises PayoutError on a duplicate,
    insufficient funds, or a provider failure (wallet auto-refunded). Returns the
    settled (Successful) ledger transaction.
    """
    try:
        txn = debit(
            user, amount, f"Transfer to {name}",
            # Pre-flag `reconcile` ATOMICALLY with the debit, BEFORE the provider
            # call (mirroring run_provider_purchase). The debit commits on its own,
            # with no lock held across the network I/O, so a worker crash between the
            # send and the settle write would otherwise orphan a committed PENDING
            # row the payout sweep (filters meta__reconcile=True) could never find —
            # money debited, never settled or reversed. Cleared below only on a
            # definitively delivered result.
            meta={"account": account_number, "bank": bank.name, "note": note,
                  "reconcile": True},
            idempotency_key=idempotency_key,
        )
    except DuplicateTransaction:
        raise PayoutError("duplicate", "This request was already processed.")
    except InsufficientFunds:
        raise PayoutError("insufficient", "Insufficient wallet balance.")
    except LimitExceeded as exc:
        raise PayoutError("limit_exceeded", str(exc))

    # Wema per-user-balance model: debit the sender's own NUBAN.
    sender_source = getattr(getattr(user, "wallet", None), "account_number", "") or ""
    result = payout_send(amount, txn.reference, note or f"Transfer to {name}",
                         bank.bank_code, account_number, name, bank_name=bank.name,
                         source_account=sender_source)

    delivered = (result.get("success")
                 and (result.get("status") or "").upper() not in ("PENDING", "PROCESSING"))
    if delivered:
        # Confirmed sent — settle now and clear the reconcile flag.
        meta = dict(txn.meta or {})
        meta.pop("reconcile", None)
        txn.meta = meta
        txn.transaction_status = Transaction.SUCCESS
        txn.save(update_fields=["transaction_status", "meta"])
    elif result.get("success") or result.get("pending"):
        # Accepted-but-queued (PENDING/PROCESSING), OR an AMBIGUOUS outcome — a send
        # timeout / lost response on the non-idempotent transfer POST, where Wema may
        # already have paid the recipient. HOLD the debit: never refund a
        # maybe-delivered transfer (that would drain the float and, on retry, double-
        # pay). The row stays PENDING with the reconcile flag pre-set above; the
        # transfer webhook or the payout reconciler (verify_payout) settles it
        # (settle_payout) or reverses it (reverse_transfer) once the outcome is known.
        # The caller reports "processing", not "sent".
        pass
    else:
        # Definitive rejection (validation error / OTP-required misconfig): the
        # transfer was NOT executed, so refunding the sender is safe.
        refund(txn)
        raise PayoutError("provider", result.get("message", "Transfer failed"))

    # Auto-save / dedupe the beneficiary for next time.
    Beneficiary.objects.get_or_create(
        user=user, account_number=account_number, bank_name=bank.name,
        defaults={"name": name, "bank_code": bank.bank_code, "color": bank.color or "#0FA295"},
    )
    return txn
