"""Tests for the WhatsApp Flows secure PIN pad.

Three layers: the encryption envelope (flows_crypto), the business logic
(flows.handle_flow_request — PIN verify + execute), and the router arming /
chat-guard behaviour. The envelope tests are skipped if `cryptography` can't be
imported in this environment; the logic tests run everywhere (they bypass the
envelope and call the handler with a decrypted payload, exactly as the endpoint
does after decryption).
"""
import base64
import json
import os
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from transfers.models import Bank
from wallet.services import credit, get_or_create_wallet

from .flows import FLOW_PIN_STATE, resolve_flow_token, sign_flow_token
from .models import PendingAction, WhatsAppLink

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _HAS_CRYPTO = True
except Exception:  # pragma: no cover - environment without a built cryptography
    _HAS_CRYPTO = False

import unittest

User = get_user_model()
MSISDN = "2348011112222"


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _make_user(balance="50000"):
    u = User.objects.create(username="08010000001", phone="08010000001",
                            email="ada@zitch.test", first_name="Ada", last_name="Eze",
                            tier=1, bvn_verified=True)
    u.set_transaction_pin("1234")
    u.save()
    get_or_create_wallet(u)
    credit(u, Decimal(balance), "Seed")
    WhatsAppLink.objects.create(user=u, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)
    return u


def _transfer_action(user, state=FLOW_PIN_STATE):
    return PendingAction.objects.create(
        user=user, msisdn=MSISDN, action_type="transfer", state=state,
        payload={"amount": "5000", "account": "0123456789", "bank_code": "058",
                 "bank_name": "GTBank", "name": "JOHN DOE",
                 "flow_summary": "Send ₦5,000.00 to JOHN DOE · GTBank 0123456789",
                 "pin_attempts": 0},
        expires_at=timezone.now() + timedelta(minutes=5),
    )


# --------------------------------------------------------------------------- #
# envelope (RSA-OAEP + AES-GCM)
# --------------------------------------------------------------------------- #
@unittest.skipUnless(_HAS_CRYPTO, "cryptography not importable in this environment")
class FlowsCryptoTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.priv_pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        cls.pub = key.public_key()

    def _meta_encrypt(self, payload: dict):
        """Replicate what Meta does when it posts to the endpoint."""
        aes_key, iv = os.urandom(16), os.urandom(16)
        ct = AESGCM(aes_key).encrypt(iv, json.dumps(payload).encode(), None)
        enc_key = self.pub.encrypt(
            aes_key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                  algorithm=hashes.SHA256(), label=None))
        return {"encrypted_flow_data": _b64(ct), "encrypted_aes_key": _b64(enc_key),
                "initial_vector": _b64(iv)}, aes_key, iv

    def test_decrypt_then_encrypt_roundtrip(self):
        from .flows_crypto import decrypt_request, encrypt_response

        with override_settings(WHATSAPP_FLOW={"PRIVATE_KEY": self.priv_pem}):
            body, aes_key, iv = self._meta_encrypt({"action": "ping"})
            payload, got_key, got_iv = decrypt_request(body)
            self.assertEqual(payload, {"action": "ping"})
            self.assertEqual(got_key, aes_key)

            # The reply is AES-GCM under the same key with the IV bit-inverted —
            # decrypt it Meta-side to prove Meta could read it.
            b64_response = encrypt_response({"data": {"status": "active"}}, got_key, got_iv)
            flipped = bytes(x ^ 0xFF for x in iv)
            clear = AESGCM(aes_key).decrypt(flipped, base64.b64decode(b64_response), None)
            self.assertEqual(json.loads(clear), {"data": {"status": "active"}})

    def test_wrong_key_raises_decrypt_error(self):
        from .flows_crypto import FlowDecryptError, decrypt_request

        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_pem = other.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        body, _, _ = self._meta_encrypt({"action": "ping"})
        with override_settings(WHATSAPP_FLOW={"PRIVATE_KEY": other_pem}):
            with self.assertRaises(FlowDecryptError):
                decrypt_request(body)


# --------------------------------------------------------------------------- #
# business logic (decrypted payload -> response), envelope-independent
# --------------------------------------------------------------------------- #
class FlowsHandlerTests(TestCase):
    def setUp(self):
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#e30613", active=True)
        self.user = _make_user()

    def test_ping_is_health_check(self):
        from .flows import handle_flow_request

        self.assertEqual(handle_flow_request({"action": "ping"}), {"data": {"status": "active"}})

    def test_init_returns_pin_screen_with_summary(self):
        from .flows import PIN_SCREEN, handle_flow_request

        pa = _transfer_action(self.user)
        resp = handle_flow_request({"action": "INIT", "flow_token": sign_flow_token(pa)})
        self.assertEqual(resp["screen"], PIN_SCREEN)
        self.assertIn("JOHN DOE", resp["data"]["summary"])

    def test_wrong_pin_reprompts_and_does_not_debit(self):
        from .flows import PIN_SCREEN, handle_flow_request

        pa = _transfer_action(self.user)
        before = get_or_create_wallet(self.user).balance
        resp = handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_flow_token(pa),
                                    "data": {"pin": "0000"}})
        self.assertEqual(resp["screen"], PIN_SCREEN)
        self.assertTrue(resp["data"]["error"])
        self.assertEqual(get_or_create_wallet(self.user).balance, before)   # nothing moved
        self.assertTrue(PendingAction.objects.filter(id=pa.id).exists())    # still pending

    def test_correct_pin_executes_and_completes(self):
        from .flows import SUCCESS_SCREEN, handle_flow_request

        pa = _transfer_action(self.user)
        before = get_or_create_wallet(self.user).balance
        resp = handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_flow_token(pa),
                                    "data": {"pin": "1234"}})
        self.assertEqual(resp["screen"], SUCCESS_SCREEN)
        self.assertIn("5,000", resp["data"]["message"])
        self.assertEqual(get_or_create_wallet(self.user).balance, before - Decimal("5000"))
        self.assertFalse(PendingAction.objects.filter(id=pa.id).exists())   # consumed

    def test_forged_token_is_rejected(self):
        from .flows import SUCCESS_SCREEN, handle_flow_request

        pa = _transfer_action(self.user)
        before = get_or_create_wallet(self.user).balance
        forged = f"{pa.id}.deadbeefdeadbeefdeadbe"        # right id, wrong signature
        resp = handle_flow_request({"action": "data_exchange", "flow_token": forged,
                                    "data": {"pin": "1234"}})
        self.assertEqual(resp["screen"], SUCCESS_SCREEN)   # terminal "expired" screen
        self.assertEqual(get_or_create_wallet(self.user).balance, before)   # no debit
        self.assertTrue(PendingAction.objects.filter(id=pa.id).exists())

    def test_resolve_token_rejects_wrong_state(self):
        pa = _transfer_action(self.user, state="pin")      # not a Flow-armed action
        self.assertIsNone(resolve_flow_token(sign_flow_token(pa)))


# --------------------------------------------------------------------------- #
# router arming + chat guard
# --------------------------------------------------------------------------- #
class FlowArmingTests(TestCase):
    def setUp(self):
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#e30613", active=True)
        self.user = _make_user()

    def test_arm_confirm_sends_flow_when_live(self):
        from . import router

        pa = _transfer_action(self.user, state="bank")
        with patch.object(router, "flows_live", return_value=True), \
             patch.object(router, "send_flow", return_value={"success": True}) as mock_send:
            router._arm_confirm(pa, self.user)
        mock_send.assert_called_once()
        pa.refresh_from_db()
        self.assertEqual(pa.state, FLOW_PIN_STATE)
        self.assertIn("flow_summary", pa.payload)
        # No SMS/PIN otp armed when the Flow is used.
        self.assertNotIn("otp_hash", pa.payload)

    def test_arm_confirm_falls_back_when_flow_send_fails(self):
        from . import router

        pa = _transfer_action(self.user, state="bank")
        with patch.object(router, "flows_live", return_value=True), \
             patch.object(router, "send_flow", return_value={"success": False}), \
             override_settings(SENDCHAMP={"API_KEY": ""}):
            router._arm_confirm(pa, self.user)
        pa.refresh_from_db()
        self.assertEqual(pa.state, "pin")   # fell back to the chat PIN gate

    def test_chat_message_during_flow_is_nudged_not_executed(self):
        from .router import handle_inbound

        pa = _transfer_action(self.user)
        before = get_or_create_wallet(self.user).balance
        handle_inbound(MSISDN, "1234")   # user types the PIN in chat by mistake
        pa.refresh_from_db()
        self.assertEqual(pa.state, FLOW_PIN_STATE)                       # unchanged
        self.assertEqual(get_or_create_wallet(self.user).balance, before)  # no debit
        last = router_last_reply()
        self.assertIn("secure screen", last.lower())


def router_last_reply():
    from .models import WaMessageLog
    row = WaMessageLog.objects.filter(msisdn=MSISDN, direction=WaMessageLog.OUT).order_by("-created").first()
    return row.text if row else ""


# --------------------------------------------------------------------------- #
# endpoint (full crypto + HTTP)
# --------------------------------------------------------------------------- #
@unittest.skipUnless(_HAS_CRYPTO, "cryptography not importable in this environment")
class FlowEndpointTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.priv_pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        cls.pub = key.public_key()

    def _meta_encrypt(self, payload: dict):
        aes_key, iv = os.urandom(16), os.urandom(16)
        ct = AESGCM(aes_key).encrypt(iv, json.dumps(payload).encode(), None)
        enc_key = self.pub.encrypt(
            aes_key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                  algorithm=hashes.SHA256(), label=None))
        return {"encrypted_flow_data": _b64(ct), "encrypted_aes_key": _b64(enc_key),
                "initial_vector": _b64(iv)}, aes_key, iv

    def test_ping_returns_encrypted_active(self):
        body, aes_key, iv = self._meta_encrypt({"action": "ping"})
        with override_settings(WHATSAPP_FLOW={"PRIVATE_KEY": self.priv_pem}):
            res = Client().post("/webhooks/whatsapp/flow", data=json.dumps(body),
                                content_type="application/json")
        self.assertEqual(res.status_code, 200)
        flipped = bytes(x ^ 0xFF for x in iv)
        clear = AESGCM(aes_key).decrypt(flipped, base64.b64decode(res.content), None)
        self.assertEqual(json.loads(clear), {"data": {"status": "active"}})

    def test_undecryptable_body_returns_421(self):
        with override_settings(WHATSAPP_FLOW={"PRIVATE_KEY": self.priv_pem}):
            res = Client().post("/webhooks/whatsapp/flow",
                                data=json.dumps({"encrypted_flow_data": "AA==",
                                                 "encrypted_aes_key": "AA==",
                                                 "initial_vector": "AA=="}),
                                content_type="application/json")
        self.assertEqual(res.status_code, 421)
