"""Canonical validation for Nigerian mobile destinations."""
import re


def normalize_nigerian_mobile(value) -> str | None:
    """Return an 11-digit local mobile number, or ``None`` when invalid.

    Customers may use local, 234, or +234 notation and familiar visual
    separators. Other characters and international destinations are rejected
    rather than removed, so a typo cannot silently become a different number.
    """
    if not isinstance(value, str):
        return None
    phone = re.sub(r"[\s()\-]", "", value)
    if phone.startswith("+234"):
        phone = "0" + phone[4:]
    elif phone.startswith("234"):
        phone = "0" + phone[3:]
    return phone if re.fullmatch(r"0[789][0-9]{9}", phone) else None
