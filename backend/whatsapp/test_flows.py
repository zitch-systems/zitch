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
                 "flow_fields": {"amount": "₦5,000.00", "recipient": "To JOHN DOE",
                                 "details": "GTBank · 0123456789"},
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

    def test_init_returns_pin_screen_with_amount_recipient_and_bank(self):
        from .flows import PIN_SCREEN, handle_flow_request

        pa = _transfer_action(self.user)
        resp = handle_flow_request({"action": "INIT", "flow_token": sign_flow_token(pa)})
        self.assertEqual(resp["screen"], PIN_SCREEN)
        self.assertIn("5,000", resp["data"]["amount"])
        self.assertIn("JOHN DOE", resp["data"]["recipient"])
        # The bank is half of where the money goes — it must be on the screen.
        self.assertIn("GTBank", resp["data"]["details"])
        self.assertIn("0123456789", resp["data"]["details"])

    def test_pin_screen_falls_back_to_the_one_line_summary(self):
        """Actions queued before flow_fields existed still render every field."""
        from .flows import PIN_SCREEN, handle_flow_request

        pa = _transfer_action(self.user)
        pa.payload.pop("flow_fields")
        pa.save(update_fields=["payload"])
        resp = handle_flow_request({"action": "INIT", "flow_token": sign_flow_token(pa)})
        self.assertEqual(resp["screen"], PIN_SCREEN)
        self.assertIn("JOHN DOE", resp["data"]["amount"])
        self.assertEqual(resp["data"]["recipient"], "")
        self.assertEqual(resp["data"]["details"], "")

    def test_wrong_pin_reprompts_and_does_not_debit(self):
        # PIN_RETRY, not PIN_SCREEN: WhatsApp keeps form state across a
        # same-screen re-render, so re-asking on PIN_SCREEN came back with the
        # rejected PIN still in the box. See PIN_RETRY.
        from .flows import PIN_RETRY, handle_flow_request

        pa = _transfer_action(self.user)
        before = get_or_create_wallet(self.user).balance
        resp = handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_flow_token(pa),
                                    "data": {"pin": "0000"}})
        self.assertEqual(resp["screen"], PIN_RETRY)
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

    def test_a_transaction_takes_one_pin_entry_and_never_sees_the_confirm_screen(self):
        """The re-enter pair exists to catch a typo while CREATING a PIN — where a
        typo locks the customer out of their own money. On a transaction the PIN
        already exists and is verified server-side, so a second entry would be
        pure friction; this pins the requirement so the confirm screen can never
        leak into the payment path."""
        from .flows import (PIN_CONFIRM, PIN_RETRY, PIN_SCREEN, SUCCESS_SCREEN,
                            handle_flow_request)

        pa = _transfer_action(self.user)
        token = sign_flow_token(pa)
        opened = handle_flow_request({"action": "INIT", "flow_token": token})
        self.assertEqual(opened["screen"], PIN_SCREEN)

        done = handle_flow_request({"action": "data_exchange", "flow_token": token,
                                    "data": {"pin": "1234"}})
        self.assertEqual(done["screen"], SUCCESS_SCREEN)          # one entry, executed
        self.assertNotEqual(opened["screen"], PIN_CONFIRM)

        # And a WRONG pin re-asks on an empty payment screen — never the confirm
        # pair, which would read as "re-enter to confirm" on a failed payment.
        pa2 = _transfer_action(self.user)
        wrong = handle_flow_request({"action": "data_exchange",
                                     "flow_token": sign_flow_token(pa2),
                                     "data": {"pin": "0000"}})
        self.assertEqual(wrong["screen"], PIN_RETRY)
        self.assertNotEqual(wrong["screen"], PIN_CONFIRM)

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
@override_settings(WHATSAPP_PROCESS_INLINE=False)
class FlowExecutionOffMetasClockTests(TestCase):
    """Meta gives a data-exchange 10 seconds. Executing a transfer inside one
    means a name enquiry, a payout to the bank rail, a rendered receipt, a media
    upload and two Graph sends, in sequence — routinely longer than that, and the
    customer is shown "Couldn't load content. Try again later." for a payment
    that went through.

    So the endpoint answers as soon as the PIN is verified and the payment runs
    in the durable queue, where every chat-confirmed payment has always run.
    These tests run with inline processing OFF, which is production's setting —
    the rest of the suite runs inline, so without this class the queued path
    would never be exercised.
    """

    def setUp(self):
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#e30613", active=True)
        self.user = _make_user()

    def submit_pin(self, pa, pin="1234"):
        from .flows import handle_flow_request

        return handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_flow_token(pa),
                                    "data": {"pin": pin}})

    def jobs_for(self, pa):
        return WaMessageLog.objects.filter(wa_message_id=f"flowexec-{pa.pk}")

    def test_the_endpoint_answers_without_touching_the_bank_rail(self):
        # The regression guard: no payout may happen while Meta is waiting.
        from whatsapp import router

        pa = _transfer_action(self.user)
        before = get_or_create_wallet(self.user).balance
        with patch.object(router, "execute_payout") as payout:
            resp = self.submit_pin(pa)
        payout.assert_not_called()
        self.assertEqual(get_or_create_wallet(self.user).balance, before)
        # Answered, but NOT on the terminal screen: the payment is queued, not
        # settled, and a Flow that closes itself is a statement that the job is
        # done. See test_a_pending_payment_holds_the_panel_open below.
        self.assertNotEqual(resp["screen"], "SUCCESS")

    def test_a_pending_payment_holds_the_panel_open(self):
        """Only a settled success may close the panel by itself.

        A payment still with the rail closed the Flow on "Pending", which left
        the customer looking at their own chat thread trying to work out whether
        their money had moved. The outcome now stays on the panel they were
        already looking at, and they dismiss it when they have read it.
        """
        from .flows import PIN_RETRY

        resp = self.submit_pin(_transfer_action(self.user))
        self.assertEqual(resp["screen"], PIN_RETRY)
        self.assertIn("Confirmed", resp["data"]["error"])
        self.assertIn("receipt", resp["data"]["error"].lower())

    def test_a_held_panel_still_answers_a_complete_screen(self):
        """Holding the panel open is only safe if the reply is renderable: a
        missing declared property is what WhatsApp shows as "Couldn't load
        content. Try again later."."""
        import json
        import os

        resp = self.submit_pin(_transfer_action(self.user))
        path = os.path.join(os.path.dirname(__file__), "flow_assets", "pin_flow.json")
        with open(path, encoding="utf-8") as fh:
            declared = {s["id"]: set((s.get("data") or {}).keys())
                        for s in json.load(fh)["screens"]}
        self.assertEqual(set(resp["data"].keys()), declared[resp["screen"]])

    def test_the_authorised_payment_is_queued_and_then_paid(self):
        from .jobs import process_inbound_message

        pa = _transfer_action(self.user)
        before = get_or_create_wallet(self.user).balance
        self.submit_pin(pa)

        job = self.jobs_for(pa).get()                       # durable, one row
        pa.refresh_from_db()
        self.assertEqual(pa.state, "executing")
        self.assertGreater(pa.expires_at, timezone.now() + timedelta(minutes=5))

        self.assertEqual(process_inbound_message(job.pk), "processed")
        self.assertEqual(get_or_create_wallet(self.user).balance, before - Decimal("5000"))
        self.assertFalse(PendingAction.objects.filter(id=pa.id).exists())   # consumed
        receipts = [r.text for r in WaMessageLog.objects.filter(
            msisdn=MSISDN, direction=WaMessageLog.OUT)]
        self.assertTrue(any("Transfer receipt" in t for t in receipts))

    def test_confirming_twice_queues_one_payment(self):
        # Meta showed an error and the customer tapped Confirm again. That must
        # not become two transfers, and must not become two jobs.
        pa = _transfer_action(self.user)
        self.submit_pin(pa)
        self.submit_pin(pa)
        self.assertEqual(self.jobs_for(pa).count(), 1)

    def test_a_replayed_job_pays_nothing_a_second_time(self):
        from .jobs import process_inbound_message

        pa = _transfer_action(self.user)
        before = get_or_create_wallet(self.user).balance
        self.submit_pin(pa)
        job = self.jobs_for(pa).get()
        process_inbound_message(job.pk)
        paid = get_or_create_wallet(self.user).balance
        self.assertEqual(paid, before - Decimal("5000"))

        # A finished job has its payload wiped, so re-running it cannot reach the
        # money path at all — it dead-letters instead of paying again.
        WaMessageLog.objects.filter(pk=job.pk).update(processed_at=None, processing_started_at=None)
        self.assertEqual(process_inbound_message(job.pk), "dead_letter")
        self.assertEqual(get_or_create_wallet(self.user).balance, paid)

    def test_executing_an_action_that_already_ran_is_a_no_op(self):
        # The second line of defence, for a job that outlived its action by any
        # route: no row, nothing to pay, no exception to retry on.
        from .jobs import _execute_authorised_action

        pa = _transfer_action(self.user)
        before = get_or_create_wallet(self.user).balance
        stale_id, user_id = pa.pk, self.user.pk
        pa.delete()
        _execute_authorised_action(stale_id, user_id)
        self.assertEqual(get_or_create_wallet(self.user).balance, before)

    def test_the_confirm_window_does_not_kill_a_payment_already_authorised(self):
        # The 2-minute clock measures how long the customer has to CONFIRM. Once
        # they have, a slow rail must not turn into a dropped payment.
        from .jobs import process_inbound_message

        pa = _transfer_action(self.user)
        before = get_or_create_wallet(self.user).balance
        self.submit_pin(pa)
        job = self.jobs_for(pa).get()
        PendingAction.objects.filter(pk=pa.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1))

        process_inbound_message(job.pk)
        self.assertEqual(get_or_create_wallet(self.user).balance, before - Decimal("5000"))

    def test_a_settling_payment_is_never_announced_as_not_charged(self):
        # The timeout notice promises "nothing was charged". That promise cannot
        # be made about a payment whose money may already be moving, and the row
        # is the only record left to reconcile it against.
        from .router import _announce_timeout

        pa = _transfer_action(self.user)
        self.submit_pin(pa)
        PendingAction.objects.filter(pk=pa.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1))

        self.assertFalse(_announce_timeout(MSISDN))
        self.assertTrue(PendingAction.objects.filter(pk=pa.pk).exists())

    def test_a_message_while_it_settles_does_not_cancel_it(self):
        # Every other in-flow branch can end in _clear_actions; clearing THIS row
        # would delete a payment the customer was told was on its way.
        from .router import handle_inbound

        pa = _transfer_action(self.user)          # _make_user already linked MSISDN
        self.submit_pin(pa)
        handle_inbound(MSISDN, "balance")
        self.assertTrue(PendingAction.objects.filter(pk=pa.pk).exists())
        last = WaMessageLog.objects.filter(
            msisdn=MSISDN, direction=WaMessageLog.OUT).order_by("-created").first()
        self.assertIn("going through", last.text)


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

    def test_oversized_flow_envelope_is_rejected_before_crypto(self):
        with override_settings(WHATSAPP_FLOW={"PRIVATE_KEY": self.priv_pem}):
            res = Client().post(
                "/webhooks/whatsapp/flow", data=b"x" * (1024 * 1024 + 1),
                content_type="application/json")
        self.assertEqual(res.status_code, 413)

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

    def test_a_wrong_length_number_is_rejected_onto_an_empty_retry_screen(self):
        # IDENTITY_RETRY, not a same-screen re-render: the field is MASKED, so
        # digits left in the box cannot even be read to correct. A typo costs
        # no lookup attempt — length is not a verdict.
        from .flows import IDENTITY_RETRY, handle_flow_request, sign_identity_token

        pa = self._action()
        resp = handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_identity_token(pa),
                                    "data": {"number": "123"}})
        self.assertEqual(resp["screen"], IDENTITY_RETRY)
        self.assertIn("11 digits", resp["data"]["error"])
        pa.refresh_from_db()
        self.assertFalse(pa.payload.get("id_bad_attempts"))   # no attempt burned

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

    def test_typing_the_number_into_the_chat_is_processed_not_refused(self):
        """This used to assert a refusal, on the reasoning that reading it would
        put in the transcript what the Flow exists to keep out. It is already in
        the transcript by then — refusing does not un-send it, it only adds a
        dead end on top of the exposure. So the number is processed, and the
        protections that DO still help are the ones asserted here: it never
        reaches the message log in clear, and the reply says how to delete it.
        """
        from . import router

        self._action()
        with patch.object(router, "reply") as mock_reply, \
             patch.object(router, "_kyc_submit_identity") as submit:
            router._advance(  # noqa: SLF001
                PendingAction.objects.get(action_type="kyc"), self.user,
                MSISDN, "12345678901")
        submit.assert_called_once()
        self.assertIn("delete your message",
                      " ".join(str(c) for c in mock_reply.call_args_list).lower())
        self.assertFalse(WaMessageLog.objects.filter(text__contains="12345678901").exists())

    def test_an_open_identity_flow_masks_a_number_typed_into_the_chat(self):
        """The masking gate the webhook consults. It covered the chat-fallback
        states only, so with a Flow actually open the number it was meant to
        protect went into the log in clear — the case that now happens most,
        since a typed number is answered rather than refused."""
        from . import router

        self._action()
        self.assertTrue(router.is_awaiting_bvn(MSISDN))

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


class EmailFlowTests(TestCase):
    """Email rides the same encrypted Flow as BVN and NIN. The address is entered
    there and the 6-digit code comes back there — the code is a bearer credential
    for ten minutes, and a thread keeps it far longer than that."""

    def setUp(self):
        self.user = _make_user()
        self.user.email, self.user.email_verified = "", False
        self.user.save(update_fields=["email", "email_verified"])

    def _action(self, step="address", **payload):
        return PendingAction.objects.create(
            msisdn=MSISDN, user=self.user, action_type="kyc",
            state=FLOW_ID_STATE, payload={"id_kind": "email", "id_step": step, **payload},
            expires_at=timezone.now() + timedelta(minutes=10))

    def _submit(self, pa, value):
        from .flows import handle_flow_request, sign_identity_token

        return handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_identity_token(pa),
                                    "data": {"number": value}})

    def test_init_opens_the_address_screen(self):
        from .flows import EMAIL_SCREEN, handle_flow_request, sign_identity_token

        pa = self._action()
        resp = handle_flow_request({"action": "INIT", "flow_token": sign_identity_token(pa)})
        self.assertEqual(resp["screen"], EMAIL_SCREEN)
        self.assertEqual(resp["data"]["label"], "Email address")

    def test_init_opens_the_masked_code_screen_once_the_address_is_known(self):
        from .flows import CODE_SCREEN, handle_flow_request, sign_identity_token

        self.user.email = "ada@example.com"
        self.user.save(update_fields=["email"])
        pa = self._action("code")
        resp = handle_flow_request({"action": "INIT", "flow_token": sign_identity_token(pa)})
        self.assertEqual(resp["screen"], CODE_SCREEN)     # the 6-digit root
        self.assertEqual(resp["data"]["label"], "Email code")
        self.assertIn("ada@example.com", resp["data"]["summary"])

    def test_a_malformed_address_reprompts_without_saving(self):
        from .flows import EMAIL_SCREEN

        resp = self._submit(self._action(), "not-an-email")
        self.assertEqual(resp["screen"], EMAIL_SCREEN)
        self.assertTrue(resp["data"]["error"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "")

    def test_an_address_owned_by_someone_else_is_refused(self):
        from .flows import EMAIL_SCREEN

        User.objects.create(username="08010000002", phone="08010000002",
                            email="taken@example.com")
        resp = self._submit(self._action(), "TAKEN@example.com")
        self.assertEqual(resp["screen"], EMAIL_SCREEN)
        self.assertIn("another Zitch account", resp["data"]["error"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "")

    @patch("whatsapp.router.send_email", return_value={"success": True})
    @patch("whatsapp.router.email_live", return_value=True)
    def test_a_good_address_mails_a_code_and_moves_to_the_masked_screen(self, _live, mail):
        from .flows import IDENTITY_CHAIN

        pa = self._action()
        resp = self._submit(pa, "Ada@Example.com")
        self.assertEqual(resp["screen"], IDENTITY_CHAIN)   # next page, same session
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "ada@example.com")
        self.assertFalse(self.user.email_verified)          # the code still has to come back
        pa.refresh_from_db()
        self.assertEqual(pa.payload["id_step"], "code")
        self.assertTrue(pa.payload["code_hash"])
        mailed = mail.call_args.args[2]
        self.assertNotIn(mailed.split()[-1], str(resp))     # the code is never echoed to the screen

    @patch("whatsapp.router.send_email", return_value={"success": True})
    @patch("whatsapp.router.email_live", return_value=True)
    def test_the_right_code_verifies_the_email_and_never_reaches_the_chat(self, _live, mail):
        pa = self._action()
        self._submit(pa, "ada@example.com")
        code = mail.call_args.args[2].split()[-1]
        pa.refresh_from_db()
        resp = self._submit(pa, code)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)
        self.assertNotIn(code, str(resp))
        self.assertFalse(WaMessageLog.objects.filter(text__contains=code).exists())

    @patch("whatsapp.router.send_email", return_value={"success": True})
    @patch("whatsapp.router.email_live", return_value=True)
    def test_a_wrong_code_reprompts_and_the_third_one_ends_the_attempt(self, _live, _mail):
        # CODE_RETRY, not a same-screen re-render: the masked box must never
        # come back holding the code that just failed.
        from .flows import CODE_RETRY, SUCCESS_SCREEN

        pa = self._action()
        self._submit(pa, "ada@example.com")
        for _ in range(2):
            pa.refresh_from_db()
            resp = self._submit(pa, "000000")
            self.assertEqual(resp["screen"], CODE_RETRY)
            self.assertTrue(resp["data"]["error"])
        pa.refresh_from_db()
        resp = self._submit(pa, "000000")
        self.assertEqual(resp["screen"], SUCCESS_SCREEN)
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)

    @patch("whatsapp.router.send_email", return_value={"success": True})
    @patch("whatsapp.router.email_live", return_value=True)
    def test_a_used_code_is_burnt_so_it_cannot_be_replayed(self, _live, mail):
        # NIN still outstanding, so the ladder moves on rather than clearing the
        # action — which is what lets us look at what it left behind.
        self.user.nin_verified = False
        self.user.save(update_fields=["nin_verified"])
        pa = self._action()
        self._submit(pa, "ada@example.com")
        code = mail.call_args.args[2].split()[-1]
        pa.refresh_from_db()
        self._submit(pa, code)
        pa.refresh_from_db()
        self.assertEqual(pa.payload.get("code_hash"), "")

    def test_the_router_sends_the_email_flow_instead_of_asking_in_the_chat(self):
        from . import router

        pa = self._action()
        with patch.object(router, "flows_live", return_value=True), \
             patch.object(router, "send_flow", return_value={"success": True}) as sent:
            self.assertTrue(router._send_email_flow(pa, "address"))  # noqa: SLF001
        sent.assert_called_once()
        pa.refresh_from_db()
        self.assertEqual(pa.state, FLOW_ID_STATE)
        self.assertEqual(pa.payload["id_kind"], "email")

    def test_a_failed_send_falls_back_to_the_chat_state(self):
        from . import router

        pa = self._action("code")
        with patch.object(router, "flows_live", return_value=True), \
             patch.object(router, "send_flow", return_value={"success": False}):
            self.assertFalse(router._send_email_flow(pa, "code"))  # noqa: SLF001
        pa.refresh_from_db()
        self.assertEqual(pa.state, "email")

    def test_typing_the_code_into_the_chat_is_processed_not_refused(self):
        """Same correction as the identity number: the code is in the thread the
        moment it is sent, so refusing to read it protects nothing and strands
        the customer. It goes to the SAME checker the Flow submit uses."""
        from . import router

        pa = self._action("code")
        with patch.object(router, "reply"), \
             patch.object(router, "_advance_kyc") as advance:
            router._advance(pa, self.user, MSISDN, "123456")  # noqa: SLF001
        advance.assert_called_once_with(pa, self.user, MSISDN, "123456")
        pa.refresh_from_db()
        self.assertEqual(pa.state, "email")   # the chat step for the code

    @patch("whatsapp.router.send_email", return_value={"success": True})
    @patch("whatsapp.router.email_live", return_value=True)
    def test_resend_is_the_one_word_the_chat_still_acts_on(self, _live, mail):
        """An expired code with no way to ask for another strands the customer
        inside a Flow they can no longer complete."""
        from . import router

        self.user.email = "ada@example.com"
        self.user.save(update_fields=["email"])
        pa = self._action("code")
        with patch.object(router, "flows_live", return_value=True), \
             patch.object(router, "send_flow", return_value={"success": True}):
            router._advance(pa, self.user, MSISDN, "resend")  # noqa: SLF001
        mail.assert_called_once()


class PublishedFlowJsonTests(unittest.TestCase):
    """`pin_flow.json` is validated by Meta at publish time, not by us at import
    time, so a change that parses perfectly here can still be rejected in
    WhatsApp Manager — and until it is published, every Flow send falls back to
    the chat. These assert the parts of Meta's contract we have actually been
    caught by."""

    @classmethod
    def setUpClass(cls):
        import pathlib

        path = pathlib.Path(__file__).parent / "flow_assets" / "pin_flow.json"
        cls.doc = json.loads(path.read_text())
        cls.order = [s["id"] for s in cls.doc["screens"]]

    def test_every_route_points_at_a_screen_that_exists(self):
        for source, targets in self.doc["routing_model"].items():
            self.assertIn(source, self.order)
            for target in targets:
                self.assertIn(target, self.order, f"{source} routes to unknown {target}")

    def test_routes_only_ever_go_forward(self):
        """Meta rejects a backward route outright: declaring both directions of
        a pair fails the whole document, which is how EMAIL_SCREEN ->
        IDENTITY_SCREEN first shipped broken. Forward means later in `screens`."""
        for source, targets in self.doc["routing_model"].items():
            for target in targets:
                self.assertLess(
                    self.order.index(source), self.order.index(target),
                    f"{source} -> {target} is a backward route; Meta will reject the Flow")

    def test_the_screens_the_endpoint_returns_are_all_published(self):
        from .flows import (EMAIL_SCREEN, IDENTITY_SCREEN, PIN_CONFIRM, PIN_SCREEN,
                            SUCCESS_SCREEN)

        for screen in (EMAIL_SCREEN, IDENTITY_SCREEN, PIN_CONFIRM, PIN_SCREEN, SUCCESS_SCREEN):
            self.assertIn(screen, self.order)

    def test_every_screen_opens_with_the_zitch_mark(self):
        """Flows cannot fetch a remote image, so the logo is base64 inlined and a
        careless edit drops it silently. It is also the only brand anchor on the
        screen where someone types their PIN — worth asserting, not assuming."""
        for screen in self.doc["screens"]:
            first = screen["layout"]["children"][0]
            self.assertEqual(first["type"], "Image", f"{screen['id']} has no logo")
            self.assertEqual(first["alt-text"], "Zitch")
            decoded = base64.b64decode(first["src"], validate=True)
            self.assertTrue(decoded.startswith(b"\x89PNG"), f"{screen['id']} logo is not a PNG")

    def test_every_field_the_endpoint_sends_is_declared_on_its_screen(self):
        """A screen renders only what it declares. An undeclared key is not a
        blank line — it fails the exchange, mid-payment."""
        from .flows import (_email_screen, _identity_screen,  # noqa: SLF001
                            _pin_screen, _success_screen)

        declared = {s["id"]: set(s["data"]) for s in self.doc["screens"]}
        from .flows import _confirm_pin_screen  # noqa: SLF001

        for built in (_pin_screen({"amount": "a", "recipient": "b", "details": "c"}, "e"),
                      _confirm_pin_screen("e"),
                      _identity_screen("bvn", error="e"),
                      _email_screen(error="e"),
                      _success_screen("done")):
            self.assertEqual(set(built["data"]), declared[built["screen"]],
                             f"{built['screen']} data keys drifted from the published JSON")


class SendPayloadsMatchThePublishedScreensTests(TestCase):
    """The endpoint's RESPONSES are already pinned to the published schema; these
    pin the SENDS. The signup PIN send kept the retired "summary" key after
    PIN_SCREEN moved to {amount, recipient, details, error} — an undeclared
    property, which Meta rejects — so every signup PIN send failed from the
    moment the new Flow was published, and signup silently fell down its
    fallback rungs. Nothing server-side could see it: the schema lives at Meta.
    """

    @classmethod
    def setUpClass(cls):
        import json as jsonlib
        import pathlib as pl

        super().setUpClass()
        doc = jsonlib.loads((pl.Path(__file__).parent / "flow_assets" / "pin_flow.json").read_text())
        cls.declared = {sc["id"]: set(sc["data"]) for sc in doc["screens"]}

    def setUp(self):
        self.user = _make_user()
        self.pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer", state="amount",
            payload={"amount": "5000", "account": "0123456789", "bank_code": "058",
                     "bank_name": "GTBank", "name": "JOHN DOE", "pin_attempts": 0},
            expires_at=timezone.now() + timedelta(minutes=5))

    def _sent(self, fn, *args, **kwargs):
        from whatsapp import router

        calls = []

        def capture(msisdn, token, **kw):
            calls.append(kw)
            return {"success": True}

        with patch.object(router, "flows_live", return_value=True), \
             patch.object(router, "send_flow", side_effect=capture), \
             patch.object(router, "reply"):
            fn(*args, **kwargs)
        self.assertTrue(calls, "no Flow message was sent")
        return calls[-1]

    def _assert_matches(self, kw):
        screen, data = kw["screen"], kw["screen_data"]
        self.assertEqual(set(data), self.declared[screen],
                         f"{screen} send keys drifted from the published JSON")

    def test_the_transfer_confirm(self):
        from whatsapp import router

        self._assert_matches(self._sent(router._send_pin_flow, self.pa, self.user))  # noqa: SLF001

    def test_the_transfer_form(self):
        from transfers.models import Bank
        from whatsapp import router

        Bank.objects.get_or_create(code="gtb", defaults={"name": "GTBank", "bank_code": "058",
                                                         "color": "#e30613", "active": True})
        self._assert_matches(self._sent(router._start_transfer, self.user, MSISDN))  # noqa: SLF001

    def test_the_signup_form(self):
        from whatsapp import router
        from whatsapp.models import WaOnboarding

        def start():
            router._start_onboarding("2348099990002")  # noqa: SLF001

        self._assert_matches(self._sent(start))

    def test_the_signup_pin(self):
        from whatsapp import router
        from whatsapp.models import WaOnboarding

        ob = WaOnboarding.objects.create(
            msisdn="2348099990000", step="pin",
            payload={"first_name": "Ada", "last_name": "Eze", "email": "a@b.test"},
            expires_at=timezone.now() + timedelta(minutes=15))
        self._assert_matches(self._sent(router._arm_onboarding_pin, ob, ob.msisdn))  # noqa: SLF001

    def test_the_pin_reset(self):
        from whatsapp import router

        self._assert_matches(self._sent(router._start_pin_reset, self.user, MSISDN))  # noqa: SLF001

    def test_the_identity_and_account_otp_screens(self):
        from whatsapp import router

        self.pa.action_type = "kyc"
        self.pa.save(update_fields=["action_type"])
        self._assert_matches(self._sent(router._send_identity_flow, self.pa, "bvn"))  # noqa: SLF001
        self._assert_matches(self._sent(router._send_account_otp_flow, self.pa))      # noqa: SLF001

    def test_the_email_screens(self):
        from whatsapp import router

        self.pa.action_type = "kyc"
        self.pa.save(update_fields=["action_type"])
        self._assert_matches(self._sent(router._send_email_flow, self.pa, "address"))  # noqa: SLF001
        self.user.email = "ada@example.com"
        self.user.save(update_fields=["email"])
        self._assert_matches(self._sent(router._send_email_flow, self.pa, "code"))     # noqa: SLF001


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


class SignupFormFlowTests(TestCase):
    """The template-gallery pattern, on our own flow: names + email in ONE
    private form, chained straight into the PIN pair — the whole signup with
    zero chat round-trips. The email is only COLLECTED here; the OTP round-trip
    still verifies it afterwards."""

    def _ob(self, step=None):
        from datetime import timedelta as td

        from .flows import FLOW_SIGNUP_STATE
        from .models import WaOnboarding

        return WaOnboarding.objects.create(
            msisdn="2348099990001", step=step or FLOW_SIGNUP_STATE, payload={},
            expires_at=timezone.now() + td(minutes=15))

    def _submit(self, ob, **data):
        from .flows import handle_flow_request, sign_onboarding_token

        return handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_onboarding_token(ob),
                                    "data": data})

    def test_init_opens_the_signup_form(self):
        from .flows import SIGNUP_SCREEN, handle_flow_request, sign_onboarding_token

        resp = handle_flow_request({"action": "INIT", "flow_token": sign_onboarding_token(self._ob())})
        self.assertEqual(resp["screen"], SIGNUP_SCREEN)

    def test_valid_details_move_to_the_phone_page_when_no_email_rail_exists(self):
        # The ladder is details -> email code -> phone -> PIN, but a deploy with
        # no email rail cannot send a code, so the ladder skips to the phone
        # page rather than dead-ending the signup.
        from .flows import SIGNUP_PHONE

        ob = self._ob()
        resp = self._submit(ob, first_name="Ngozi", last_name="Ade", email="Ngozi@Example.com")
        self.assertEqual(resp["screen"], SIGNUP_PHONE)        # same flow session
        ob.refresh_from_db()
        self.assertEqual(ob.payload["email"], "ngozi@example.com")
        self.assertEqual(ob.payload["first_name"], "Ngozi")

    def test_with_an_email_rail_the_code_page_comes_first_and_verifies(self):
        from .flows import SIGNUP_EMAIL_CODE, SIGNUP_PHONE

        ob = self._ob()
        with patch("whatsapp.router.email_live", return_value=True), \
             patch("whatsapp.router.send_email", return_value={"success": True}) as mail:
            resp = self._submit(ob, first_name="Ngozi", last_name="Ade", email="n1@example.com")
            self.assertEqual(resp["screen"], SIGNUP_EMAIL_CODE)
            self.assertIn("n1@example.com", resp["data"]["summary"])
            code = mail.call_args[0][2].split("code is ")[1][:6]
        # Wrong code: a prompt with the reason, on the same page.
        wrong = self._submit(ob, email_code="000000")
        self.assertEqual(wrong["screen"], SIGNUP_EMAIL_CODE)
        self.assertIn("⚠️", wrong["data"]["error"])
        # Right code: verified, on to the phone page.
        ok = self._submit(ob, email_code=code)
        self.assertEqual(ok["screen"], SIGNUP_PHONE)
        ob.refresh_from_db()
        self.assertTrue(ob.payload["email_verified_flow"])

    def test_three_wrong_codes_move_on_unverified_instead_of_dead_ending(self):
        from .flows import SIGNUP_EMAIL_CODE, SIGNUP_PHONE

        ob = self._ob()
        with patch("whatsapp.router.email_live", return_value=True), \
             patch("whatsapp.router.send_email", return_value={"success": True}):
            self._submit(ob, first_name="Ngozi", last_name="Ade", email="n2@example.com")
        for _ in range(2):
            self.assertEqual(self._submit(ob, email_code="000000")["screen"], SIGNUP_EMAIL_CODE)
        third = self._submit(ob, email_code="000000")
        self.assertEqual(third["screen"], SIGNUP_PHONE)       # moved on, with the note
        self.assertIn("⚠️", third["data"]["error"])
        ob.refresh_from_db()
        self.assertFalse(ob.payload.get("email_verified_flow"))

    def test_a_taken_or_malformed_phone_is_refused_with_the_reason(self):
        from .flows import FLOW_PHONE_STATE, SIGNUP_PHONE

        User.objects.create(username="08055550000", phone="08055550000", email="t@x.z")
        ob = self._ob(step=FLOW_PHONE_STATE)
        short = self._submit(ob, phone="0812345")
        self.assertEqual(short["screen"], SIGNUP_PHONE)
        self.assertIn("11-digit", short["data"]["error"])
        taken = self._submit(ob, phone="08055550000")
        self.assertIn("already on a Zitch account", taken["data"]["error"])

    def test_the_phone_page_chains_into_the_pin_screen(self):
        from .flows import FLOW_PHONE_STATE, PIN_CHAIN

        ob = self._ob(step=FLOW_PHONE_STATE)
        resp = self._submit(ob, phone="08066660001")
        self.assertEqual(resp["screen"], PIN_CHAIN)
        ob.refresh_from_db()
        self.assertEqual(ob.payload["phone"], "08066660001")

    def test_the_whole_signup_completes_in_one_flow_session(self):
        from .flows import PIN_CONFIRM, SUCCESS_SCREEN

        ob = self._ob()
        self._submit(ob, first_name="Ngozi", last_name="Ade", email="ngozi1@example.com")
        # Typing the number you are chatting from proves possession of it.
        self._submit(ob, phone="08099990001")
        ob.refresh_from_db()
        first = self._submit(ob, pin="246810")
        self.assertEqual(first["screen"], PIN_CONFIRM)
        done = self._submit(ob, pin="246810")
        self.assertEqual(done["screen"], SUCCESS_SCREEN)
        u = User.objects.get(phone="08099990001")
        self.assertEqual(u.email, "ngozi1@example.com")
        self.assertFalse(u.email_verified)                    # no email rail here
        self.assertTrue(u.phone_verified)                     # same number as the chat
        self.assertTrue(u.check_transaction_pin("246810"))

    def test_signup_rejects_predictable_pin_onto_the_empty_retry_twin(self):
        """A refused signup PIN has to land on PIN_RETRY, not re-render PIN_CHAIN.

        WhatsApp keeps a form's client-side value when the endpoint answers with
        the SAME screen id, and the PIN box is masked — so a re-render leaves the
        refused PIN sitting in a box the customer cannot read, and one Confirm tap
        resubmits the identical weak PIN. The twin arrives empty, which is the
        only way the customer can actually act on the error.
        """
        from .flows import PIN_CONFIRM, PIN_RETRY, SUCCESS_SCREEN

        ob = self._ob()
        self._submit(ob, first_name="Ngozi", last_name="Ade", email="weak-pin@example.com")
        self._submit(ob, phone="08099990001")
        rejected = self._submit(ob, pin="123456")
        self.assertEqual(rejected["screen"], PIN_RETRY)
        self.assertIn("less predictable", rejected["data"]["error"])
        ob.refresh_from_db()
        self.assertNotIn("flow_pin_hash", ob.payload)
        self.assertEqual(ob.payload["flow_screen"], PIN_RETRY)

        # PIN_CHAIN -> PIN_RETRY -> PIN_CONFIRM are all legal routes in the
        # published flow, so recovering from the rejection still finishes signup.
        self.assertEqual(self._submit(ob, pin="246810")["screen"], PIN_CONFIRM)
        self.assertEqual(self._submit(ob, pin="246810")["screen"], SUCCESS_SCREEN)
        self.assertTrue(User.objects.get(phone="08099990001").check_transaction_pin("246810"))

    def test_a_different_account_phone_is_stored_unverified(self):
        from .flows import SUCCESS_SCREEN

        ob = self._ob()
        self._submit(ob, first_name="Ngozi", last_name="Ade", email="ngozi3@example.com")
        self._submit(ob, phone="08077770002")                  # banks on a different line
        self._submit(ob, pin="246810")
        done = self._submit(ob, pin="246810")
        self.assertEqual(done["screen"], SUCCESS_SCREEN)
        u = User.objects.get(phone="08077770002")              # the TYPED number
        self.assertFalse(u.phone_verified)                     # possession not proven

    def test_bad_or_taken_details_re_render_with_the_reason(self):
        from .flows import SIGNUP_SCREEN

        ob = self._ob()
        self.assertIn("first and last name",
                      self._submit(ob, first_name="N", last_name="A", email="x@y.z")["data"]["error"])
        self.assertIn("look like an email",
                      self._submit(ob, first_name="Ngozi", last_name="Ade", email="nope")["data"]["error"])
        User.objects.create(username="08088880000", phone="08088880000", email="taken2@example.com")
        resp = self._submit(ob, first_name="Ngozi", last_name="Ade", email="taken2@example.com")
        self.assertEqual(resp["screen"], SIGNUP_SCREEN)
        self.assertIn("already on a Zitch account", resp["data"]["error"])
        ob.refresh_from_db()
        self.assertNotIn("email", ob.payload)                 # nothing stored on a refusal


class TransferFormFlowTests(TestCase):
    """The transfer form: amount + account + searchable bank in one private
    screen, the bank auto-detected from the NUBAN checksum when it is
    unambiguous, and the account NAME resolved server-side before the PIN."""

    def setUp(self):
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#e30613", active=True)
        Bank.objects.create(code="uba", name="UBA", bank_code="033", color="#c00", active=True)
        self.user = _make_user()

    def _valid_account(self, bank_code="058"):
        """Mint a NUBAN whose check digit is valid for `bank_code` only."""
        serial = "123456789"
        seq = bank_code + serial
        check = (10 - sum(int(c) * (3, 7, 3)[i % 3] for i, c in enumerate(seq)) % 10) % 10
        return serial + str(check)

    def _pa(self):
        from .flows import FLOW_FORM_STATE

        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer", state=FLOW_FORM_STATE,
            payload={"pin_attempts": 0}, expires_at=timezone.now() + timedelta(minutes=5))

    def _submit(self, pa, **data):
        from .flows import handle_flow_request

        return handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_flow_token(pa), "data": data})

    def test_the_form_resolves_the_name_and_chains_into_the_pin_screen(self):
        from .flows import PIN_CHAIN

        pa = self._pa()
        with patch("utility.providers.payout_resolve_account",
                   return_value={"success": True, "name": "Adeyemi William"}):
            resp = self._submit(pa, amount="2300", account_number="0123456789", bank="gtb")
        self.assertEqual(resp["screen"], PIN_CHAIN)
        self.assertIn("ADEYEMI WILLIAM", resp["data"]["recipient"])   # auto-detected name
        self.assertIn("GTBank", resp["data"]["details"])

    def test_one_checksum_match_autodetects_the_bank(self):
        from .flows import PIN_CHAIN

        acct = self._valid_account("058")
        pa = self._pa()
        with patch("utility.providers.payout_resolve_account",
                   return_value={"success": True, "name": "Ada Eze"}) as enquiry:
            resp = self._submit(pa, amount="2300", account_number=acct, bank="")
        self.assertEqual(resp["screen"], PIN_CHAIN)
        self.assertIn("GTBank", resp["data"]["details"])              # detected, not asked
        enquiry.assert_called_once_with(acct, "058")

    def test_no_checksum_match_asks_rather_than_guessing(self):
        from .flows import TRANSFER_FORM

        pa = self._pa()
        # 10 digits whose check digit fits neither seeded bank.
        acct = self._valid_account("058")
        bad = acct[:9] + str((int(acct[9]) + 1) % 10)
        resp = self._submit(pa, amount="2300", account_number=bad, bank="")
        self.assertEqual(resp["screen"], TRANSFER_FORM)
        self.assertIn("Pick the bank", resp["data"]["error"])

    def test_every_chat_guard_still_holds_on_the_form(self):
        from .flows import TRANSFER_FORM

        pa = self._pa()
        self.assertIn("10 digits",
                      self._submit(pa, amount="2300", account_number="123", bank="gtb")["data"]["error"])
        self.assertIn("Minimum",
                      self._submit(pa, amount="10", account_number="0123456789", bank="gtb")["data"]["error"])
        resp = self._submit(pa, amount="999999999", account_number="0123456789", bank="gtb")
        self.assertEqual(resp["screen"], TRANSFER_FORM)               # limit / balance refusal

    def test_an_account_with_no_pin_is_refused_not_shown_a_pin_pad(self):
        """The chat path refuses through _arm_confirm; chaining past it would
        raise a PIN screen the customer has no way to satisfy."""
        from .flows import SUCCESS_SCREEN

        self.user.transaction_pin = ""
        self.user.save(update_fields=["transaction_pin"])
        pa = self._pa()
        with patch("utility.providers.payout_resolve_account",
                   return_value={"success": True, "name": "Ada Eze"}):
            resp = self._submit(pa, amount="2300", account_number="0123456789", bank="gtb")
        self.assertEqual(resp["screen"], SUCCESS_SCREEN)
        self.assertIn("set pin", resp["data"]["message"].lower())
        self.assertFalse(PendingAction.objects.filter(id=pa.id).exists())

    def test_the_whole_transfer_completes_in_one_session(self):
        from .flows import SUCCESS_SCREEN

        pa = self._pa()
        before = get_or_create_wallet(self.user).balance
        with patch("utility.providers.payout_resolve_account",
                   return_value={"success": True, "name": "Ada Eze"}):
            self._submit(pa, amount="2300", account_number="0123456789", bank="gtb")
        done = self._submit(pa, pin="1234")
        self.assertEqual(done["screen"], SUCCESS_SCREEN)
        self.assertEqual(get_or_create_wallet(self.user).balance, before - Decimal("2300"))


class TransferFormChatGuardTests(TestCase):
    """Chat text while the transfer form is armed. This guard previously sat in
    _confirm_prompt(pa) — a string builder with no text/msisdn in scope — where
    it was dead code one routing change away from a NameError. These pin it to
    the dispatcher where the text actually arrives."""

    def setUp(self):
        self.user = _make_user()
        from .flows import FLOW_FORM_STATE

        self.pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer", state=FLOW_FORM_STATE,
            payload={"pin_attempts": 0}, expires_at=timezone.now() + timedelta(minutes=5))

    def _say(self, text):
        from whatsapp.router import handle_inbound

        sent = []
        with patch("whatsapp.router.reply", side_effect=lambda m, t, **k: sent.append(t)), \
             patch("whatsapp.router.send_flow", return_value={"success": True}):
            handle_inbound(MSISDN, text)
        return "\n".join(sent)

    def test_digits_are_pointed_back_at_the_form_without_crashing(self):
        # Digit-only text is far more likely a stray amount/code than a new
        # instruction, so it stays on the form. This is the line that would
        # have raised NameError from the old location.
        out = self._say("2500")
        self.assertIn("Send money", out)
        self.assertTrue(PendingAction.objects.filter(id=self.pa.id).exists())

    def test_a_new_command_escapes_the_form(self):
        out = self._say("balance")
        self.assertIn("leaving that transfer", out)
        self.assertIn("balance", out.lower())          # and the command still ran
        self.assertFalse(PendingAction.objects.filter(
            id=self.pa.id, state="flow_transfer_form").exists())


class SignupPhoneCodeTests(TestCase):
    """The SMS check for the typed phone — mirroring the email code page.

    Only for a number OTHER than the one they are chatting from: the chat
    session already proves possession of its own number, so that case skips
    the code entirely rather than paying for an SMS that proves nothing new.
    """

    def _ob(self):
        from datetime import timedelta as td

        from .flows import FLOW_PHONE_STATE
        from .models import WaOnboarding

        return WaOnboarding.objects.create(
            msisdn="2348099990001", step=FLOW_PHONE_STATE,
            payload={"first_name": "Ngozi", "last_name": "Ade", "email": "np@example.com"},
            expires_at=timezone.now() + td(minutes=15))

    def _submit(self, ob, **data):
        from .flows import handle_flow_request, sign_onboarding_token

        return handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_onboarding_token(ob), "data": data})

    def test_the_chat_number_skips_the_code_page(self):
        from .flows import PIN_CHAIN

        ob = self._ob()
        with patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms") as sms:
            resp = self._submit(ob, phone="08099990001")   # the number they chat from
        self.assertEqual(resp["screen"], PIN_CHAIN)
        sms.assert_not_called()                            # possession already proven

    def test_a_different_number_gets_the_code_page_and_verifies(self):
        from .flows import PIN_CHAIN, SIGNUP_PHONE_CODE

        ob = self._ob()
        with patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}) as sms:
            resp = self._submit(ob, phone="08077770009")
            self.assertEqual(resp["screen"], SIGNUP_PHONE_CODE)
            self.assertIn("•••••0009", resp["data"]["summary"])
            self.assertEqual(sms.call_args[0][0], "08077770009")   # the TYPED number
            code = sms.call_args[0][1].split("Zitch: ")[1][:6]
        wrong = self._submit(ob, phone_code="000000")
        self.assertEqual(wrong["screen"], SIGNUP_PHONE_CODE)
        self.assertIn("⚠️", wrong["data"]["error"])
        ok = self._submit(ob, phone_code=code)
        self.assertEqual(ok["screen"], PIN_CHAIN)
        ob.refresh_from_db()
        self.assertTrue(ob.payload["phone_verified_flow"])

    def test_no_sms_rail_moves_on_unverified_rather_than_dead_ending(self):
        from .flows import PIN_CHAIN

        ob = self._ob()
        resp = self._submit(ob, phone="08077770010")       # sms_live False in tests
        self.assertEqual(resp["screen"], PIN_CHAIN)
        ob.refresh_from_db()
        self.assertFalse(ob.payload.get("phone_verified_flow"))

    def test_a_flow_verified_number_lands_verified_on_the_account(self):
        from .flows import SUCCESS_SCREEN

        ob = self._ob()
        with patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}) as sms:
            self._submit(ob, phone="08077770011")
            code = sms.call_args[0][1].split("Zitch: ")[1][:6]
        self._submit(ob, phone_code=code)
        self._submit(ob, pin="246810")
        done = self._submit(ob, pin="246810")
        self.assertEqual(done["screen"], SUCCESS_SCREEN)
        u = User.objects.get(phone="08077770011")
        self.assertTrue(u.phone_verified)                  # proven by the code, not the chat


class ScreenContractTests(TestCase):
    """Every reply must declare EXACTLY the properties its screen does.

    WhatsApp renders any mismatch — a missing declared property or an extra
    undeclared one — as "Couldn't load content. Try again later." on the device,
    naming neither the screen nor the key. That is what a customer saw after a
    ₦1,000 data purchase that had in fact gone through: the endpoint's own
    error screen was hand-built and omitted `status`, so the one screen whose
    job was to explain a failure in words was itself unrenderable.
    """

    def declared(self):
        path = os.path.join(os.path.dirname(__file__), "flow_assets", "pin_flow.json")
        with open(path, encoding="utf-8") as fh:
            return {s["id"]: set((s.get("data") or {}).keys())
                    for s in json.load(fh)["screens"]}

    def test_the_endpoints_own_error_screen_is_renderable(self):
        """The regression. This screen only ever appears when something has
        already gone wrong, which is exactly when nobody is watching it."""
        from .flows import _success_screen

        resp = _success_screen("Something went wrong on our side.", status="failed")
        self.assertEqual(set(resp["data"].keys()), self.declared()[resp["screen"]])
        self.assertIn("status", resp["data"])

    def test_every_screen_builder_matches_its_declaration(self):
        from .flows import (CODE_SCREEN, EMAIL_SCREEN, IDENTITY_SCREEN, PIN_CONFIRM,
                            PIN_RETRY, PIN_SCREEN, _confirm_pin_screen, _email_screen,
                            _identity_screen, _pin_screen, _success_screen)

        declared = self.declared()
        built = [
            _pin_screen({"balance": "b", "amount": "a", "recipient": "r",
                         "details": "d", "narration": "n"}, screen=PIN_SCREEN),
            _pin_screen("one-line summary", error="oops", screen=PIN_RETRY),
            _confirm_pin_screen(),
            _confirm_pin_screen(error="mismatch"),
            _identity_screen("BVN"),
            _identity_screen("BVN", screen=CODE_SCREEN),
            _email_screen(),
            _success_screen("done"),
            _success_screen("nope", status="failed"),
        ]
        for resp in built:
            with self.subTest(screen=resp["screen"]):
                self.assertIn(resp["screen"], declared)
                self.assertEqual(set(resp["data"].keys()), declared[resp["screen"]])

    def test_a_mismatch_is_logged_with_the_offending_key(self):
        """The guard does not repair the response — guessing a value for a
        property the caller forgot papers over the bug that produced it. It
        records which key is wrong so the next one is a grep, not a screenshot."""
        from .flows import _check_contract

        with self.assertLogs("whatsapp", level="ERROR") as caught:
            _check_contract({"screen": "SUCCESS", "data": {"message": "no status here"}})
        self.assertIn("wa_flow_contract_mismatch", "\n".join(caught.output))
        self.assertIn("status", "\n".join(caught.output))

    def test_an_unknown_screen_is_left_alone(self):
        """A guard that cannot recognise a screen must not start refusing it."""
        from .flows import _check_contract

        passthrough = {"screen": "NOT_A_SCREEN", "data": {"x": 1}}
        self.assertEqual(_check_contract(passthrough), passthrough)

    @override_settings(WHATSAPP={"MODE": "sandbox", "VERIFY_TOKEN": "v", "TOKEN": "",
                                 "APP_SECRET": "", "PHONE_NUMBER_ID": "1",
                                 "BASE_URL": "https://graph.test"})
    def test_the_live_endpoint_answers_a_renderable_screen_when_logic_explodes(self):
        """End to end through the VIEW: an exception inside the handler must
        still reach the device as a screen it can draw.

        This is the exact path that produced "Couldn't load content" on a
        payment that had already gone through — the handler raised, and the
        view's hand-built fallback was itself invalid.
        """
        captured = {}

        def fake_encrypt(response, aes_key, iv):
            captured["response"] = response
            return "ciphertext"

        with patch("whatsapp.flows_crypto.decrypt_request",
                   return_value=({"action": "data_exchange", "flow_token": "x.y"}, b"k", b"iv")), \
             patch("whatsapp.flows_crypto.encrypt_response", side_effect=fake_encrypt), \
             patch("whatsapp.flows.handle_flow_request", side_effect=RuntimeError("boom")):
            res = Client().post("/webhooks/whatsapp/flow", data=json.dumps({"x": 1}),
                                content_type="application/json")

        self.assertEqual(res.status_code, 200)
        resp = captured["response"]
        self.assertEqual(set(resp["data"].keys()), self.declared()[resp["screen"]])
        self.assertIn("status", resp["data"])
