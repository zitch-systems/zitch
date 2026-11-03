"""Bank payout orchestration — shared by the HTTP API and the WhatsApp channel.

Keeps a single implementation of the money movement (debit -> provider payout ->
settle/refund -> save beneficiary) so both entry points behave identically.
Callers do their own auth / PIN / tier-limit checks and name-enquiry first, then
hand a resolved account name to `execute_payout`.
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor, wait
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

from .models import Beneficiary

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



_NAME_ENQUIRIES = ThreadPoolExecutor(max_workers=6, thread_name_prefix="bank-name")


def nuban_candidate(number, bank_code):
    """CBN's check digit narrows DMB candidates; it never verifies a recipient.

    The 2018 CBN standard pads a DMB code to six digits and applies repeating
    weights 3,7,3 to that code plus the nine-digit account serial. Other NIP
    routing codes cannot be assumed to be CBN institution identifiers.
    """
    if not re.fullmatch(r"[0-9]{10}", number or ""):
        return False
    if not re.fullmatch(r"(?:[0-9]{3}|000[0-9]{3}|9[0-9]{5})", bank_code or ""):
        return None
    digits = bank_code.zfill(6) + number[:9]
    check = (10 - sum(int(value) * (3, 7, 3)[i % 3] for i, value in enumerate(digits)) % 10) % 10
    return check == int(number[-1])


def detect_account_banks(account_number: str, *, user=None) -> list[dict]:
    from django.core.cache import cache
    from banking.accounts import require_enabled
    from banking.adapter import resolve_account
    from banking.client import Client, ProviderError, configured
    from banking.payments import digest
    from .models import Bank

    if not re.fullmatch(r"[0-9]{10}", account_number or ""):
        return []
    if user is not None:
        try:
            require_enabled(user)
        except ValueError:
            return []
    banks = list(Bank.objects.filter(active=True).exclude(bank_code="").order_by("-popular", "name"))
    by_slug = {bank.code: bank for bank in banks}
    # A customer's own completed payment history is the fastest source of a
    # bank hint. The payout still performs its own fresh recipient enquiry.
    if user is not None:
        known = []
        for saved in Beneficiary.objects.filter(user=user, account_number=account_number):
            bank = by_slug.get(saved.bank_code)
            if bank and saved.name:
                known.append({"bank": bank.code, "bank_name": bank.name, "name": saved.name})
        if known:
            return known
    candidates = [bank for bank in banks if nuban_candidate(account_number, bank.bank_code) is True]
    # Popular OFIs are manually routable even when NIP and CBN identifiers differ.
    candidates += [bank for bank in banks if bank.popular and nuban_candidate(account_number, bank.bank_code) is None][:4]
    candidates = candidates[:8]
    if not candidates or not configured():
        return []
    client = Client(timeout=(1, 3))
    key = "bank-detection:" + digest([client._cache_key(), account_number,
                                      [(bank.code, bank.bank_code) for bank in candidates]])
    cached = cache.get(key)
    if cached is not None:
        return cached
    if not cache.add(key + ":lease", True, timeout=12):
        return []
    try:
        client.token()  # Warm the shared token before starting concurrent reads.
        pending = {_NAME_ENQUIRIES.submit(resolve_account, account_number, bank.bank_code,
                                         timeout=(1, 3)): bank for bank in candidates}
        done, remaining = wait(pending, timeout=5)
        results = []
        for future in done:
            bank = pending[future]
            try:
                resolved = future.result()
            except Exception:
                continue
            if not isinstance(resolved, dict):
                continue
            if resolved.get("success") is True and isinstance(resolved.get("name"), str) and resolved["name"].strip():
                results.append({"bank": bank.code, "bank_name": bank.name, "name": resolved["name"].strip()})
        for future in remaining:
            future.cancel()
        results.sort(key=lambda row: next(i for i, bank in enumerate(candidates) if bank.code == row["bank"]))
        cache.set(key, results, timeout=60 if results else 5)
        return results
    except ProviderError:
        return []
    finally:
        cache.delete(key + ":lease")


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
    if row.transfer_count > 50 and not row.saved:
        row.saved = True
        row.save(update_fields=["saved"])
    txn.beneficiary_id, txn.beneficiary_transfers, txn.beneficiary_saved = row.pk, row.transfer_count, row.saved
    return txn
