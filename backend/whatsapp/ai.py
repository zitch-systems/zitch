"""LLM intent extraction for the WhatsApp channel.

The model only *proposes* a structured intent — one tool call with params. The
deterministic execution layer (router.py) validates and is the only thing that
moves money (spec hard-rule #1). Holds no payment credentials.

Mock-friendly: with no LLM_API_KEY, `extract_intent` returns None so the caller
falls back to the deterministic router. The SDK is imported lazily, so the app
runs (and tests pass) without `anthropic` installed.
"""
import logging
import re

from django.conf import settings

log = logging.getLogger("whatsapp")

SYSTEM_PROMPT = (
    "You are Zitch's transaction assistant. Convert the user's message into exactly "
    "one tool call. Interpret Nigerian shorthand for amounts (k = thousand, m = "
    "million) and number words; return amount as an integer in the major unit of the "
    "stated/implied currency (default NGN). Account, meter, smartcard and phone "
    "numbers appear as opaque tokens like num_ref_1 — copy a token verbatim into "
    "the matching field. Never invent or guess an account number, "
    "meter number, or smartcard number. If a transfer names a person without an "
    "account number, set beneficiary_ref and leave account fields null. If "
    "airtime/data has no target phone, leave phone null. If the message isn't a "
    "supported action, call clarify."
)

# Anthropic tool schemas (the spec's §6 input_schemas verbatim).
TOOLS = [
    {"name": "check_balance",
     "description": "Check the user's wallet balance.",
     "input_schema": {"type": "object",
                      "properties": {"currency": {"type": ["string", "null"],
                                                  "description": "null = all wallets"}}}},
    {"name": "transfer",
     "description": "Send money to a bank account or a saved beneficiary.",
     "input_schema": {"type": "object",
                      "properties": {
                          "amount": {"type": "integer"},
                          "currency": {"type": "string", "default": "NGN"},
                          "beneficiary_ref": {"type": ["string", "null"]},
                          "account_number": {"type": ["string", "null"]},
                          "bank_name": {"type": ["string", "null"]},
                          "narration": {"type": ["string", "null"]}},
                      "required": ["amount"]}},
    {"name": "buy_airtime",
     "description": "Buy airtime.",
     "input_schema": {"type": "object",
                      "properties": {
                          "amount": {"type": "integer"},
                          "phone": {"type": ["string", "null"]},
                          "network": {"type": ["string", "null"]}},
                      "required": ["amount"]}},
    {"name": "buy_data",
     "description": "Buy a data bundle.",
     "input_schema": {"type": "object",
                      "properties": {
                          "plan": {"type": ["string", "null"]},
                          "phone": {"type": ["string", "null"]},
                          "network": {"type": ["string", "null"]}}}},
    {"name": "pay_bill",
     "description": "Pay an electricity, cable TV, or internet bill.",
     "input_schema": {"type": "object",
                      "properties": {
                          "category": {"type": "string", "description": "electricity|cabletv|internet"},
                          "biller": {"type": ["string", "null"]},
                          "customer_id": {"type": ["string", "null"], "description": "meter or smartcard"},
                          "variation": {"type": ["string", "null"]},
                          "amount": {"type": ["integer", "null"]}},
                      "required": ["category"]}},
    {"name": "add_money",
     "description": "Show the user's dedicated Zitch account number so they can fund "
                    "(top up / add money to) their wallet by bank transfer.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "convert_currency",
     "description": "Convert between currencies.",
     "input_schema": {"type": "object",
                      "properties": {
                          "from_currency": {"type": "string"},
                          "to_currency": {"type": "string"},
                          "amount": {"type": "number"},
                          "amount_side": {"type": "string", "enum": ["sell", "buy"], "default": "sell"}},
                      "required": ["from_currency", "to_currency", "amount"]}},
    {"name": "clarify",
     "description": "The message isn't a supported action or is ambiguous.",
     "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}}},
]


def llm_available() -> bool:
    """Configured well enough to make a call — which for an OpenAI-compatible
    provider means a base URL and model too, not just a key."""
    from . import llm

    return llm.configured()


# 7+ digits, tolerating the single spaces and dashes people actually type:
# "0123 456 789" and "0123-456-789" are how a Nigerian customer writes an
# account number, and a contiguous-run-only pattern misses both — which meant
# the identifier reached the provider and the support log in clear.
_LONG_IDENTIFIER = re.compile(r"(?<![\d])\d(?:[ -]?\d){6,}(?![\d])")
_EMAIL = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
# A payment card typed the way people type them: 13-19 digits in groups joined
# by spaces or dashes. Confirmed by Luhn before removal, so an 11-digit meter
# number or a 10-digit NUBAN is never mistaken for one.
_CARD_SHAPE = re.compile(r"(?<!\d)\d{4}(?:[ -]?\d{2,6}){2,4}(?!\d)")
# Words that mark the digits near them as a secret rather than an amount:
# "my pin is 1234" vs "send 1234". Amounts and PINs share a shape; the words
# around them are the discriminator.
_SECRET_CONTEXT = re.compile(r"(?i)\b(pin|otp|password|passcode|passwd|cvv|cvc|secret)\b")
_SHORT_CODE = re.compile(r"(?<!\d)\d{3,6}(?!\d)")

TOKEN_PREFIX = "num_ref_"
_TOKEN = re.compile(rf"{TOKEN_PREFIX}\d+")


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def sanitize_for_model(text: str) -> tuple[str, dict]:
    """Split the message into what a model may see and what it may not.

    Two different fates, matching two different kinds of number:

    * Secrets — card numbers (Luhn-confirmed, however they are spaced), and any
      3-6 digit group in a message that talks about a pin/otp/cvv — are REMOVED.
      They are never tokenized, because no intent needs them: nothing the model
      could do with a PIN is something we want done.
    * Identifiers — account, meter, smartcard, phone numbers (7+ digits) — are
      replaced with opaque tokens (num_ref_1, …) and returned in `mapping` so
      the caller can put the real value back into whatever field the model chose.
      The model works on a de-identified sentence; the customer's numbers never
      leave the building.

    Returns (masked_text, mapping) where mapping is token -> raw string.
    """
    safe = str(text or "")

    def _card(m):
        digits = re.sub(r"\D", "", m.group(0))
        return "[card removed]" if 13 <= len(digits) <= 19 and _luhn_ok(digits) else m.group(0)

    safe = _CARD_SHAPE.sub(_card, safe)
    safe = _EMAIL.sub("[email removed]", safe)

    mapping: dict = {}

    def _tokenize(m):
        token = f"{TOKEN_PREFIX}{len(mapping) + 1}"
        # Store digits only: every consumer (account, meter, smartcard, phone)
        # wants the bare number, and re-hydrating "0123 456 789" would push the
        # customer's spacing into a field the bank validates strictly.
        mapping[token] = re.sub(r"\D", "", m.group(0))
        return token

    safe = _LONG_IDENTIFIER.sub(_tokenize, safe)
    if _SECRET_CONTEXT.search(safe):
        # Only now that identifiers are tokenized: whatever 3-6 digit groups
        # remain next to secret-words are PIN/OTP/CVV-shaped, not accounts.
        safe = _SHORT_CODE.sub("[code removed]", safe)
    return safe, mapping


def rehydrate_value(value, mapping: dict):
    """Swap tokens back for the real identifiers, recursively — the way out of
    the de-identified request. Unknown tokens are left as-is; the deterministic
    flow treats them as missing and asks, which is the safe failure."""
    if isinstance(value, str):
        return _TOKEN.sub(lambda m: mapping.get(m.group(0), m.group(0)), value)
    if isinstance(value, dict):
        return {k: rehydrate_value(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [rehydrate_value(v, mapping) for v in value]
    return value


def privacy_safe_text(text: str) -> str:
    """Back-compat shim: the masked text alone (used by callers that only need
    to strip, not re-hydrate)."""
    return sanitize_for_model(text)[0]


def extract_intent(text: str) -> dict | None:
    """Map a message to one tool call -> {"name", "input"}, or None to fall back
    to the deterministic router (not configured, or any error — money never
    blocks on AI).

    The provider is whatever the console has configured (Claude, OpenAI, Gemini,
    Grok, Groq, DeepSeek, Kimi, Qwen, or any OpenAI-compatible endpoint); llm.py
    owns the wire formats. The message is redacted before it leaves the building
    either way — see privacy_safe_text."""
    if not llm_available():
        return None
    from . import llm

    masked, mapping = sanitize_for_model(text)
    intent = llm.call_tools(SYSTEM_PROMPT, masked, TOOLS)
    if not intent:
        return None
    # The model's proposal contains tokens at most, never real identifiers —
    # keep that version for the log/console, and re-hydrate a separate copy for
    # dispatch so the deterministic flow gets the customer's actual numbers.
    masked_input = dict(intent.get("input") or {})
    return {"name": intent.get("name"),
            "input": rehydrate_value(masked_input, mapping),
            "masked_input": masked_input}
