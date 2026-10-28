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
from wallet.models import Transaction
from wallet.services import (
    DuplicateTransaction,
    InsufficientFunds,
    LimitExceeded,
    debit,
    merge_transaction_meta,
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

    `remote` is `banking.adapter.get_banks()["banks"]` — `[{bank_name, bank_code}]`.
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
    # Account numbers are not a unique bank identifier. Avoid a paid multi-bank
    # sweep for every keystroke; the customer selects one bank for name enquiry.
    return []


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
                   note: str = "", idempotency_key: str = "", channel: str = "",
                   expected_fee=None) -> Transaction:
    """Reserve principal and confirmed fee atomically with a single-use outbox."""
    from django.db import transaction
    from banking.payments import spending_account, bind_instruction, dispatch
    from banking.fees import transfer_fee
    from banking.client import money
    try:
        source = spending_account(user, refresh=True)
        fee = transfer_fee(amount)
        if fee is None:
            raise LimitExceeded("Transfer fees are unavailable. No money was taken.")
        if expected_fee is None or Decimal(str(expected_fee)) != fee:
            raise LimitExceeded("Review the current transfer fee before confirming this payment.")
        narration = " ".join(str(note or "").split())[:100] or f"Transfer to {name}"[:100]
        payload = {"source": source.number, "destination": account_number,
            "bank_code": bank.bank_code, "destination_name": name,
            "amount": money(amount), "fee": str(fee), "narration": narration}
        with transaction.atomic():
            txn = debit(user, amount + fee, f"Transfer to {name}",
                meta={"provider": "9psb", "account": account_number, "bank": bank.name,
                      "recipient_name": name, "note": narration, "narration": narration,
                      "principal": str(amount), "fee": str(fee), "reconcile": True, "channel": channel},
                idempotency_key=idempotency_key)
            instruction = bind_instruction(txn, "waas", "OTHER_BANKS", payload)
    except DuplicateTransaction:
        raise PayoutError("duplicate", "This request was already processed.") from None
    except InsufficientFunds:
        raise PayoutError("insufficient", "Insufficient available balance for the amount and fee.") from None
    except (LimitExceeded, ValueError, ArithmeticError) as exc:
        raise PayoutError("limit_exceeded", str(exc)) from None
    result = dispatch(instruction)
    txn.refresh_from_db()
    if txn.transaction_status == Transaction.FAILED:
        raise PayoutError("provider", result.get("message", "Transfer could not be completed."))
    row, _ = Beneficiary.objects.get_or_create(user=user, account_number=account_number,
        bank_name=bank.name, defaults={"name": name[:80], "bank_code": bank.code,
                                     "color": bank.color or "#0FA295"})
    Beneficiary.objects.filter(pk=row.pk).update(transfer_count=F("transfer_count") + 1, bank_code=bank.code)
    row.refresh_from_db()
    txn.beneficiary_id, txn.beneficiary_transfers, txn.beneficiary_saved = row.pk, row.transfer_count, row.saved
    return txn
