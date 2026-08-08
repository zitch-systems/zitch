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
    "stated/implied currency (default NGN). Never invent or guess an account number, "
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


_LONG_IDENTIFIER = re.compile(r"(?<!\d)\d{7,}(?!\d)")
_EMAIL = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")


def privacy_safe_text(text: str) -> str:
    """Remove direct identifiers before optional third-party intent extraction.

    The model only needs to choose a flow. Account, phone, meter and smartcard
    numbers are collected/validated by deterministic code after routing.
    """
    safe = _EMAIL.sub("[email redacted]", str(text or ""))
    return _LONG_IDENTIFIER.sub("[identifier redacted]", safe)


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

    return llm.call_tools(SYSTEM_PROMPT, privacy_safe_text(text), TOOLS)
