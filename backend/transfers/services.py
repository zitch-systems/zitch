"""Bank payout orchestration — shared by the HTTP API and the WhatsApp channel.

Keeps a single implementation of the money movement (debit -> provider payout ->
settle/refund -> save beneficiary) so both entry points behave identically.
Callers do their own auth / PIN / tier-limit checks and name-enquiry first, then
hand a resolved account name to `execute_payout`.
"""
import logging
from decimal import Decimal

from django.db.models import F

from utility.providers import payout_send
from utility.wema import classify_transfer_status
from wallet.models import Transaction
from wallet.services import (
    DuplicateTransaction,
    InsufficientFunds,
    LimitExceeded,
    debit,
    settle_or_refund,
)

from .models import Bank, Beneficiary

log = logging.getLogger("zitch")

# Generic words that carry no identity: dropping them lets "Moniepoint MFB" match
# "Moniepoint Microfinance Bank" while keeping "First Bank" != "Fidelity Bank".
# The joiners matter as much as the nouns: without them "First Bank" never meets
# "First Bank of Nigeria".
# "merchant" belongs with "microfinance": a licence class, not an identity — the
# rail may still carry Nova Bank under its former "Nova Merchant Bank" name. Words
# that DO distinguish real banks stay (e.g. "trust": Titan Trust != PremiumTrust).
_NAME_NOISE = {"bank", "banks", "plc", "ltd", "limited", "nigeria", "nigerian", "ng",
               "mfb", "microfinance", "merchant", "finance", "psb", "payment", "service",
               "services", "digital", "company", "co", "of", "for", "and", "the"}

# Banks whose legal name on the rail is nothing like the trade name we show in the
# picker — no amount of token normalization gets "GTBank" to "Guaranty Trust". Each
# group maps every spelling we have seen to one id; a bank is matched when both
# sides land on the same id. Keys are already normalized (tokens sorted), so
# "United Bank for Africa" appears here as "africa united".
#
# Deliberately explicit rather than fuzzy: these decide which bank a payout is
# routed to, and a plausible-but-wrong guess sends real money to the wrong bank.
_ALIAS_GROUPS = (
    ("gtb", ("gtbank", "gt", "guaranty trust", "guaranty")),
    ("uba", ("uba", "africa united")),
    ("fcmb", ("fcmb", "city first monument")),
    ("scb", ("scb", "chartered standard")),
    ("vfd", ("v vfd", "vfd", "v")),
    # The rail writes this one as a single token, "9PAYMENT SERVICE BANK".
    ("9psb", ("9psb", "9", "9payment")),
    ("momo", ("momo mtn", "momo")),
    ("smartcash", ("airtel smartcash", "smartcash")),
    ("citi", ("citi", "citibank")),
    # Our picker says "Mint MFB"; the rail lists the licensed entity behind it,
    # "MINT-FINEX MFB" (090281). Confirmed against the rail's own leftover list —
    # it carries no other Mint.
    ("mint", ("mint", "finex mint", "mintfinex", "mintyn")),
)
_ALIASES = {key: canon for canon, keys in _ALIAS_GROUPS for key in keys}


def normalize_bank_name(name: str) -> str:
    """Identity-bearing tokens of a bank name, sorted and joined."""
    import re

    words = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    kept = [w for w in words if w not in _NAME_NOISE]
    return " ".join(sorted(kept or words))


def bank_name_keys(name: str) -> tuple:
    """The keys `name` can be matched on, most-precise first.

    1. the normalized token key            ("premium trust")
    2. the same with spaces squashed       ("premiumtrust") — one side writes the
       trade name as one word, the other as two
    3. an explicit alias id, for trade names that share no words at all
    """
    key = normalize_bank_name(name)
    squashed = key.replace(" ", "")
    alias = _ALIASES.get(key) or _ALIASES.get(squashed)
    return (key, squashed, f"alias:{alias}" if alias else None)


def _suggest(name: str, leftovers: list[dict], limit: int = 5) -> list:
    """Rail rows that plausibly ARE `name`, for a human to confirm.

    Shares a distinctive word with our name, or contains it as a prefix of one
    ("Mint" -> "MINT-FINEX"). Purely advisory — nothing is applied off a
    suggestion, because "plausibly the same bank" is not good enough to route
    money on.
    """
    ours = set(normalize_bank_name(name).split())
    if not ours:
        return []
    scored = []
    for row in leftovers:
        theirs = set(normalize_bank_name(row["name"]).split())
        shared = ours & theirs
        if not shared:
            # A word of ours that starts one of theirs (or vice versa) still counts,
            # so a hyphenated or run-together legal name is not missed.
            shared = {a for a in ours for b in theirs
                      if len(a) > 2 and (b.startswith(a) or a.startswith(b))}
        if shared:
            scored.append((len(shared), -len(theirs), row))
    scored.sort(key=lambda s: (-s[0], s[1], s[2]["name"]))
    return [r for _, _, r in scored[:limit]]


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
    guessing: writing the wrong code here misroutes real money. `rail_unmatched`
    lists the rail's own leftover names, which is what a human needs to close a
    gap by hand (or to extend _ALIAS_GROUPS).

    Matching runs key by key (see bank_name_keys) and stops at the first key that
    hits, so an exact name match is never overridden by a looser one. Each matched
    row records `via` — exact / squashed / alias — so every rewrite is auditable.
    """
    rows = [r for r in (remote or []) if r.get("bank_code")]
    indexes: list[dict] = [{}, {}, {}]
    for row in rows:
        for i, key in enumerate(bank_name_keys(row.get("bank_name", ""))):
            if key:
                indexes[i].setdefault(key, []).append(row)

    labels = ("exact", "squashed", "alias")
    out = {"agree": [], "differ": [], "ambiguous": [], "unmatched": [],
           "rail_unmatched": [], "remote_count": len(rows)}
    claimed = set()
    for bank in Bank.objects.filter(active=True).order_by("name"):
        row = {"name": bank.name, "ours": bank.bank_code, "code": bank.code}
        hits, via = [], ""
        for i, key in enumerate(bank_name_keys(bank.name)):
            if key and indexes[i].get(key):
                hits, via = indexes[i][key], labels[i]
                break
        if not hits:
            out["unmatched"].append(row)
            continue
        claimed.update(id(h) for h in hits)
        codes = {h["bank_code"] for h in hits}
        if len(codes) > 1:
            out["ambiguous"].append({**row, "rail": sorted(codes), "via": via})
        elif hits[0]["bank_code"] == bank.bank_code:
            out["agree"].append(row)
        else:
            out["differ"].append({**row, "rail": hits[0]["bank_code"],
                                  "rail_name": hits[0].get("bank_name", ""), "via": via})

    # What the rail carries that we never matched. Reporting ALL of it was useless
    # in practice: the live rail returns ~1000 rows, nearly all microfinance banks
    # we do not list, so the answer for the two banks that mattered was buried in a
    # wall of names. Each unmatched bank instead carries its own shortlist — the
    # rail rows sharing one of its distinctive words — which is what a human needs
    # to close the gap (e.g. "Mint MFB" -> "MINT-FINEX MFB = 090281").
    leftovers = sorted(
        ({"name": r.get("bank_name", ""), "code": r["bank_code"]}
         for r in rows if id(r) not in claimed),
        key=lambda r: r["name"])
    for row in out["unmatched"]:
        row["suggestions"] = _suggest(row["name"], leftovers)
    out["rail_unmatched_count"] = len(leftovers)
    out["rail_unmatched"] = leftovers
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
                   note: str = "", idempotency_key: str = "", channel: str = "") -> Transaction:
    """Debit the wallet, send the payout, settle/refund, and save the beneficiary.

    The caller must already have verified the PIN + tier limits and resolved
    `name` via the provider's name-enquiry. Raises PayoutError on a duplicate,
    insufficient funds, or a provider failure (wallet auto-refunded).

    Returns the ledger transaction — **which is not always Successful**. The
    "pending" branch below returns a row still marked Pending, both for an
    accepted-but-queued transfer and for the ambiguous send-timeout where Wema
    may or may not have paid. CHECK `transaction_status` before telling a
    customer their money arrived: this docstring used to promise a settled row,
    and the WhatsApp path believed it and issued receipts for transfers that had
    not gone through.

    `channel`, when set to "whatsapp", is stamped onto the ledger row's meta so
    the post-save alert knows the customer already saw a receipt and a balance
    line in that same chat and does not need a second "Debit alert" message.
    """
    # A NUBAN minted while the rail was mocked exists nowhere at the bank, but it is
    # still on the wallet once live keys are set — and it is what we send as the
    # payout's sourceAccountNumber. The rail's enquiry then fails and blames "the
    # account number", which reads as the RECIPIENT's. Refuse before the debit:
    # otherwise every attempt debits, fails at the rail and refunds, leaving a trail
    # of reversals and a user watching money leave and come back.
    from utility.providers import payout_live
    from wallet.services import is_demo_account

    wallet = getattr(user, "wallet", None)
    if payout_live() and wallet is not None and is_demo_account(wallet):
        raise PayoutError(
            "source_unusable",
            "Your Zitch account number was issued in test mode and can't send money. "
            "Contact support to have it reissued — your balance is safe.")

    # No NUBAN of their own AND no shared pool to fall back on: there is no account
    # to debit, so payout_send would refuse — but only AFTER the debit, and with
    # "Payouts are temporarily unavailable, please try again shortly". That reads as
    # a passing outage and invites exactly the one thing that cannot work: waiting.
    # The account is missing until someone finishes setting it up. Say so, and say it
    # before taking the money.
    from django.conf import settings

    if (payout_live() and not getattr(wallet, "account_number", "")
            and not (settings.WEMA.get("SOURCE_ACCOUNT") or "").strip()):
        log.warning("payout_no_source user=%s (no wallet NUBAN, WEMA_SOURCE_ACCOUNT unset)",
                    getattr(user, "id", "?"))
        raise PayoutError(
            "source_missing",
            "Your Zitch account number isn't set up yet, so there's nothing to send from. "
            "Finish account setup, then try again — your balance is safe.")

    narration = " ".join(str(note or "").split())[:60] or f"Transfer to {name}"

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
            meta={"account": account_number, "bank": bank.name,
                  "recipient_name": name, "note": narration, "narration": narration,
                  "reconcile": True, "channel": channel},
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
    result = payout_send(amount, txn.reference, narration,
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
        # Confirmed sent — settle through the locked state machine. A concurrent
        # callback/reconciler may already have resolved the row; never overwrite
        # its terminal state from this request's stale in-memory object.
        settled = settle_or_refund(txn, {"success": True, "status": result.get("status", "")})
        if settled != "success":
            log.critical("payout_state_conflict ref=%s provider=success ledger=%s",
                         txn.reference, settled)
            raise PayoutError(
                "state_conflict",
                "The bank response conflicted with the ledger. Your transaction is under review.",
            )
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
        #
        # Record WHY on the row first, the way settle_or_refund does for VAS. The
        # reason was previously raised to the user and logged, and nowhere else —
        # so after the fact, with no shell and no log access, a failed payout was
        # indistinguishable from any other failed payout. It is the single most
        # useful field when a rail starts rejecting transfers.
        settled = settle_or_refund(
            txn,
            {"success": False,
             "message": result.get("message", "") or "Transfer failed",
             "status": result.get("status", "")},
        )
        # A verified callback/reconciler can win before this stale failure
        # response returns. Its success is authoritative; refunding would create
        # free money after the recipient was paid.
        if settled != "success":
            raise PayoutError("provider", result.get("message", "Transfer failed"))

    # Record the recipient for next time. This is a RECENT, never a saved
    # beneficiary: `saved` and `nickname` are absent from `defaults` on purpose,
    # so a repeat payout to somebody the customer has already saved and named
    # cannot quietly undo either. Nothing in the payout path decides what belongs
    # in a customer's address book; only the customer does.
    row, _ = Beneficiary.objects.get_or_create(
        user=user, account_number=account_number, bank_name=bank.name,
        defaults={"name": name, "bank_code": bank.bank_code, "color": bank.color or "#0FA295"},
    )
    # Automatic recipient memory is separate from the explicit address book:
    # every successful/accepted transfer increments the private frequency counter.
    # After transfer #3 it becomes eligible for account-number search/autofill;
    # after transfer #51 it is promoted to Beneficiary (saved=True). This is
    # idempotent for retries because the payout itself is idempotent.
    Beneficiary.objects.filter(pk=row.pk).update(transfer_count=F("transfer_count") + 1)
    row.refresh_from_db(fields=["transfer_count", "saved", "name", "bank_code", "color", "nickname"])
    if row.transfer_count > 50 and not row.saved:
        row.saved = True
        row.save(update_fields=["saved"])
    txn.refresh_from_db()
    # Carried on the transaction so the caller can offer "save this recipient"
    # against the exact row we just wrote, rather than re-deriving it from an
    # account number — which would cost a second name enquiry to answer a
    # question we already know the answer to.
    txn.beneficiary_id = row.id
    return txn
