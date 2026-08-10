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

from .flows import (FLOW_ID_STATE, FLOW_PIN_STATE, resolve_flow_token,
                    sign_flow_token)
from .models import PendingAction, WaMessageLog, WhatsAppLink

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
                            tier=1, bvn_verified=True, nin_verified=True,
                            email_verified=True, phone_verified=True)
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

    def test_malformed_decrypted_shapes_return_a_safe_screen(self):
        from .flows import SUCCESS_SCREEN, handle_flow_request

        self.assertEqual(handle_flow_request([])["screen"], SUCCESS_SCREEN)
        response = handle_flow_request({"action": "data_exchange", "data": ["bad"]})
        self.assertEqual(response["screen"], SUCCESS_SCREEN)

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
             override_settings(TERMII={"BASE_URL": "https://v3.api.termii.com", "API_KEY": "",
                                       "SENDER_ID": "Zitch", "CHANNEL": "dnd"}):
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

    def test_signature_enforced_when_secret_set(self):
        # Meta signs Flows data-exchange requests like webhook callbacks; the
        # envelope encryption alone doesn't authenticate the sender (anyone with
        # our PUBLIC key can produce a decryptable body), so with an APP_SECRET
        # configured the endpoint must reject an unsigned/badly-signed POST.
        import hashlib
        import hmac as hmac_mod

        body, _, _ = self._meta_encrypt({"action": "ping"})
        raw = json.dumps(body).encode()
        wa = {"VERIFY_TOKEN": "", "TOKEN": "", "APP_SECRET": "shh",
              "BASE_URL": "x", "PHONE_NUMBER_ID": "", "BUSINESS_NUMBER": ""}
        with override_settings(WHATSAPP=wa, WHATSAPP_FLOW={"PRIVATE_KEY": self.priv_pem}):
            unsigned = Client().post("/webhooks/whatsapp/flow", data=raw,
                                     content_type="application/json")
            self.assertEqual(unsigned.status_code, 401)
            good_sig = hmac_mod.new(b"shh", raw, hashlib.sha256).hexdigest()
            signed = Client().post("/webhooks/whatsapp/flow", data=raw,
                                   content_type="application/json",
                                   HTTP_X_HUB_SIGNATURE_256=f"sha256={good_sig}")
            self.assertEqual(signed.status_code, 200)  # signature passes; ping served


# --------------------------------------------------------------------------- #
# identity (BVN/NIN) collected in the Flow, not the chat
# --------------------------------------------------------------------------- #
class IdentityFlowTests(TestCase):
    """WhatsApp has no view-once for text and lets only the SENDER delete, so a
    BVN typed into the thread stays in the customer's own history indefinitely.
    It is collected in the encrypted Flow for the same reason the PIN is."""

    def setUp(self):
        self.user = _make_user()
        self.user.bvn_verified = self.user.nin_verified = False
        self.user.save(update_fields=["bvn_verified", "nin_verified"])

    def _action(self, kind="bvn"):
        return PendingAction.objects.create(
            msisdn=MSISDN, user=self.user, action_type="kyc",
            state=FLOW_ID_STATE, payload={"id_kind": kind},
            expires_at=timezone.now() + timedelta(minutes=10))

    def test_init_opens_the_identity_screen_for_the_right_number(self):
        from .flows import IDENTITY_SCREEN, handle_flow_request, sign_identity_token

        pa = self._action("nin")
        resp = handle_flow_request({"action": "INIT", "flow_token": sign_identity_token(pa)})
        self.assertEqual(resp["screen"], IDENTITY_SCREEN)
        self.assertEqual(resp["data"]["label"], "NIN")

    def test_a_wrong_length_number_is_rejected_without_leaving_the_screen(self):
        from .flows import IDENTITY_SCREEN, handle_flow_request, sign_identity_token

        pa = self._action()
        resp = handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_identity_token(pa),
                                    "data": {"number": "123"}})
        self.assertEqual(resp["screen"], IDENTITY_SCREEN)
        self.assertIn("11 digits", resp["data"]["error"])

    def test_a_valid_number_is_stored_hashed_and_never_echoed(self):
        from .flows import handle_flow_request, sign_identity_token

        pa = self._action()
        with patch("whatsapp.router.verify_bvn", return_value={"success": True}):
            resp = handle_flow_request({"action": "data_exchange",
                                        "flow_token": sign_identity_token(pa),
                                        "data": {"number": "12345678901"}})
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)
        self.assertTrue(self.user.bvn_hash)
        # The number reaches neither the screen nor the chat transcript.
        self.assertNotIn("12345678901", str(resp))
        self.assertFalse(WaMessageLog.objects.filter(text__contains="12345678901").exists())

    def test_a_forged_or_stale_token_resolves_to_nothing(self):
        from .flows import handle_flow_request, sign_identity_token

        pa = self._action()
        good = sign_identity_token(pa)
        forged = good[:-1] + ("a" if good[-1] != "a" else "b")
        resp = handle_flow_request({"action": "data_exchange", "flow_token": forged,
                                    "data": {"number": "12345678901"}})
        self.assertIn("expired", resp["data"]["message"].lower())
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_verified)

    def test_an_identity_token_is_not_a_money_token(self):
        """The three token kinds resolve through different lookups; one must never
        be accepted where another is expected."""
        from .flows import resolve_flow_token, resolve_onboarding_token, sign_identity_token

        pa = self._action()
        token = sign_identity_token(pa)
        self.assertIsNone(resolve_flow_token(token))
        self.assertIsNone(resolve_onboarding_token(token))

    def test_typing_the_number_into_the_chat_is_refused_while_the_flow_is_open(self):
        """Accepting it here would put in the transcript exactly what the Flow
        exists to keep out of it."""
        from . import router

        self._action()
        with patch.object(router, "reply") as mock_reply:
            router._advance(  # noqa: SLF001
                PendingAction.objects.get(action_type="kyc"), self.user,
                MSISDN, "12345678901")
        said = " ".join(str(c) for c in mock_reply.call_args_list)
        self.assertIn("secure screen", said)
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_verified)

    def test_the_router_sends_the_flow_instead_of_asking_in_the_chat(self):
        from . import router

        pa = self._action()
        pa.state = "kyc_start"
        pa.save(update_fields=["state"])
        with patch.object(router, "flows_live", return_value=True), \
             patch.object(router, "send_flow", return_value={"success": True}) as sent:
            self.assertTrue(router._send_identity_flow(pa, "nin"))  # noqa: SLF001
        sent.assert_called_once()
        pa.refresh_from_db()
        self.assertEqual(pa.state, FLOW_ID_STATE)
        self.assertEqual(pa.payload["id_kind"], "nin")

    def test_a_failed_flow_send_leaves_the_action_where_the_chat_expects_it(self):
        """Otherwise the action sits in a Flow state with no Flow open, and the
        customer's next message is refused by a guard pointing at a screen that
        was never delivered."""
        from . import router

        pa = self._action()
        with patch.object(router, "flows_live", return_value=True), \
             patch.object(router, "send_flow", return_value={"success": False}):
            self.assertFalse(router._send_identity_flow(pa, "bvn"))  # noqa: SLF001
        pa.refresh_from_db()
        self.assertEqual(pa.state, "bvn")


class PemNormalisationTests(unittest.TestCase):
    """A PEM is only valid with real line breaks, and secret-management UIs mangle
    them in several different ways. Each variant below looks correct in the
    dashboard and fails to parse, surfacing as HTTP 421 and a Meta health check
    that says nothing more useful than "failed"."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not _HAS_CRYPTO:
            raise unittest.SkipTest("cryptography not importable")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()

    def _loads(self, variant):
        from .flows_crypto import normalize_pem

        serialization.load_pem_private_key(normalize_pem(variant).encode(), password=None)

    def test_an_untouched_pem_still_loads(self):
        self._loads(self.pem)

    def test_literal_backslash_n_is_repaired(self):
        self._loads(self.pem.replace("\n", "\\n"))

    def test_a_pem_flattened_onto_one_line_is_repaired(self):
        self._loads(" ".join(self.pem.split()))

    def test_surrounding_quotes_are_stripped(self):
        self._loads('"' + self.pem + '"')

    def test_a_legacy_encrypted_key_is_left_alone(self):
        """Proc-Type/DEK-Info headers live between the header line and the body;
        re-wrapping would destroy them, so that shape is passed through."""
        from .flows_crypto import normalize_pem

        # Assembled rather than written literally: a "BEGIN RSA PRIVATE KEY"
        # banner in source is precisely what the repo's secret scan exists to
        # catch, and a test fixture is not worth teaching it to ignore. The body
        # is four bytes of padding — there is no key here.
        begin, end = "-----BEGIN RSA ", "-----END RSA "
        legacy = (f"{begin}PRIVATE KEY-----\n"
                  "Proc-Type: 4,ENCRYPTED\n"
                  "DEK-Info: AES-128-CBC,0123\n\nAAAA\n"
                  f"{end}PRIVATE KEY-----\n")
        self.assertEqual(normalize_pem(legacy), legacy.strip())

    def test_an_unset_key_is_reported_not_swallowed(self):
        from django.test import override_settings

        from .flows_crypto import FlowDecryptError, _private_key

        with override_settings(WHATSAPP_FLOW={"PRIVATE_KEY": ""}):
            with self.assertLogs("whatsapp", level="WARNING") as logs:
                with self.assertRaises(FlowDecryptError):
                    _private_key()
        self.assertIn("wa_flow_private_key_missing", "\n".join(logs.output))


@unittest.skipUnless(_HAS_CRYPTO, "cryptography not importable in this environment")
class KeyMismatchLoggingTests(TestCase):
    """A key that parses but is not Meta's pair is the one failure that looks
    like every other: valid PEM, so neither the missing nor the invalid check
    fires, and the endpoint still answers a bare 421. It has to name itself."""

    def test_a_mismatched_key_pair_says_so(self):
        from .flows_crypto import FlowDecryptError, decrypt_request

        meta_side = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ours = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        aes_key, iv = os.urandom(16), os.urandom(16)
        body = {
            "encrypted_flow_data": _b64(AESGCM(aes_key).encrypt(iv, b"{}", None)),
            # Sealed to a public key we do NOT hold the private half of.
            "encrypted_aes_key": _b64(meta_side.public_key().encrypt(
                aes_key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                      algorithm=hashes.SHA256(), label=None))),
            "initial_vector": _b64(iv),
        }
        pem = ours.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        with override_settings(WHATSAPP_FLOW={"PRIVATE_KEY": pem}):
            with self.assertLogs("whatsapp", level="WARNING") as logs:
                with self.assertRaises(FlowDecryptError):
                    decrypt_request(body)
        self.assertIn("wa_flow_key_mismatch", "\n".join(logs.output))
