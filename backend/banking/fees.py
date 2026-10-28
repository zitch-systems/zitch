"""Explicit all-inclusive pricing; contract base costs are not final customer fees."""
from decimal import Decimal, InvalidOperation
from .client import config


def transfer_fee(amount):
    raw = config().get("TRANSFER_FEE_TOTAL", "")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = Decimal(str(raw))
        if not value.is_finite() or value < 0 or value != value.quantize(Decimal("0.01")):
            return None
        return value
    except (InvalidOperation, ValueError):
        return None
