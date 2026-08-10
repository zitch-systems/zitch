"""Deep-link biometric approval: authorise a chat-armed money action in the app.

The chain is: chat arms a confirm → the confirm message carries an https link →
the bounce page forwards into zitch://waapprove → the app authenticates the
customer (biometric releasing the cached PIN on-device) and posts the SAME
transaction PIN every other surface verifies. These tests pin the properties
that make the hand-off no weaker than the Flow it stands beside:

* the token is tamper-evident and single-use;
* only a session belonging to the action's owner may see or redeem it, and a
  wrong user learns nothing ("expired", not "forbidden");
* the server verifies the PIN — possession of a token and a session is never
  enough;
* execution runs through run_flow_execution, so freeze re-check and idempotent
  executors are shared, not re-implemented.
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from accounts.models import AccessToken
from transfers.models import Bank
from wallet.services import credit, get_or_create_wallet

from .flows import FLOW_PIN_STATE, resolve_approve_token, sign_approve_token
from .models import PendingAction, WhatsAppLink

from django.utils import timezone

User = get_user_model()
MSISDN = "2348011112222"

_WA_ON = {"MODE": "sandbox", "ALLOW_CHAT_SIGNUP": True, "VERIFY_TOKEN": "",
          "APP_SECRET": "", "TOKEN": "", "PHONE_NUMBER_ID": "", "BUSINESS_NUMBER": ""}


def _user(phone="08010000001", email="ada@zitch.test", msisdn=MSISDN):
    u = User.objects.create(username=phone, phone=phone, email=email,
                            first_name="Ada", last_name="Eze", tier=1,
                            email_verified=True, phone_verified=True,
                            bvn_verified=True, nin_verified=True)
    u.set_transaction_pin("1234")
    u.save()
    get_or_create_wallet(u)
    credit(u, Decimal("50000"), "Seed")
    WhatsAppLink.objects.create(user=u, wa_msisdn=msisdn, status=WhatsAppLink.ACTIVE)
    return u, AccessToken.issue(u).key


def _armed_transfer(user, state=FLOW_PIN_STATE):
    return PendingAction.objects.create(
        user=user, msisdn=MSISDN, action_type="transfer", state=state,
        payload={"amount": "5000", "account": "0123456789", "bank_code": "058",
                 "bank_name": "GTBank", "name": "JOHN DOE",
                 "flow_summary": "Send ₦5,000.00 to JOHN DOE · GTBank 0123456789",
                 "pin_attempts": 0},
        expires_at=timezone.now() + timedelta(minutes=5),
    )


class ApproveTokenTests(TestCase):
    def setUp(self):
        self.user, _ = _user()

    def test_roundtrip_resolves_while_armed(self):
        pa = _armed_transfer(self.user)
        self.assertEqual(resolve_approve_token(sign_approve_token(pa)).id, pa.id)

    def test_the_sms_code_rung_is_also_approvable(self):
        """Which secure channel was offered first doesn't change what approving
        in the app means — both confirm states accept the hand-off."""
        pa = _armed_transfer(self.user, state="pin")
        self.assertIsNotNone(resolve_approve_token(sign_approve_token(pa)))

    def test_a_flipped_signature_resolves_to_nothing(self):
        pa = _armed_transfer(self.user)
        token = sign_approve_token(pa)
        forged = token[:-1] + ("a" if token[-1] != "a" else "b")
        self.assertIsNone(resolve_approve_token(forged))

    def test_an_expired_action_resolves_to_nothing(self):
        pa = _armed_transfer(self.user)
        token = sign_approve_token(pa)
        pa.expires_at = timezone.now() - timedelta(seconds=1)
        pa.save(update_fields=["expires_at"])
        self.assertIsNone(resolve_approve_token(token))

    def test_an_action_not_at_a_confirm_step_resolves_to_nothing(self):
        """A token minted (or replayed) against an action still collecting slots
        must not let the app skip straight to execution."""
        pa = _armed_transfer(self.user, state="amount")
        self.assertIsNone(resolve_approve_token(sign_approve_token(pa)))

    def test_an_approve_token_is_not_a_flow_or_identity_token(self):
        from .flows import resolve_flow_token, resolve_identity_token, resolve_onboarding_token

        pa = _armed_transfer(self.user)
        token = sign_approve_token(pa)
        self.assertIsNone(resolve_flow_token(token))
        self.assertIsNone(resolve_identity_token(token))
        self.assertIsNone(resolve_onboarding_token(token))


@override_settings(WHATSAPP=_WA_ON)
class ApproveEndpointTests(TestCase):
    def setUp(self):
        self.client = Client()
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#e30613", active=True)
        self.user, self.token = _user()
        self.pa = _armed_transfer(self.user)
        self.handoff = sign_approve_token(self.pa)

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    # --- preview -----------------------------------------------------------
    def test_preview_shows_the_armed_summary_verbatim(self):
        res, body = self.post("/api/whatsapp/approve/preview/",
                              {"access_token": self.token, "token": self.handoff})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["summary"], "Send ₦5,000.00 to JOHN DOE · GTBank 0123456789")
        self.assertEqual(body["action_type"], "transfer")

    def test_a_different_users_session_learns_nothing(self):
        """The token rode through a chat and an OS deep link. A signed-in wrong
        account gets the same words as an expired token — a probe cannot tell a
        real token from a dead one."""
        _, other_token = _user(phone="08020000002", email="bob@zitch.test",
                               msisdn="2348099998888")
        res, body = self.post("/api/whatsapp/approve/preview/",
                              {"access_token": other_token, "token": self.handoff})
        self.assertEqual(res.status_code, 410)
        self.assertIn("expired", body["message"].lower())
        self.assertNotIn("JOHN DOE", str(body))

    def test_preview_requires_a_session(self):
        res, _ = self.post("/api/whatsapp/approve/preview/", {"token": self.handoff})
        self.assertEqual(res.status_code, 401)

    # --- execute -----------------------------------------------------------
    def test_the_pin_is_verified_server_side(self):
        """Token + session is never enough: the biometric only releases the PIN
        on-device, and this is where that PIN is actually checked."""
        res, body = self.post("/api/whatsapp/approve/execute/",
                              {"access_token": self.token, "token": self.handoff,
                               "transaction_pin": "0000"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("50000"))
        # And the action survives a wrong PIN for a retry.
        self.assertIsNotNone(resolve_approve_token(self.handoff))

    def test_a_correct_pin_executes_and_burns_the_token(self):
        res, body = self.post("/api/whatsapp/approve/execute/",
                              {"access_token": self.token, "token": self.handoff,
                               "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("45000"))
        # Execution cleared the action: the same hand-off cannot be redeemed twice.
        self.assertIsNone(resolve_approve_token(self.handoff))
        res2, _ = self.post("/api/whatsapp/approve/execute/",
                            {"access_token": self.token, "token": self.handoff,
                             "transaction_pin": "1234"})
        self.assertEqual(res2.status_code, 410)

    def test_execute_refuses_a_different_users_session(self):
        _, other_token = _user(phone="08020000002", email="bob@zitch.test",
                               msisdn="2348099998888")
        res, _ = self.post("/api/whatsapp/approve/execute/",
                           {"access_token": other_token, "token": self.handoff,
                            "transaction_pin": "1234"})
        self.assertEqual(res.status_code, 410)
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("50000"))

    def test_a_frozen_account_cannot_execute_via_the_handoff(self):
        """The freeze holds on this path twice over: require_user refuses the
        frozen account's session outright, and even a session that slipped
        through would hit run_flow_execution's own is_active re-check."""
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        res, body = self.post("/api/whatsapp/approve/execute/",
                              {"access_token": self.token, "token": self.handoff,
                               "transaction_pin": "1234"})
        self.assertNotEqual(res.status_code, 200)
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("50000"))

    def test_pin_lockout_is_shared_with_every_other_surface(self):
        for _ in range(5):
            self.post("/api/whatsapp/approve/execute/",
                      {"access_token": self.token, "token": self.handoff,
                       "transaction_pin": "0000"})
        res, body = self.post("/api/whatsapp/approve/execute/",
                              {"access_token": self.token, "token": self.handoff,
                               "transaction_pin": "1234"})   # correct, but locked
        self.assertEqual(res.status_code, 429)


@override_settings(WHATSAPP=_WA_ON,
                   ZITCH_LINKS={"WEBSITE": "https://zitch.ng", "APP": "https://zitch.ng/app",
                                "SUPPORT_WA": "", "SUPPORT_EMAIL": "",
                                "API_BASE": "https://api.zitch.test"})
class HandoffSurfaceTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, _ = _user()

    def test_the_confirm_prompt_carries_the_approve_link(self):
        from .router import _confirm_prompt

        pa = _armed_transfer(self.user, state="pin")
        pa.payload["otp_hash"] = "x"
        prompt = _confirm_prompt(pa)
        self.assertIn("https://api.zitch.test/wa/approve/", prompt)
        self.assertIn("fingerprint or Face ID", prompt)

    def test_the_bounce_page_forwards_into_the_app_and_shows_nothing(self):
        """Unauthenticated by design, so it must render no amount, recipient or
        state — only the bounce and the store fallback."""
        pa = _armed_transfer(self.user)
        token = sign_approve_token(pa)
        res = self.client.get(f"/wa/approve/{token}")
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn(f"zitch://waapprove?token={token}", html)
        self.assertIn("https://zitch.ng/app", html)
        self.assertNotIn("JOHN DOE", html)
        self.assertNotIn("5,000", html)

    def test_the_bounce_page_strips_hostile_path_material(self):
        # No %2F: Django's <str:> converter 404s an encoded slash before the view
        # runs, which is its own defence — this exercises the in-view filter.
        res = self.client.get("/wa/approve/ab%22%3E%3Cscript%3Ealert(1)")
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertNotIn("<script>alert", html)
        self.assertNotIn('token=ab">', html)


@override_settings(WHATSAPP=_WA_ON,
                   ZITCH_LINKS={"WEBSITE": "https://zitch.ng", "APP": "https://zitch.ng/app",
                                "SUPPORT_WA": "", "SUPPORT_EMAIL": "",
                                "API_BASE": "https://api.zitch.test"})
class BiometricPrimaryTests(TestCase):
    """Biometric approval is the PREFERRED confirmation for anyone who has the
    app — it proves the owner's finger or face, which a shoulder-surfed PIN
    cannot. `known_devices` (written only on app sign-in) picks the framing; a
    WhatsApp-only customer is not led to a door they can't open."""

    def setUp(self):
        self.user, _ = _user()

    def _give_app(self):
        from accounts.models import KnownDevice

        KnownDevice.objects.create(user=self.user, device_id="dev-1", model="Pixel 7")

    def test_an_app_customer_gets_biometric_first_and_pin_as_fallback(self):
        from unittest.mock import patch

        from whatsapp import router

        self._give_app()
        pa = _armed_transfer(self.user, state="bank")
        with patch.object(router, "flows_live", return_value=True), \
             patch.object(router, "send_flow", return_value={"success": True}) as sent:
            router._arm_confirm(pa, self.user)  # noqa: SLF001
        kwargs = sent.call_args.kwargs
        body = kwargs.get("body") or sent.call_args.args[3]
        # The message LEADS with the biometric approval…
        self.assertLess(body.find("fingerprint or Face ID"), body.find("api.zitch.test"))
        self.assertIn("Approve with your fingerprint", body)
        # …and the Flow button is demoted to the fallback.
        self.assertEqual(kwargs.get("cta"), "Use PIN instead")

    def test_a_whatsapp_only_customer_keeps_pin_first(self):
        from unittest.mock import patch

        from whatsapp import router

        pa = _armed_transfer(self.user, state="bank")   # no KnownDevice rows
        with patch.object(router, "flows_live", return_value=True), \
             patch.object(router, "send_flow", return_value={"success": True}) as sent:
            router._arm_confirm(pa, self.user)  # noqa: SLF001
        kwargs = sent.call_args.kwargs
        body = kwargs.get("body") or sent.call_args.args[3]
        # Summary first, the app offer second — and the default PIN CTA stands.
        self.assertTrue(body.startswith("Send ₦5,000.00"))
        self.assertIn("Have the Zitch app?", body)
        self.assertEqual(kwargs.get("cta"), "")

    def test_the_sms_rung_leads_with_biometric_for_an_app_customer(self):
        from whatsapp.router import _confirm_prompt

        self._give_app()
        pa = _armed_transfer(self.user, state="pin")
        pa.payload["otp_hash"] = "x"
        prompt = _confirm_prompt(pa)
        self.assertLess(prompt.find("fingerprint or Face ID"), prompt.find("6-digit code"))
        self.assertIn("Or enter the *6-digit code*", prompt)

    def test_the_sms_rung_stays_code_first_without_the_app(self):
        from whatsapp.router import _confirm_prompt

        pa = _armed_transfer(self.user, state="pin")
        pa.payload["otp_hash"] = "x"
        prompt = _confirm_prompt(pa)
        self.assertTrue(prompt.startswith("🔐 Enter the *6-digit code*"))
        self.assertIn("Have the Zitch app?", prompt)
