"""Encryption for the WhatsApp Flows data-exchange endpoint.

WhatsApp Flows never let the PIN touch the chat: the user types it into a native,
masked field inside the Flow, and Meta delivers the submit to our endpoint under
a hybrid encryption scheme (Meta's spec) —

  * the request carries an AES-128-GCM key, itself RSA-OAEP-encrypted with OUR
    public key (which we upload to Meta once); only our private key can unwrap it;
  * the Flow payload is AES-128-GCM encrypted under that key, tag appended;
  * our reply must be AES-128-GCM encrypted with the SAME key and the IV bit-
    inverted (Meta rejects a reply encrypted with the original IV).

So a request can only be produced by Meta (it needs our public key) and only read
by us (the AES key is sealed to our private key). The PIN is plaintext ONLY inside
this process, after decryption — never in the chat, the message log, or at rest.
"""
import base64
import json
import logging
import re

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings


log = logging.getLogger("whatsapp")


class FlowDecryptError(Exception):
    """Raised when the request can't be decrypted (bad/rotated key, malformed
    body). The endpoint maps this to HTTP 421, Meta's signal to refetch our
    public key."""


def normalize_pem(raw: str) -> str:
    """Repair a PEM that lost its newlines on the way into an env var.

    A PEM is only valid with real line breaks, and every dashboard that takes
    secrets mangles them differently: some store a literal ``\\n``, some collapse
    the block onto one line, some wrap the value in quotes. The result is a key
    that looks present and correct in the UI and fails to parse — which surfaces
    as HTTP 421 and a health check that says nothing more useful than "failed".
    Rather than make that a support ticket every time, put the block back
    together.

    A key carrying PEM headers (``Proc-Type``/``DEK-Info``, on legacy encrypted
    keys) is left alone: those live between the header line and the body, and
    re-wrapping would destroy them.
    """
    pem = (raw or "").strip().strip('"').strip("'")
    pem = pem.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    match = re.match(r"(-----BEGIN [A-Z0-9 ]+-----)(.*?)(-----END [A-Z0-9 ]+-----)",
                     pem, re.DOTALL)
    if not match:
        return pem                      # not a PEM we recognise — let the loader judge
    head, body, tail = match.groups()
    if ":" in body:                     # legacy encrypted key with PEM headers
        return pem
    body = re.sub(r"\s+", "", body)
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    return "\n".join([head, *lines, tail]) + "\n"


def _private_key():
    cfg = getattr(settings, "WHATSAPP_FLOW", {}) or {}
    pem = normalize_pem(cfg.get("PRIVATE_KEY", "") or "")
    if not pem:
        # Logged, not just raised: the endpoint answers Meta with a bare 421, so
        # without a line here the only signal anyone sees is a health check that
        # failed for no stated reason.
        log.warning("wa_flow_private_key_missing — WHATSAPP_FLOW_PRIVATE_KEY is unset")
        raise FlowDecryptError("Flows private key is not configured")
    passphrase = (cfg.get("PRIVATE_KEY_PASSPHRASE", "") or "").encode() or None
    try:
        return serialization.load_pem_private_key(pem.encode(), password=passphrase)
    except (ValueError, TypeError) as exc:
        log.warning("wa_flow_private_key_invalid error=%s", exc)
        raise FlowDecryptError(f"Invalid Flows private key: {exc}") from exc


def decrypt_request(body: dict):
    """Return ``(payload_dict, aes_key, iv)`` from Meta's encrypted request body
    ``{encrypted_flow_data, encrypted_aes_key, initial_vector}`` (all base64).

    The AES key + IV are returned so the reply can be encrypted with the same
    key and the inverted IV, as the spec requires."""
    try:
        enc_flow = base64.b64decode(body["encrypted_flow_data"])
        enc_key = base64.b64decode(body["encrypted_aes_key"])
        iv = base64.b64decode(body["initial_vector"])
    except (KeyError, TypeError, ValueError, base64.binascii.Error) as exc:
        raise FlowDecryptError(f"Malformed encrypted request: {exc}") from exc

    try:
        aes_key = _private_key().decrypt(
            enc_key,
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                         algorithm=hashes.SHA256(), label=None),
        )
    except ValueError as exc:
        # Wrong/rotated key pair — Meta refetches our public key on a 421.
        raise FlowDecryptError(f"Could not unwrap AES key: {exc}") from exc

    try:
        # Meta appends the 16-byte GCM tag to the ciphertext, which is exactly
        # what AESGCM.decrypt expects (ciphertext || tag).
        plaintext = AESGCM(aes_key).decrypt(iv, enc_flow, None)
    except Exception as exc:  # InvalidTag et al.
        raise FlowDecryptError(f"Could not decrypt flow data: {exc}") from exc

    try:
        return json.loads(plaintext.decode() or "{}"), aes_key, iv
    except (ValueError, UnicodeDecodeError) as exc:
        raise FlowDecryptError(f"Decrypted body is not JSON: {exc}") from exc


def encrypt_response(response: dict, aes_key: bytes, iv: bytes) -> str:
    """Encrypt an endpoint reply for Meta: AES-128-GCM under the request's key
    with the IV bit-inverted, base64-encoded. Returned as the raw response body
    (Meta expects a base64 string, not JSON)."""
    flipped_iv = bytes(b ^ 0xFF for b in iv)
    ciphertext = AESGCM(aes_key).encrypt(flipped_iv, json.dumps(response).encode(), None)
    return base64.b64encode(ciphertext).decode()
