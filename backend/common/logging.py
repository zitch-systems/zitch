"""Defence-in-depth redaction for stdout/application log messages."""
import logging
import re


# Restrict the local part to email characters.  A broad ``[^\s@]*`` can start at
# the ``e`` in ``email=ada@example.com`` and accidentally retain the label's first
# character instead of the address's first character.
_EMAIL = re.compile(
    r"(?i)\b([a-z0-9._%+-])[a-z0-9._%+-]*@([a-z0-9.-]+\.[a-z]{2,})\b"
)
_NG_PHONE = re.compile(r"(?<!\d)(?:\+?234|0)[789]\d{9}(?!\d)")
_LABELLED_SECRET = re.compile(
    r"(?i)\b(phone|msisdn|account|account_number|nuban|bvn|nin|securityinfo|"
    r"access_token|authorization|token)=([\"']?)([^\s,;]+)"
)


def _mask_phone(match: re.Match) -> str:
    value = match.group(0)
    return f"{value[:4]}***{value[-2:]}"


def redact_log_text(value: str) -> str:
    """Redact common fintech identifiers while preserving useful event context."""
    text = str(value or "")
    text = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)
    text = _NG_PHONE.sub(_mask_phone, text)
    text = _LABELLED_SECRET.sub(lambda m: f"{m.group(1)}=[redacted]", text)
    return text


class RedactingFormatter(logging.Formatter):
    """Final log-line scrubber for messages missed by call-site masking."""

    def format(self, record):
        return redact_log_text(super().format(record))
