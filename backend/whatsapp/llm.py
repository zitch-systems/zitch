"""Provider registry and runtime configuration for the LLM intent layer.

Two wire formats cover every provider we support: Anthropic's native Messages
API, and the OpenAI chat-completions shape that OpenAI, Groq, DeepSeek, Kimi
(Moonshot), Qwen (DashScope), xAI (Grok) and Google (via its OpenAI-compatible
endpoint) all speak. So a new provider is usually a row in PROVIDERS, not code.

Configuration lives in SystemSetting so an operator can switch provider, model
or key from the console without a deploy, and falls back to the LLM_* env vars
when nothing is stored. The API key is ENCRYPTED at rest and never returned by
any endpoint — see `set_api_key` / `masked_key`.

The model only ever *proposes* an intent; router.py validates and is the only
thing that moves money. A provider swap therefore cannot change what the
platform is willing to do — only how well it understands a sentence.
"""
import base64
import hashlib
import logging

from django.conf import settings

log = logging.getLogger("whatsapp")

# wire: which request/response shape to speak.
#   "anthropic" — Messages API with `tools` + tool_use blocks (native SDK).
#   "openai"    — /chat/completions with `tools` + tool_calls (plain HTTPS).
PROVIDERS = {
    "anthropic": {
        "label": "Claude (Anthropic)",
        "wire": "anthropic",
        "base_url": "",  # SDK default
        "default_model": "claude-haiku-4-5-20251001",
        "key_url": "https://console.anthropic.com/settings/keys",
    },
    "openai": {
        "label": "OpenAI (GPT)",
        "wire": "openai",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "key_url": "https://platform.openai.com/api-keys",
    },
    "gemini": {
        "label": "Gemini (Google)",
        "wire": "openai",
        # Google publishes an OpenAI-compatible surface; using it keeps one code
        # path instead of a third wire format for a single provider.
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-2.0-flash",
        "key_url": "https://aistudio.google.com/apikey",
    },
    "xai": {
        "label": "Grok (xAI)",
        "wire": "openai",
        "base_url": "https://api.x.ai/v1",
        "default_model": "grok-2-latest",
        "key_url": "https://console.x.ai",
    },
    "groq": {
        "label": "Groq",
        "wire": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "key_url": "https://console.groq.com/keys",
    },
    "deepseek": {
        "label": "DeepSeek",
        "wire": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "key_url": "https://platform.deepseek.com/api_keys",
    },
    "moonshot": {
        "label": "Kimi (Moonshot)",
        "wire": "openai",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
        "key_url": "https://platform.moonshot.cn/console/api-keys",
    },
    "qwen": {
        "label": "Qwen (Alibaba DashScope)",
        "wire": "openai",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "key_url": "https://dashscope.console.aliyun.com/apiKey",
    },
    "custom": {
        "label": "Other (OpenAI-compatible)",
        "wire": "openai",
        "base_url": "",  # operator supplies it
        "default_model": "",
        "key_url": "",
    },
}

# SystemSetting keys.
K_PROVIDER = "llm_provider"
K_MODEL = "llm_model"
K_KEY = "llm_api_key_enc"
K_BASE_URL = "llm_base_url"


def custom_endpoint_allowed() -> bool:
    return bool((getattr(settings, "LLM", {}) or {}).get("ALLOW_CUSTOM_ENDPOINT", False))


# --------------------------------------------------------------------------- #
# base-URL validation (SSRF)
# --------------------------------------------------------------------------- #
def base_url_error(url: str) -> str | None:
    """Reject a custom endpoint the server must not be made to call.

    A configurable base URL turns the backend into an HTTP client aimed wherever
    an operator points it, and it sends real customer text plus an Authorization
    header. Aimed at a private or link-local address that becomes an SSRF probe
    from inside the deployment — cloud metadata (169.254.169.254) being the
    classic prize, since on many hosts it hands out instance credentials.

    So: HTTPS only (the key is a bearer token on the wire), and the host must
    resolve to public addresses only. Resolution is checked at save time. Since
    that cannot stop DNS rebinding after the check, production disables custom
    endpoints altogether and permits only the code-owned provider registry.
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    url = (url or "").strip()
    if not url:
        return "A base URL is required"
    if not url.startswith("https://"):
        return "The base URL must start with https:// — the provider key travels on it"
    host = (urlparse(url).hostname or "").strip()
    if not host:
        return "That base URL has no host"
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return f"Could not resolve {host}"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return (f"{host} resolves to a private or link-local address ({ip}). "
                    "The model endpoint must be a public host.")
    return None


# --------------------------------------------------------------------------- #
# key storage
# --------------------------------------------------------------------------- #
def _fernet():
    """Fernet keyed from SECRET_KEY.

    This protects the provider key against a database leak — a dump, a stolen
    backup, a read replica — which is the realistic exposure for a value an
    operator pastes into a web form. It is NOT protection against someone who
    already has the application's settings, and it does not pretend to be: with
    SECRET_KEY in hand the value is recoverable. A KMS would raise that bar and
    is the natural upgrade; the interface here (set/get) would not change.

    Rotating SECRET_KEY makes a stored key undecryptable — it reads as "not
    configured" and an operator re-enters it, which is a recoverable state and
    is why the ciphertext is never the only copy of anything that matters.
    """
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(f"llm-key:{settings.SECRET_KEY}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def set_api_key(raw: str) -> None:
    from .models import SystemSetting

    raw = (raw or "").strip()
    if not raw:
        SystemSetting.set(K_KEY, "")
        return
    SystemSetting.set(K_KEY, _fernet().encrypt(raw.encode()).decode())


def stored_api_key() -> str:
    """The decrypted stored key, or "" if absent/undecryptable. Never send this
    to a client — use `masked_key` for display."""
    from .models import SystemSetting

    blob = SystemSetting.get(K_KEY, "")
    if not blob:
        return ""
    try:
        from cryptography.fernet import InvalidToken

        try:
            return _fernet().decrypt(blob.encode()).decode()
        except InvalidToken:
            # Almost always a SECRET_KEY rotation. Log the fact, not the value.
            log.warning("llm_key_undecryptable — re-enter the provider key in the console")
            return ""
    except Exception:  # noqa: BLE001 — a broken key store must not break the channel
        log.exception("llm_key_read_failed")
        return ""


def masked_key(raw: str) -> str:
    """Last four only. Enough for an operator to tell which key is installed,
    useless to anyone who intercepts the page."""
    raw = raw or ""
    return f"••••••••{raw[-4:]}" if len(raw) >= 4 else ("••••••••" if raw else "")


# --------------------------------------------------------------------------- #
# active configuration
# --------------------------------------------------------------------------- #
def active_config() -> dict:
    """Resolve the live LLM config: stored settings first, env as the fallback,
    so an untouched deployment behaves exactly as it did before this existed."""
    from .models import SystemSetting

    env = getattr(settings, "LLM", {}) or {}
    provider = (SystemSetting.get(K_PROVIDER, "") or env.get("PROVIDER") or "anthropic").strip()
    spec = PROVIDERS.get(provider) or PROVIDERS["anthropic"]
    model = (SystemSetting.get(K_MODEL, "") or env.get("MODEL") or spec["default_model"]).strip()
    api_key = stored_api_key() or (env.get("API_KEY") or "").strip()
    base_url = (SystemSetting.get(K_BASE_URL, "") or spec["base_url"] or "").strip().rstrip("/")
    if provider == "custom" and not custom_endpoint_allowed():
        # A stale custom configuration fails closed after a production hardening
        # deploy, even before an operator revisits the settings page.
        api_key = ""
    return {
        "provider": provider if provider in PROVIDERS else "anthropic",
        "label": spec["label"],
        "wire": spec["wire"],
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "timeout": env.get("TIMEOUT", 8),
    }


def configured() -> bool:
    """True when a call could actually be made. An OpenAI-compatible provider
    also needs a base URL and a model — `custom` has neither by default, and a
    half-configured provider must read as OFF rather than fail on every message."""
    cfg = active_config()
    if not cfg["api_key"] or not cfg["model"]:
        return False
    if cfg["wire"] == "openai" and not cfg["base_url"]:
        return False
    return True


# --------------------------------------------------------------------------- #
# tool-schema translation
# --------------------------------------------------------------------------- #
def to_openai_tools(tools: list) -> list:
    """Anthropic tool schemas -> OpenAI function-tool schemas. One source of
    truth for the tool list (ai.TOOLS); this is a pure shape change."""
    return [
        {"type": "function",
         "function": {"name": t["name"],
                      "description": t.get("description", ""),
                      "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
        for t in tools
    ]


# --------------------------------------------------------------------------- #
# the call
# --------------------------------------------------------------------------- #
def call_tools(system: str, user_text: str, tools: list, cfg: dict | None = None) -> dict | None:
    """Ask the configured provider for exactly one tool call.

    Returns {"name", "input"} or None. Never raises: the caller falls back to the
    deterministic router, because money must not depend on a third party being up.
    """
    cfg = cfg or active_config()
    if not cfg["api_key"]:
        return None
    try:
        if cfg["wire"] == "anthropic":
            return _call_anthropic(system, user_text, tools, cfg)
        return _call_openai(system, user_text, tools, cfg)
    except Exception as exc:  # noqa: BLE001
        # Log the type and provider, never the key or the customer's message.
        log.warning("llm_intent_failed provider=%s error_type=%s", cfg["provider"], type(exc).__name__)
        return None


def _call_anthropic(system: str, user_text: str, tools: list, cfg: dict) -> dict | None:
    import anthropic

    # Bound the call: this runs INSIDE the synchronous Meta webhook, so an
    # unbounded client (SDK default ~10 min with retries) would stall the handler
    # and trigger Meta retries → webhook disablement. Fail fast.
    kwargs = {"api_key": cfg["api_key"], "timeout": cfg["timeout"], "max_retries": 0}
    if cfg["base_url"]:
        kwargs["base_url"] = cfg["base_url"]
    client = anthropic.Anthropic(**kwargs)
    resp = client.messages.create(
        model=cfg["model"], max_tokens=512, temperature=0,
        system=system, tools=tools, tool_choice={"type": "any"},
        messages=[{"role": "user", "content": user_text}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use":
            return {"name": block.name, "input": dict(block.input or {})}
    return None


def _call_openai(system: str, user_text: str, tools: list, cfg: dict) -> dict | None:
    import json

    import requests

    # Bandit cannot infer that the validated numeric cfg value below is a
    # requests timeout; the call is intentionally bounded.
    resp = requests.post(  # nosec B113
        f"{cfg['base_url']}/chat/completions",
        headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
        json={
            "model": cfg["model"],
            "temperature": 0,
            "max_tokens": 512,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user_text}],
            "tools": to_openai_tools(tools),
            # "required" is the portable spelling of Anthropic's tool_choice "any".
            # Providers that ignore it still usually emit a tool call; if one does
            # not, we return None and the deterministic router takes over.
            "tool_choice": "required",
        },
        timeout=cfg["timeout"],
    )
    if resp.status_code >= 400:
        log.warning("llm_http_error provider=%s status=%s", cfg["provider"], resp.status_code)
        return None
    choices = (resp.json() or {}).get("choices") or []
    if not choices:
        return None
    calls = (choices[0].get("message") or {}).get("tool_calls") or []
    if not calls:
        return None
    fn = calls[0].get("function") or {}
    name = fn.get("name")
    if not name:
        return None
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except (TypeError, ValueError):
        args = {}
    return {"name": name, "input": args if isinstance(args, dict) else {}}


def test_connection(cfg: dict | None = None) -> dict:
    """Live round-trip against the configured provider, for the console's Test
    button. Returns {ok, message} — an operator should find out a key is wrong
    here, not from customers getting dumb replies."""
    from .ai import SYSTEM_PROMPT, TOOLS

    cfg = cfg or active_config()
    if not cfg["api_key"]:
        return {"ok": False, "message": "No API key configured."}
    if cfg["wire"] == "openai" and not cfg["base_url"]:
        return {"ok": False, "message": "This provider needs a base URL."}
    if not cfg["model"]:
        return {"ok": False, "message": "No model configured."}
    intent = call_tools(SYSTEM_PROMPT, "what is my balance", TOOLS, cfg)
    if intent is None:
        return {"ok": False, "message": f"{cfg['label']} did not return a usable response — "
                                        "check the key, model name and base URL."}
    return {"ok": True, "message": f"{cfg['label']} responded — understood “what is my balance” "
                                   f"as {intent.get('name')}."}
