"""Bank payout orchestration — shared by the HTTP API and the WhatsApp channel.

Keeps a single implementation of the money movement (debit -> provider payout ->
settle/refund -> save beneficiary) so both entry points behave identically.
Callers do their own auth / PIN / tier-limit checks and name-enquiry first, then
hand a resolved account name to `execute_payout`.
"""
from decimal import Decimal

from utility.providers import payout_send
from utility.wema import classify_transfer_status
from wallet.models import Transaction
from wallet.services import (
    DuplicateTransaction,
    InsufficientFunds,
    LimitExceeded,
    debit,
    refund,
)

from .models import Bank, Beneficiary

# Generic words that carry no identity: dropping them lets "Moniepoint MFB" match
# "Moniepoint Microfinance Bank" while keeping "First Bank" != "Fidelity Bank".
_NAME_NOISE = {"bank", "banks", "plc", "ltd", "limited", "nigeria", "nigerian", "ng",
               "mfb", "microfinance", "finance", "psb", "payment", "service", "services",
               "digital", "company", "co"}


def normalize_bank_name(name: str) -> str:
    """Identity-bearing tokens of a bank name, sorted and joined."""
    import re

    words = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    kept = [w for w in words if w not in _NAME_NOISE]
    return " ".join(sorted(kept or words))


def compare_bank_codes(remote: list[dict]) -> dict:
    """Classify our payout bank codes against the rail's own bank list.

    `remote` is `utility.wema.get_banks()["banks"]` — `[{bank_name, bank_code}]`.
    Returns `{agree, differ, ambiguous, unmatched, remote_count, ok}`, where the
    three problem lists carry `{name, ours, rail}` rows.

    We resolve recipients by `(account_number, bank_code)` in the RAIL's code
    space, but ours were seeded from a NIBSS/Paystack mirror (see seed_plans), so
    a code that differs there fails name enquiry for every account number a user
    types — and the gateway reports that as an invalid account number, never as a
    bank problem.

    A name matching more than one remote row is `ambiguous`, never resolved by
    guessing: writing the wrong code here misroutes real money.
    """
    by_name: dict[str, list] = {}
    for row in remote or []:
        if row.get("bank_code"):
            by_name.setdefault(normalize_bank_name(row.get("bank_name", "")), []).append(row)

    out = {"agree": [], "differ": [], "ambiguous": [], "unmatched": [],
           "remote_count": sum(len(v) for v in by_name.values())}
    for bank in Bank.objects.filter(active=True).order_by("name"):
        hits = by_name.get(normalize_bank_name(bank.name)) or []
        row = {"name": bank.name, "ours": bank.bank_code, "code": bank.code}
        if not hits:
            out["unmatched"].append(row)
        elif len({h["bank_code"] for h in hits}) > 1:
            out["ambiguous"].append({**row, "rail": sorted({h["bank_code"] for h in hits})})
        elif hits[0]["bank_code"] == bank.bank_code:
            out["agree"].append(row)
        else:
            out["differ"].append({**row, "rail": hits[0]["bank_code"],
                                  "rail_name": hits[0].get("bank_name", "")})
    out["ok"] = not (out["differ"] or out["ambiguous"] or out["unmatched"])
    return out


def apply_bank_codes(differ: list[dict]) -> int:
    """Write the rail's code onto the `differ` rows from compare_bank_codes()."""
    updated = 0
    for row in differ or []:
        bank = Bank.objects.filter(code=row["code"]).first()
        if bank and row.get("rail"):
            bank.bank_code = row["rail"]
            bank.save(update_fields=["bank_code"])
            updated += 1
    return updated


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

    if result.get("pending"):
        outcome = "pending"
    elif result.get("success"):
        # APIM accepting a request is not proof the recipient was paid.  Unknown
        # provider status strings are held for reconciliation; only the explicit
        # terminal-success legend can settle the ledger.
        outcome = classify_transfer_status(result.get("status"), envelope_ok=True)
    else:
        outcome = "failed"

    if outcome == "success":
        # Confirmed sent — settle now and clear the reconcile flag.
        meta = dict(txn.meta or {})
        meta.pop("reconcile", None)
        txn.meta = meta
        txn.transaction_status = Transaction.SUCCESS
        txn.save(update_fields=["transaction_status", "meta"])
    elif outcome == "pending":
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
