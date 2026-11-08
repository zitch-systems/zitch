"""Pure decoding of retained Wema statement evidence, without transport or writes.

Preserved from the implementation archive for historical reviews and migrations.
A decoded row is not authorization to post it into the current bank ledger.
"""
from decimal import Decimal, InvalidOperation

def _naira(v) -> Decimal | None:
    """Parse an ALAT money value to Decimal, tolerating thousands separators and a
    currency symbol/code (history amounts can arrive as "1,000.00" or "₦1,000").
    Returns None only when genuinely unparseable — callers must treat None as
    'skip', never as zero."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("₦", "").replace("NGN", "").strip()
    if not s:
        return None
    try:
        amount = Decimal(s).quantize(Decimal("0.01"))
        return amount if amount.is_finite() else None
    except (TypeError, ValueError, InvalidOperation):
        return None

_TX_SETTLED = {"successfull", "successful"}

def normalize_transaction(tx: dict) -> dict:
    """Flatten one ALAT TransactionHistoryModel row to the fields reconciliation
    needs: {reference, amount_naira, is_credit, settled, status, narration, sender}.

    `referenceId` (fallback `tranId`) is the unique per-transaction key used as
    the ledger idempotency guard; `creditType == "Credit"` marks an inbound
    deposit; `settled` is False for a row the documented `status` marks Failed/
    Pending, so the funding sweep (apply_wema_credit) never credits a deposit that
    hasn't actually landed."""
    if not isinstance(tx, dict):
        return {"reference": "", "amount_naira": None, "is_credit": False,
                "settled": False, "status": "", "narration": "", "sender": ""}
    ref = str(tx.get("referenceId") or tx.get("tranId") or "").strip()
    # A Default row is fundable only when the authenticated history has the
    # independent posted-record fields in Wema's own sample: a transaction ID,
    # matching retrieval reference and resulting balance. The caller also requires
    # a successful API envelope. A receipt or an unconfirmed/incomplete row alone
    # never creates spendable Zitch funds.
    is_credit = str(tx.get("creditType") or "").strip().lower() == "credit"
    status = str(tx.get("status") or "").strip().lower()
    rrn = str(tx.get("rrn") or "").strip()
    posted_default = (
        status == "default" and is_credit
        and bool(str(tx.get("referenceId") or "").strip())
        and bool(str(tx.get("tranId") or "").strip())
        and rrn == ref
        and _naira(tx.get("balance")) is not None
    )
    return {"reference": ref, "amount_naira": _naira(tx.get("amount")),
            "is_credit": is_credit, "settled": status in _TX_SETTLED or posted_default,
            "status": status, "narration": tx.get("narration") or "",
            "sender": tx.get("sender") or tx.get("senderAccountNumber") or ""}
