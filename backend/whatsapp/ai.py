"""LLM intent extraction for the WhatsApp channel.

The model only *proposes* a structured intent — one tool call with params. The
deterministic execution layer (router.py) validates and is the only thing that
moves money (spec hard-rule #1). Holds no payment credentials.

Mock-friendly: with no LLM_API_KEY, `extract_intent` returns None so the caller
falls back to the deterministic router. The SDK is imported lazily, so the app
runs (and tests pass) without `anthropic` installed.
"""
import logging

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
    return bool(settings.LLM.get("API_KEY"))


def extract_intent(text: str) -> dict | None:
    """Map a message to one tool call -> {"name", "input"}, or None to fall back
    to the deterministic router (no key, or any error — money never blocks on AI)."""
    if not llm_available():
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.LLM["API_KEY"])
        resp = client.messages.create(
            model=settings.LLM.get("MODEL") or "claude-haiku-4-5-20251001",
            max_tokens=512,
            temperature=0,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            tool_choice={"type": "any"},  # force exactly one tool call
            messages=[{"role": "user", "content": text}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                return {"name": block.name, "input": dict(block.input or {})}
        return None
    except Exception as exc:  # noqa: BLE001 — never let the AI break the channel
        log.warning("LLM intent extraction failed: %s", exc)
        return None
