"""WhatsApp channel tests (slice 1): webhook, linking, balance, NGN transfer.

All run in MOCK mode (no Meta/Wema keys), so the webhook accepts unsigned
bodies and the payout settles automatically — the full flow is exercised offline.
"""
import hashlib
import hmac
import json
import re
import unittest
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken
from transfers.models import Bank
from utility.models import CablePlan, DataPlan
from wallet.models import Transaction
from wallet.services import credit, get_or_create_wallet

from .models import (
    AuditLog, Broadcast, BroadcastRecipient, ConversationState,
    PendingAction, SystemSetting, WaMessageLog, WhatsAppLink,
)

User = get_user_model()
MSISDN = "2348011112222"


def make_user(phone="08010000001", email="ada@zitch.test", pin="1234", balance="50000"):
    # App-linked users have completed KYC (BVN verified), so they can send on
    # WhatsApp. WhatsApp-onboarded accounts start unverified (tested separately).
    u = User.objects.create(username=phone, phone=phone, email=email,
                            first_name="Ada", last_name="Eze", tier=1, bvn_verified=True,
                            email_verified=True)  # Tier >= 1 requires it
    u.set_transaction_pin(pin)
    u.save()
    get_or_create_wallet(u)
    if Decimal(balance) > 0:
        credit(u, Decimal(balance), "Seed")
    return u, AccessToken.issue(u).key


def give_account(user, number="0100000123"):
    """Provision a Wema NUBAN on the user's wallet (what the app OTP flow persists)."""
    w = get_or_create_wallet(user)
    w.account_number = number
    w.account_name = (user.get_full_name() or "Ada Eze").strip()
    w.bank_name = "Wema Bank"
    w.save(update_fields=["account_number", "account_name", "bank_name", "updated"])
    return w


class WebhookTests(TestCase):
    def setUp(self):
        self.client = Client()

    @override_settings(WHATSAPP={"VERIFY_TOKEN": "v-tok", "TOKEN": "", "APP_SECRET": "",
                                 "BASE_URL": "x", "PHONE_NUMBER_ID": "", "BUSINESS_NUMBER": ""})
    def test_verify_handshake(self):
        res = self.client.get("/webhooks/whatsapp",
                              {"hub.mode": "subscribe", "hub.verify_token": "v-tok", "hub.challenge": "42"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content.decode(), "42")
        bad = self.client.get("/webhooks/whatsapp",
                              {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
        self.assertEqual(bad.status_code, 403)

    @override_settings(WHATSAPP={"VERIFY_TOKEN": "", "TOKEN": "", "APP_SECRET": "shh",
                                 "BASE_URL": "x", "PHONE_NUMBER_ID": "phone-1",
                                 "BUSINESS_NUMBER": "2348000000000", "MODE": "live"})
    def test_signature_enforced_when_secret_set(self):
        event = {"entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "phone-1"}}}]}]}
        body = json.dumps(event).encode()
        bad = self.client.post("/webhooks/whatsapp", data=body, content_type="application/json",
                               HTTP_X_HUB_SIGNATURE_256="sha256=deadbeef")
        self.assertEqual(bad.status_code, 401)
        good_sig = hmac.new(b"shh", body, hashlib.sha256).hexdigest()
        good = self.client.post("/webhooks/whatsapp", data=body, content_type="application/json",
                                HTTP_X_HUB_SIGNATURE_256=f"sha256={good_sig}")
        self.assertEqual(good.status_code, 200)

    @override_settings(WHATSAPP={"MODE": "disabled", "VERIFY_TOKEN": "", "TOKEN": "",
                                 "APP_SECRET": "", "BASE_URL": "x", "PHONE_NUMBER_ID": "",
                                 "BUSINESS_NUMBER": ""})
    def test_disabled_channel_exposes_no_webhook(self):
        self.assertEqual(self.client.get("/webhooks/whatsapp").status_code, 404)
        self.assertEqual(self.client.post("/webhooks/whatsapp", data=b"{}",
                                          content_type="application/json").status_code, 404)

    @override_settings(WHATSAPP={"MODE": "live", "VERIFY_TOKEN": "v", "TOKEN": "t",
                                 "APP_SECRET": "shh", "BASE_URL": "x",
                                 "PHONE_NUMBER_ID": "phone-1",
                                 "BUSINESS_NUMBER": "2348000000000"})
    def test_live_webhook_rejects_another_meta_phone_number_id(self):
        event = {"entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "attacker-phone"}}}]}]}
        body = json.dumps(event).encode()
        sig = hmac.new(b"shh", body, hashlib.sha256).hexdigest()
        response = self.client.post("/webhooks/whatsapp", data=body,
                                    content_type="application/json",
                                    HTTP_X_HUB_SIGNATURE_256=f"sha256={sig}")
        self.assertEqual(response.status_code, 400)

    @override_settings(DEBUG=False, TESTING=False, WHATSAPP_QUEUE_KEY="queue-key")
    def test_production_worker_refuses_to_consume_when_channel_is_not_live(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with patch("whatsapp.management.commands.whatsapp_worker.wa_live",
                   return_value=False):
            with self.assertRaisesMessage(CommandError, "WHATSAPP_MODE=live"):
                call_command("whatsapp_worker", "--once")


class ChannelTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user()
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#e30613", active=True)

    # --- helpers ---
    def inbound(self, text, mid, msisdn=MSISDN):
        event = {"entry": [{"changes": [{"value": {"messages": [
            {"from": msisdn, "id": mid, "type": "text", "text": {"body": text}}]}}]}]}
        return self.client.post("/webhooks/whatsapp", data=json.dumps(event), content_type="application/json")

    def last_reply(self, msisdn=MSISDN):
        row = WaMessageLog.objects.filter(msisdn=msisdn, direction=WaMessageLog.OUT).order_by("-created").first()
        return row.text if row else ""

    def link(self):
        return WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)

    def balance(self):
        return get_or_create_wallet(self.user).balance

    @override_settings(WHATSAPP_PROCESS_INLINE=False)
    def test_production_webhook_persists_before_worker_processing(self):
        """The HTTP acknowledgement never owns execution in production."""
        self.link()
        response = self.inbound("balance", "async-1")
        self.assertEqual(response.status_code, 200)
        queued = WaMessageLog.objects.get(wa_message_id="async-1")
        self.assertIsNone(queued.processed_at)
        self.assertTrue(bytes(queued.processing_payload))
        self.assertFalse(WaMessageLog.objects.filter(
            msisdn=MSISDN, direction=WaMessageLog.OUT,
        ).exists())

        from .jobs import process_inbound_batch
        self.assertEqual(process_inbound_batch(), 1)
        queued.refresh_from_db()
        self.assertIsNotNone(queued.processed_at)
        self.assertEqual(bytes(queued.processing_payload), b"")
        self.assertIn("balance", self.last_reply().lower())

    @override_settings(WHATSAPP_PROCESS_INLINE=False)
    def test_dead_letter_erases_encrypted_customer_payload(self):
        self.link()
        self.inbound("balance", "dead-1")
        queued = WaMessageLog.objects.get(wa_message_id="dead-1")
        queued.processing_attempts = 4
        queued.save(update_fields=["processing_attempts"])

        from .jobs import process_inbound_message
        with patch("whatsapp.jobs.handle_inbound", side_effect=RuntimeError("crash")):
            self.assertEqual(process_inbound_message(queued.pk), "dead_letter")
        queued.refresh_from_db()
        self.assertIsNotNone(queued.processed_at)
        self.assertEqual(bytes(queued.processing_payload), b"")
        self.assertEqual(queued.processing_error, "dead_letter:RuntimeError")

    @override_settings(
        WHATSAPP_PROCESS_INLINE=False,
        WHATSAPP_QUEUE_KEY="old-queue-key-0123456789-0123456789",
    )
    def test_worker_decrypts_jobs_during_queue_key_rotation_overlap(self):
        self.link()
        self.inbound("balance", "rotate-1")
        queued = WaMessageLog.objects.get(wa_message_id="rotate-1")

        from .jobs import process_inbound_message
        with override_settings(
            WHATSAPP_QUEUE_KEY="new-queue-key-0123456789-0123456789",
            WHATSAPP_QUEUE_KEY_PREV="old-queue-key-0123456789-0123456789",
        ):
            with patch("whatsapp.jobs.handle_inbound") as handler:
                self.assertEqual(process_inbound_message(queued.pk), "processed")
        handler.assert_called_once_with(MSISDN, "balance")

    # --- linking ---
    def test_unlinked_number_gets_create_or_link_choice(self):
        self.inbound("hello", "m1", msisdn="2349090000001")
        self.assertIn("create", self.last_reply(msisdn="2349090000001").lower())
        # Choosing "link" points to the in-app link flow.
        self.inbound("2", "m2", msisdn="2349090000001")
        self.assertIn("Link WhatsApp", self.last_reply(msisdn="2349090000001"))
        self.assertFalse(WhatsAppLink.objects.filter(wa_msisdn="2349090000001", status=WhatsAppLink.ACTIVE).exists())

    @override_settings(WHATSAPP={"MODE": "sandbox", "ALLOW_CHAT_SIGNUP": False,
                                 "VERIFY_TOKEN": "", "TOKEN": "", "APP_SECRET": "",
                                 "BASE_URL": "x", "PHONE_NUMBER_ID": "",
                                 "BUSINESS_NUMBER": ""})
    def test_production_style_channel_never_collects_a_pin_in_chat(self):
        m = "2349090000999"
        self.inbound("1", "secure-o1", msisdn=m)
        self.assertIn("zitch app", self.last_reply(m).lower())
        self.assertFalse(User.objects.filter(phone="09090000999").exists())

    # --- onboarding (create an account from WhatsApp) ---
    def test_onboarding_creates_tier0_account_and_links(self):
        m = "2349090000002"  # -> local 09090000002, no existing user
        self.inbound("1", "o1", msisdn=m)
        self.assertIn("first name", self.last_reply(m).lower())
        self.inbound("Tunde", "o2", msisdn=m)
        self.assertIn("last name", self.last_reply(m).lower())
        self.inbound("Bello", "o3", msisdn=m)
        self.assertIn("email", self.last_reply(m).lower())
        self.inbound("not-an-email", "o3b", msisdn=m)         # rejected, re-asked
        self.assertIn("look like an email", self.last_reply(m))
        self.inbound("Tunde.Bello@Example.com", "o3c", msisdn=m)
        self.assertIn("PIN", self.last_reply(m))
        self.inbound("1357", "o4", msisdn=m)   # set PIN
        self.inbound("1357", "o5", msisdn=m)   # confirm PIN
        u = User.objects.get(phone="09090000002")
        self.assertEqual(u.tier, 0)            # unverified chat signup (no BVN/NIN) -> Tier 0
        self.assertTrue(u.check_transaction_pin("1357"))
        self.assertEqual(u.email, "tunde.bello@example.com")  # stored lowercased
        self.assertTrue(u.onboarded_via_whatsapp)
        self.assertFalse(u.email_verified)                    # chat-collected: unproven
        self.assertFalse(u.has_usable_password())             # app entry = OTP reset
        self.assertTrue(WhatsAppLink.objects.filter(wa_msisdn=m, user=u, status=WhatsAppLink.ACTIVE).exists())
        self.assertIn("Welcome to Zitch", self.last_reply(m))
        self.assertIn("Forgot password", self.last_reply(m))  # the upgrade path is named

    def test_onboarding_refuses_an_email_already_on_an_account(self):
        # Recovery looks accounts up by email; two accounts sharing one address
        # would make reset codes ambiguous, so the collision is refused at entry.
        User.objects.create(username="08199990000", phone="08199990000", email="taken@zitch.test")
        m = "2349090000012"
        self.inbound("1", "d1", msisdn=m)
        self.inbound("Ada", "d2", msisdn=m)
        self.inbound("Obi", "d3", msisdn=m)
        self.inbound("TAKEN@zitch.test", "d4", msisdn=m)
        self.assertIn("already on a Zitch account", self.last_reply(m))
        self.inbound("ada.obi@zitch.test", "d5", msisdn=m)    # a fresh one proceeds
        self.assertIn("PIN", self.last_reply(m))

    def test_onboarding_pin_mismatch_retries(self):
        m = "2349090000003"
        self.inbound("1", "p1", msisdn=m)
        self.inbound("Ada", "p2", msisdn=m)
        self.inbound("Okafor", "p3", msisdn=m)
        self.inbound("ada.okafor@example.com", "p3e", msisdn=m)
        self.inbound("1111", "p4", msisdn=m)
        self.inbound("2222", "p5", msisdn=m)  # mismatch
        self.assertIn("match", self.last_reply(m).lower())
        self.assertFalse(User.objects.filter(phone="09090000003").exists())

    def test_onboarding_existing_account_is_directed_to_link(self):
        # MSISDN normalises to 08011112222; create that account first.
        User.objects.create(username="08011112222", phone="08011112222")
        self.inbound("1", "e1")  # default MSISDN
        self.assertIn("already has a Zitch account", self.last_reply())
        self.assertFalse(WaMessageLog.objects.filter(text__icontains="first name").exists())

    def test_whatsapp_user_can_send_without_bvn(self):
        # WhatsApp accounts transact immediately (capped at the unverified Tier-1
        # limits) — no BVN gate before sending.
        self.user.bvn_verified = False
        self.user.save(update_fields=["bvn_verified"])
        self.link()
        self.inbound("send", "b1")
        self.assertIn("how much", self.last_reply().lower())
        self.assertTrue(PendingAction.objects.filter(msisdn=MSISDN, action_type="transfer").exists())

    def test_frozen_user_is_blocked_on_whatsapp(self):
        # Freeze is the primary incident-response lever; it must cover WhatsApp
        # (which is link-bound, not token-bound) as well as the app.
        self.link()
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        before = self.balance()
        self.inbound("balance", "fz1")
        self.assertIn("suspended", self.last_reply().lower())
        self.inbound("send 5000", "fz2")
        self.assertIn("suspended", self.last_reply().lower())
        self.assertEqual(self.balance(), before)   # nothing transacted

    def test_link_code_links_number(self):
        # The code must be sent from the number on the user's Zitch account
        # (national significant number matches MSISDN's last 10 digits).
        self.user.phone = "08011112222"
        self.user.save(update_fields=["phone"])
        res = self.client.post("/api/whatsapp/link/start/",
                               data=json.dumps({"access_token": self.token}), content_type="application/json")
        code = res.json()["code"]
        self.assertGreaterEqual(len(code), 32)  # at least 128 bits, not a 24-bit brute-force code
        self.inbound(f"LINK {code}", "m1")
        link = WhatsAppLink.objects.get(wa_msisdn=MSISDN)
        self.assertEqual(link.status, WhatsAppLink.ACTIVE)
        self.assertEqual(link.user_id, self.user.id)
        self.assertIn("Linked", self.last_reply())

    def test_link_rejected_from_unregistered_number(self):
        # Default user phone (08010000001) does NOT match MSISDN — a leaked code
        # sent from another WhatsApp number must not bind the account.
        res = self.client.post("/api/whatsapp/link/start/",
                               data=json.dumps({"access_token": self.token}), content_type="application/json")
        code = res.json()["code"]
        self.inbound(f"LINK {code}", "m1")
        self.assertFalse(WhatsAppLink.objects.filter(status=WhatsAppLink.ACTIVE).exists())
        self.assertIn("your Zitch account", self.last_reply())

    def test_link_rejected_when_account_has_no_registered_phone(self):
        # `phone` is nullable: with no number on file there's nothing to match the
        # sender against, so the bind must FAIL CLOSED (else a leaked code from any
        # WhatsApp number would claim the account).
        res = self.client.post("/api/whatsapp/link/start/",
                               data=json.dumps({"access_token": self.token}), content_type="application/json")
        code = res.json()["code"]
        self.user.phone = None
        self.user.save(update_fields=["phone"])
        self.inbound(f"LINK {code}", "np1")
        self.assertFalse(WhatsAppLink.objects.filter(status=WhatsAppLink.ACTIVE).exists())
        self.assertIn("your Zitch account", self.last_reply())

    def test_frozen_account_cannot_execute_via_flow_pin(self):
        # Freeze bypass: the chat path blocks a frozen user, but the encrypted Flow
        # (native PIN pad) execution path must ALSO refuse — a transfer/VTU armed
        # BEFORE an admin freeze can't run through the Flow within the arm window.
        from .router import run_flow_execution

        self.link()
        before = self.balance()
        pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="airtime", state="flow_pin",
            payload={"amount": "500", "phone": "08030000000", "network": "MTN"},
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        out = run_flow_execution(pa, self.user)
        self.assertIn("suspended", out.lower())
        self.assertEqual(self.balance(), before)                       # no debit
        self.assertFalse(PendingAction.objects.filter(msisdn=MSISDN).exists())  # cleared

    # --- balance ---
    def test_balance(self):
        self.link()
        self.inbound("balance", "m1")
        self.assertIn("50,000.00", self.last_reply())

    # --- add money (dedicated reserved account) ---
    def test_add_money_shows_dedicated_account_for_verified_user(self):
        # A user with a provisioned Wema NUBAN sees it for bank-transfer funding.
        wallet = give_account(self.user)
        self.link()
        self.inbound("add money", "am1")
        reply = self.last_reply()
        self.assertIn(wallet.account_number, reply)
        self.assertIn("credited automatically", reply.lower())

    def test_add_money_menu_number_works(self):
        wallet = give_account(self.user)
        self.link()
        self.inbound("6", "am2")
        self.assertIn(wallet.account_number, self.last_reply())

    def test_add_money_without_account_points_to_app(self):
        # Wema account setup needs a BVN/NIN + OTP round-trip (done in the Zitch app),
        # so a user without an account is directed there — no in-chat BVN collection.
        self.user.bvn_verified = False
        self.user.nin_verified = False
        self.user.save(update_fields=["bvn_verified", "nin_verified"])
        self.link()
        self.inbound("fund", "am3")
        self.assertFalse(get_or_create_wallet(self.user).account_number)
        self.assertIn("app", self.last_reply().lower())
        self.assertFalse(PendingAction.objects.filter(
            msisdn=MSISDN, action_type="add_account").exists())

    def _run_transfer(self, pin="1234", start_mid="t"):
        self.link()
        self.inbound("2", f"{start_mid}1")               # send money
        self.inbound("5000", f"{start_mid}2")            # amount
        self.inbound("0123456789", f"{start_mid}3")      # account
        self.inbound("GTBank", f"{start_mid}4")          # bank -> name-enquiry -> confirm
        return self.inbound(pin, f"{start_mid}5")        # PIN

    def test_transfer_happy_path(self):
        self.link()
        self.inbound("2", "t1")
        self.assertIn("How much", self.last_reply())
        self.inbound("5000", "t2")
        self.assertIn("account number", self.last_reply())
        self.inbound("0123456789", "t3")
        self.assertIn("bank", self.last_reply().lower())
        self.inbound("GTBank", "t4")
        self.assertIn("Confirm transfer", self.last_reply())
        self.assertIn("ADEYEMI WILLIAM", self.last_reply())  # the BANK's name, not a typed one
        self.inbound("1234", "t5")
        self.assertIn("Transfer receipt", self.last_reply())
        self.assertEqual(self.balance(), Decimal("45000"))
        self.assertEqual(
            Transaction.objects.filter(user=self.user, direction=Transaction.OUT,
                                       transaction_status=Transaction.SUCCESS).count(), 1)

    def test_oneline_paste_transfer(self):
        self.link()
        self.inbound("0123456789 GTBank John Doe 5000", "p1")  # single bank match -> confirm
        self.assertIn("Confirm transfer", self.last_reply())
        self.inbound("1234", "p2")
        self.assertIn("Transfer receipt", self.last_reply())
        self.assertEqual(self.balance(), Decimal("45000"))

    def test_paste_amount_before_account(self):
        """"send 5k to 0123456789 gtbank" must read ₦5,000 — not the 10-digit account."""
        self.link()
        self.inbound("send 5k to 0123456789 gtbank", "pb1")
        self.assertIn("Confirm transfer", self.last_reply())
        self.assertIn("5,000.00", self.last_reply())
        self.inbound("1234", "pb2")
        self.assertEqual(self.balance(), Decimal("45000"))

    def test_duplicate_webhook_does_not_double_send(self):
        self._run_transfer()
        self.assertEqual(self.balance(), Decimal("45000"))
        # Meta re-delivers the PIN message (same id) -> deduped, no second debit.
        self.inbound("1234", "t5")
        self.assertEqual(self.balance(), Decimal("45000"))
        self.assertEqual(Transaction.objects.filter(user=self.user, direction=Transaction.OUT).count(), 1)
        self.assertIsNotNone(WaMessageLog.objects.get(wa_message_id="t5").processed_at)

    def test_failed_handler_releases_claim_so_meta_retry_is_not_lost(self):
        self.link()
        with patch("whatsapp.jobs.handle_inbound", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.inbound("balance", "retry-1")
        failed = WaMessageLog.objects.get(wa_message_id="retry-1")
        self.assertIsNone(failed.processed_at)
        self.assertIsNone(failed.processing_started_at)
        self.assertEqual(failed.processing_error, "RuntimeError")
        self.assertNotIn(b"balance", bytes(failed.processing_payload))
        failed.next_attempt_at = timezone.now() - timedelta(seconds=1)
        failed.save(update_fields=["next_attempt_at"])

        with patch("whatsapp.jobs.handle_inbound") as handler:
            response = self.inbound("balance", "retry-1")
        self.assertEqual(response.status_code, 200)
        handler.assert_called_once_with(MSISDN, "balance")
        retried = WaMessageLog.objects.get(wa_message_id="retry-1")
        self.assertIsNotNone(retried.processed_at)
        self.assertEqual(retried.processing_attempts, 2)
        self.assertEqual(bytes(retried.processing_payload), b"")

    def test_wrong_pin_cancels_after_retry(self):
        self.link()
        self.inbound("2", "w1"); self.inbound("5000", "w2")  # noqa: E702
        self.inbound("0123456789", "w3"); self.inbound("GTBank", "w4")  # noqa: E702
        self.inbound("0000", "w5")
        self.assertIn("PIN", self.last_reply())
        self.inbound("0000", "w6")  # second wrong -> cancel
        self.assertIn("cancelled", self.last_reply().lower())
        self.assertEqual(self.balance(), Decimal("50000"))  # never debited
        self.assertFalse(PendingAction.objects.filter(msisdn=MSISDN).exists())

    def test_pin_is_masked_in_log(self):
        self._run_transfer()
        pin_in = WaMessageLog.objects.get(wa_message_id="t5", direction=WaMessageLog.IN)
        self.assertEqual(pin_in.text, "[PIN]")
        # And the raw PIN appears nowhere in the inbound log.
        self.assertFalse(WaMessageLog.objects.filter(direction=WaMessageLog.IN, text="1234").exists())

    def test_cancel_clears_flow(self):
        self.link()
        self.inbound("2", "c1"); self.inbound("5000", "c2")  # noqa: E702
        self.inbound("cancel", "c3")
        self.assertIn("cancelled", self.last_reply().lower())
        self.assertFalse(PendingAction.objects.filter(msisdn=MSISDN).exists())


class VtuTests(TestCase):
    """Airtime / data / electricity / cable over the deterministic router."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user(balance="20000")
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)
        DataPlan.objects.create(network="1", plan_type="3", name="1GB", validity="30 days",
                                plan_code="mtn-1gb", price=Decimal("500"), active=True)
        CablePlan.objects.create(provider="2", name="DStv Compact", cable_plan_code="dstv-compact",
                                 price=Decimal("9000"), active=True)

    def inbound(self, text, mid):
        event = {"entry": [{"changes": [{"value": {"messages": [
            {"from": MSISDN, "id": mid, "type": "text", "text": {"body": text}}]}}]}]}
        return self.client.post("/webhooks/whatsapp", data=json.dumps(event), content_type="application/json")

    def last_reply(self):
        row = WaMessageLog.objects.filter(msisdn=MSISDN, direction=WaMessageLog.OUT).order_by("-created").first()
        return row.text if row else ""

    def bal(self):
        return get_or_create_wallet(self.user).balance

    def test_airtime(self):
        self.inbound("airtime", "a1")
        self.inbound("1", "a2")               # MTN -> ask phone
        self.inbound("08099998888", "a3")     # phone -> ask amount
        self.inbound("200", "a4")             # amount -> confirm
        self.assertIn("Confirm airtime", self.last_reply())
        self.inbound("1234", "a5")            # PIN
        self.assertIn("Airtime receipt", self.last_reply())
        self.assertEqual(self.bal(), Decimal("19800"))

    def test_data(self):
        self.inbound("data", "d1")
        self.inbound("1", "d2")               # MTN -> plan list
        self.assertIn("1GB", self.last_reply())
        self.inbound("1", "d3")               # pick plan -> ask phone
        self.inbound("me", "d4")              # phone -> confirm
        self.assertIn("Confirm data", self.last_reply())
        self.inbound("1234", "d5")
        self.assertIn("Data receipt", self.last_reply())
        self.assertEqual(self.bal(), Decimal("19500"))

    def test_electricity_returns_token(self):
        self.inbound("electricity", "e1")
        self.inbound("1", "e2")               # Ikeja -> meter type
        self.inbound("1", "e3")               # prepaid -> ask meter
        self.inbound("01234567890", "e4")     # meter -> validated -> ask amount
        self.assertIn("verified", self.last_reply())
        self.inbound("3000", "e5")            # amount -> confirm
        self.assertIn("Confirm electricity", self.last_reply())
        self.assertIn("ADEYEMI WILLIAM", self.last_reply())  # validated customer name
        self.inbound("1234", "e6")
        self.assertIn("Token", self.last_reply())            # prepaid token in the receipt
        self.assertEqual(self.bal(), Decimal("17000"))

    def test_cable(self):
        self.inbound("cable", "c1")
        self.inbound("2", "c2")               # DSTV -> package list
        self.assertIn("DStv Compact", self.last_reply())
        self.inbound("1", "c3")               # pick -> ask IUC
        self.inbound("1234567890", "c4")      # IUC -> validated -> confirm
        self.assertIn("Confirm cable", self.last_reply())
        self.inbound("1234", "c5")
        self.assertIn("Cable receipt", self.last_reply())
        self.assertEqual(self.bal(), Decimal("11000"))

    def test_wrong_network_reprompts(self):
        self.inbound("airtime", "n1")
        self.inbound("9", "n2")               # invalid network
        self.assertIn("network", self.last_reply().lower())
        self.assertEqual(self.bal(), Decimal("20000"))

    def test_typed_network_name_accepted(self):
        # A tapped list row sends the id ("1"), but over the text fallback users
        # type the name they can see — "MTN" must advance the flow, not re-prompt.
        self.inbound("airtime", "tn1")
        self.inbound("MTN", "tn2")
        self.assertIn("What phone number?", self.last_reply())

    def test_typed_provider_and_disco_names_accepted(self):
        self.inbound("cable", "tp1")
        self.inbound("dstv", "tp2")           # name, case-insensitive
        self.assertIn("DStv Compact", self.last_reply())
        self.inbound("cancel", "tp3")
        self.inbound("electricity", "td1")
        self.inbound("port harcourt", "td2")  # multi-word disco name
        self.assertIn("Prepaid or postpaid", self.last_reply())

    def test_airtime_data_menu_pick_by_number(self):
        # Main-menu "3" offers Airtime/Data as a numbered pick — the user selects
        # 1 or 2 (or taps the row) instead of typing the word.
        self.inbound("3", "p1")
        self.assertIn("Airtime", self.last_reply())
        self.assertIn("Data", self.last_reply())
        self.inbound("1", "p2")               # 1 -> Airtime -> ask network
        self.assertIn("network", self.last_reply().lower())
        self.inbound("1", "p3")               # MTN -> ask phone
        self.inbound("me", "p4")
        self.inbound("200", "p5")
        self.assertIn("Confirm airtime", self.last_reply())

    def test_airtime_data_menu_data_pick(self):
        self.inbound("3", "pd1")
        self.inbound("2", "pd2")              # 2 -> Data -> ask network
        self.assertIn("network", self.last_reply().lower())
        self.inbound("1", "pd3")              # MTN -> plan list
        self.assertIn("1GB", self.last_reply())

    def test_bill_menu_pick_by_number(self):
        # Main-menu "4" offers Electricity/Cable as a numbered pick, same pattern.
        self.inbound("4", "b1")
        self.assertIn("Electricity", self.last_reply())
        self.assertIn("Cable", self.last_reply())
        self.inbound("1", "b2")               # 1 -> Electricity -> disco prompt
        self.assertIn("disco", self.last_reply().lower())

    def test_service_menu_reprompts_on_bad_pick(self):
        self.inbound("3", "r1")
        self.inbound("banana", "r2")          # not a valid pick -> re-prompt, no crash
        self.assertIn("Airtime", self.last_reply())
        self.assertIn("Data", self.last_reply())

    def test_electricity_over_tier_limit_refused(self):
        # KYC tier limits apply to bills over chat too (not just transfers): a
        # tier-1 user (₦50k cap) buying ₦60k of electricity is stopped before PIN.
        w = get_or_create_wallet(self.user)
        w.balance = Decimal("200000")
        w.save()
        self.inbound("electricity", "l1")
        self.inbound("1", "l2")               # Ikeja
        self.inbound("1", "l3")               # prepaid
        self.inbound("01234567890", "l4")     # meter -> ask amount
        self.inbound("60000", "l5")           # over the tier-1 ₦50k limit
        self.assertIn("Tier", self.last_reply())
        self.assertEqual(self.bal(), Decimal("200000"))  # not debited


@override_settings(LLM={"API_KEY": "test-key", "MODEL": ""})
class AiIntentTests(TestCase):
    """LLM intent layer: free text -> structured intent -> the SAME flows.
    extract_intent is stubbed, so no real model call happens in tests."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user(balance="50000")
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN,
                                    status=WhatsAppLink.ACTIVE, ai_enabled=True)
        SystemSetting.set("ai_enabled_global", "true")
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#000", active=True)

    def inbound(self, text, mid):
        event = {"entry": [{"changes": [{"value": {"messages": [
            {"from": MSISDN, "id": mid, "type": "text", "text": {"body": text}}]}}]}]}
        return self.client.post("/webhooks/whatsapp", data=json.dumps(event), content_type="application/json")

    def last_reply(self):
        row = WaMessageLog.objects.filter(msisdn=MSISDN, direction=WaMessageLog.OUT).order_by("-created").first()
        return row.text if row else ""

    def _stub(self, intent):
        return patch("whatsapp.ai.extract_intent", return_value=intent)

    def test_freeform_balance(self):
        with self._stub({"name": "check_balance", "input": {}}):
            self.inbound("how much do I have?", "b1")
        self.assertIn("balance", self.last_reply().lower())

    def test_freeform_transfer_to_confirm(self):
        # No digits in the text -> the deterministic paste path can't handle it,
        # so this exercises the AI dispatch (stub supplies the details).
        with self._stub({"name": "transfer",
                         "input": {"amount": 5000, "account_number": "0123456789", "bank_name": "GTBank"}}):
            self.inbound("please send money to my gtbank account", "t1")
        self.assertIn("Confirm transfer", self.last_reply())
        self.assertIn("ADEYEMI WILLIAM", self.last_reply())  # bank-verified name

    def test_freeform_add_money(self):
        give_account(self.user)
        with self._stub({"name": "add_money", "input": {}}):
            self.inbound("how do I fund my wallet?", "f1")
        reply = self.last_reply()
        self.assertIn(get_or_create_wallet(self.user).account_number, reply)
        self.assertIn("credited automatically", reply.lower())

    def test_freeform_airtime_prefilled_confirm(self):
        with self._stub({"name": "buy_airtime",
                         "input": {"amount": 200, "phone": "08099998888", "network": "MTN"}}):
            self.inbound("load 200 mtn airtime for 08099998888", "a1")
        self.assertIn("Confirm airtime", self.last_reply())

    def test_freeform_airtime_fast_path_enforces_face_gate(self):
        # Regression: the AI-prefilled airtime fast-path skipped the >=₦100k face
        # step-up that the guided flow enforces. _run_vtu now gates every path, so a
        # Tier-3-without-face user can't buy >=₦100k airtime by going through the AI.
        self.user.tier = 3
        self.user.face_verified = False
        self.user.save(update_fields=["tier", "face_verified"])
        w = get_or_create_wallet(self.user)
        w.balance = Decimal("300000")
        w.save(update_fields=["balance"])
        with self._stub({"name": "buy_airtime",
                         "input": {"amount": 120000, "phone": "08099998888", "network": "MTN"}}):
            self.inbound("load 120k mtn airtime for 08099998888", "fg1")
        self.assertIn("Confirm airtime", self.last_reply())  # fast-path jumps to confirm
        self.inbound("1234", "fg2")                          # PIN -> reaches _run_vtu
        self.assertIn("Face verification", self.last_reply())
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("300000"))  # not debited

    def test_clarify_shows_menu(self):
        with self._stub({"name": "clarify", "input": {"reason": "unsupported"}}):
            self.inbound("tell me a joke", "c1")
        # Falls back to the main menu (which lists the core actions).
        self.assertIn("Check balance", self.last_reply())

    def test_parsed_intent_is_recorded(self):
        with self._stub({"name": "check_balance", "input": {}}):
            self.inbound("balance pls", "r1")
        row = WaMessageLog.objects.get(wa_message_id="r1", direction=WaMessageLog.IN)
        self.assertEqual(row.intent_json.get("name"), "check_balance")

    def test_sensitive_identifiers_are_redacted_from_chat_and_intent_logs(self):
        with self._stub({"name": "buy_airtime", "input": {
                "amount": 200, "phone": "08099998888", "network": "MTN"}}):
            self.inbound("load 200 for 08099998888", "pii-1")
        row = WaMessageLog.objects.get(wa_message_id="pii-1", direction=WaMessageLog.IN)
        self.assertNotIn("08099998888", row.text)
        self.assertEqual(row.intent_json["input"]["phone"], "[redacted]")

    def test_per_user_ai_off_is_deterministic(self):
        WhatsAppLink.objects.filter(wa_msisdn=MSISDN).update(ai_enabled=False)
        with self._stub({"name": "check_balance", "input": {}}) as m:
            self.inbound("how much do I have", "o1")
        m.assert_not_called()  # AI never consulted when the user's scope is off
        self.assertIn("didn't get that", self.last_reply())  # deterministic free-text fallback

    def test_global_kill_switch_is_deterministic(self):
        from .models import SystemSetting
        SystemSetting.set("ai_enabled_global", "false")
        with self._stub({"name": "check_balance", "input": {}}) as m:
            self.inbound("how much do I have", "g1")
        m.assert_not_called()
        # Deterministic keywords still work with AI globally off.
        self.inbound("balance", "g2")
        self.assertIn("50,000.00", self.last_reply())


class ConvertTests(TestCase):
    """Currency conversion over the router (NGN <-> USD/GBP/CAD; CNY blocked)."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user(balance="50000")
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)

    def inbound(self, text, mid):
        event = {"entry": [{"changes": [{"value": {"messages": [
            {"from": MSISDN, "id": mid, "type": "text", "text": {"body": text}}]}}]}]}
        return self.client.post("/webhooks/whatsapp", data=json.dumps(event), content_type="application/json")

    def last_reply(self):
        row = WaMessageLog.objects.filter(msisdn=MSISDN, direction=WaMessageLog.OUT).order_by("-created").first()
        return row.text if row else ""

    def test_convert_ngn_to_usd(self):
        self.inbound("convert", "x1")
        self.inbound("NGN", "x2")
        self.inbound("USD", "x3")
        self.inbound("16000", "x4")           # mock rate 1600 NGN/USD -> 10.00 USD
        self.assertIn("Confirm conversion", self.last_reply())
        self.assertIn("10.00 USD", self.last_reply())
        self.inbound("1234", "x5")            # PIN within TTL
        self.assertIn("Converted", self.last_reply())
        from wallet.forex import currency_balance
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("34000"))  # 50000 - 16000
        self.assertEqual(currency_balance(self.user, "USD"), Decimal("10.00"))

    def test_balance_shows_multicurrency(self):
        from wallet.models import CurrencyWallet
        CurrencyWallet.objects.create(user=self.user, currency="USD", balance=Decimal("10"))
        self.inbound("balance", "b1")
        r = self.last_reply()
        self.assertIn("USD", r)
        self.assertIn("50,000.00", r)

    def test_to_currency_rejects_unsupported(self):
        self.inbound("convert", "u1")
        self.inbound("NGN", "u2")
        self.inbound("CNY", "u3")             # not offered for settlement
        self.assertIn("NGN, USD, GBP", self.last_reply())


class ForexServiceTests(TestCase):
    def setUp(self):
        self.user, _ = make_user(balance="50000")

    def test_cny_is_quote_only(self):
        from wallet.forex import FxError, create_fx_quote
        with self.assertRaises(FxError) as cm:
            create_fx_quote(self.user, "NGN", "CNY", "1000")
        self.assertIn("display-only", cm.exception.message)

    def test_expired_quote_never_settles(self):
        from datetime import timedelta

        from django.utils import timezone

        from wallet.forex import FxError, create_fx_quote, execute_fx
        q = create_fx_quote(self.user, "NGN", "USD", "16000")
        q.expires_at = timezone.now() - timedelta(seconds=1)
        q.save(update_fields=["expires_at"])
        with self.assertRaises(FxError) as cm:
            execute_fx(self.user, q.quote_ref)
        self.assertIn("expired", cm.exception.message.lower())

    def test_fx_blocked_over_daily_transfer_cap(self):
        # An NGN→foreign conversion counts against the daily transfer ceiling, so a
        # conversion that would push the day's NGN outflow past the cap is refused
        # (amount kept under ₦100k to isolate the daily cap from the face gate).
        from wallet.forex import FxError, create_fx_quote
        from wallet.models import Transaction
        from wallet.services import get_or_create_wallet
        self.user.tier = 2  # per-txn ₦200k, daily transfer ₦1,000,000
        self.user.save(update_fields=["tier"])
        w = get_or_create_wallet(self.user)
        w.balance = Decimal("2000000")
        w.save(update_fields=["balance"])
        Transaction.objects.create(
            user=self.user, service="Transfer to Seed", amount=Decimal("950000"),
            direction=Transaction.OUT, transaction_status=Transaction.SUCCESS,
            reference="SEED-FXCAP", currency="NGN")
        with self.assertRaises(FxError) as cm:  # 950k + 90k > 1M
            create_fx_quote(self.user, "NGN", "USD", "90000")
        self.assertIn("daily", cm.exception.message.lower())
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("2000000"))  # quote never debits

    def test_used_quote_not_resettled(self):
        from wallet.forex import FxError, create_fx_quote, currency_balance, execute_fx
        q = create_fx_quote(self.user, "NGN", "USD", "16000")
        execute_fx(self.user, q.quote_ref)
        self.assertEqual(currency_balance(self.user, "USD"), Decimal("10.00"))
        with self.assertRaises(FxError):
            execute_fx(self.user, q.quote_ref)        # already used
        self.assertEqual(currency_balance(self.user, "USD"), Decimal("10.00"))  # not doubled
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("34000"))


class OperatorTests(TestCase):
    """STOP opt-out, human handover, broadcasts, audit, staff gating (§9-§11)."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user(balance="10000")
        # Operator with the `support` role (wa + broadcast caps) — ops endpoints
        # are role-gated, not merely is_staff-gated.
        from django.contrib.auth.models import Group

        self.staff = User.objects.create(username="adm", phone="08099999999", email="adm@zitch.test", is_staff=True)
        group, _ = Group.objects.get_or_create(name="support")
        self.staff.groups.add(group)
        self.staff_token = AccessToken.issue(self.staff, scope=AccessToken.ADMIN).key
        self.checker = User.objects.create(
            username="checker", phone="08099999998", email="checker@zitch.test", is_staff=True,
        )
        self.checker.groups.add(group)
        self.checker_token = AccessToken.issue(self.checker, scope=AccessToken.ADMIN).key

    def inbound(self, text, mid):
        event = {"entry": [{"changes": [{"value": {"messages": [
            {"from": MSISDN, "id": mid, "type": "text", "text": {"body": text}}]}}]}]}
        return self.client.post("/webhooks/whatsapp", data=json.dumps(event), content_type="application/json")

    def last_reply(self):
        row = WaMessageLog.objects.filter(msisdn=MSISDN, direction=WaMessageLog.OUT).order_by("-created").first()
        return row.text if row else ""

    def post_as(self, path, token, payload):
        body = {"access_token": token, **payload}
        res = self.client.post(path, data=json.dumps(body), content_type="application/json")
        return res, res.json()

    def approve_and_send(self, approval_id):
        res, body = self.post_as(
            "/api/admin/approvals/decide", self.checker_token,
            {"id": approval_id, "approve": True},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["status"], "executed")
        from .jobs import process_outbound_batch
        process_outbound_batch()
        return Broadcast.objects.get(pk=body["result"]["broadcast_id"])

    def test_stop_unsubscribes_marketing(self):
        link = WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN,
                                           status=WhatsAppLink.ACTIVE, marketing_opt_in=True)
        self.inbound("stop", "s1")
        link.refresh_from_db()
        self.assertFalse(link.marketing_opt_in)
        self.assertIn("unsubscribed", self.last_reply().lower())

    def test_human_handover_silences_bot(self):
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)
        ConversationState.objects.create(msisdn=MSISDN, status=ConversationState.HUMAN, ai_enabled=False)
        before = WaMessageLog.objects.filter(msisdn=MSISDN, direction=WaMessageLog.OUT).count()
        self.inbound("balance", "h1")          # bot must not auto-reply
        after = WaMessageLog.objects.filter(msisdn=MSISDN, direction=WaMessageLog.OUT).count()
        self.assertEqual(before, after)

    def test_handover_endpoint_pauses_ai(self):
        res, body = self.post_as("/api/whatsapp/ops/handover/", self.staff_token, {"msisdn": MSISDN})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["status"], "human")
        convo = ConversationState.objects.get(msisdn=MSISDN)
        self.assertFalse(convo.ai_enabled)
        self.assertEqual(convo.assigned_agent_id, self.staff.id)
        self.assertTrue(AuditLog.objects.filter(action="conversation.handover").exists())

    def test_ops_requires_staff(self):
        # A normal user's app-scoped token is refused at the scope gate (401),
        # before any staff/role check — it can never reach the ops surface.
        res, _ = self.post_as("/api/whatsapp/ops/handover/", self.token, {"msisdn": MSISDN})  # normal user
        self.assertEqual(res.status_code, 401)

    def test_ops_requires_role_capability(self):
        """A staff account with no role group is read_only: no wa/broadcast caps."""
        bare = User.objects.create(username="ro", phone="08098888888", email="ro@zitch.test", is_staff=True)
        bare_token = AccessToken.issue(bare, scope=AccessToken.ADMIN).key
        res, _ = self.post_as("/api/whatsapp/ops/handover/", bare_token, {"msisdn": MSISDN})
        self.assertEqual(res.status_code, 403)
        res, _ = self.post_as("/api/whatsapp/ops/broadcast/", bare_token, {"template_name": "promo"})
        self.assertEqual(res.status_code, 403)

    def test_finance_role_cannot_broadcast(self):
        """The finance role has money/users caps but not wa/broadcast."""
        from django.contrib.auth.models import Group

        fin = User.objects.create(username="fin", phone="08097777777", email="fin@zitch.test", is_staff=True)
        fin.groups.add(Group.objects.get_or_create(name="finance")[0])
        fin_token = AccessToken.issue(fin, scope=AccessToken.ADMIN).key
        res, _ = self.post_as("/api/whatsapp/ops/broadcast/", fin_token, {"template_name": "promo"})
        self.assertEqual(res.status_code, 403)
        res, _ = self.post_as("/api/whatsapp/ops/reply/", fin_token, {"msisdn": MSISDN, "text": "hi"})
        self.assertEqual(res.status_code, 403)

    def test_broadcast_only_opted_in(self):
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN,
                                    status=WhatsAppLink.ACTIVE, marketing_opt_in=True)
        bob, _ = make_user("08020000002", "bob@zitch.test")
        WhatsAppLink.objects.create(user=bob, wa_msisdn="2348020000002",
                                    status=WhatsAppLink.ACTIVE, marketing_opt_in=False)
        res, body = self.post_as("/api/whatsapp/ops/broadcast/", self.staff_token,
                                 {"template_name": "promo", "category": "marketing"})
        self.assertEqual(res.status_code, 202)
        broadcast = self.approve_and_send(body["approval_id"])
        self.assertEqual(broadcast.count_queued, 1)  # only the opted-in user
        self.assertEqual(broadcast.count_sent, 1)
        recips = BroadcastRecipient.objects.all()
        self.assertEqual(recips.count(), 1)
        self.assertEqual(recips.first().wa_msisdn, MSISDN)
        self.assertTrue(AuditLog.objects.filter(action="broadcast.completed").exists())

    def test_utility_broadcast_ignores_opt_in(self):
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN,
                                    status=WhatsAppLink.ACTIVE, marketing_opt_in=False)
        res, body = self.post_as("/api/whatsapp/ops/broadcast/", self.staff_token,
                                 {"template_name": "txn_alert", "category": "utility"})
        broadcast = self.approve_and_send(body["approval_id"])
        self.assertEqual(broadcast.count_sent, 1)  # utility reaches non-opted-in users

    def test_ambiguous_template_send_is_not_blindly_retried(self):
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN,
                                    status=WhatsAppLink.ACTIVE)
        _, body = self.post_as("/api/whatsapp/ops/broadcast/", self.staff_token,
                               {"template_name": "txn_alert", "category": "utility"})
        decision, decision_body = self.post_as(
            "/api/admin/approvals/decide", self.checker_token,
            {"id": body["approval_id"], "approve": True},
        )
        self.assertEqual(decision.status_code, 200)
        broadcast = Broadcast.objects.get(pk=decision_body["result"]["broadcast_id"])

        from .jobs import process_outbound_batch
        with patch("whatsapp.jobs.send_template", return_value={
            "success": False, "uncertain": True, "message": "unknown",
        }) as sender:
            self.assertEqual(process_outbound_batch(), 1)
            self.assertEqual(process_outbound_batch(), 0)
        sender.assert_called_once()
        recipient = broadcast.recipients.get()
        self.assertEqual(recipient.status, BroadcastRecipient.UNKNOWN)
        self.assertIsNotNone(recipient.processed_at)
        broadcast.refresh_from_db()
        self.assertEqual(broadcast.count_unknown, 1)
        self.assertEqual(broadcast.status, Broadcast.DONE)

    def test_sent_delivery_callback_advances_recipient(self):
        broadcast = Broadcast.objects.create(
            template_name="txn_alert", category=Broadcast.UTILITY,
            status=Broadcast.DONE, count_queued=1, count_unknown=1,
        )
        recipient = BroadcastRecipient.objects.create(
            broadcast=broadcast, user=self.user, wa_msisdn=MSISDN,
            status=BroadcastRecipient.UNKNOWN, wa_message_id="wamid.sent-1",
            processed_at=timezone.now(),
        )
        event = {"entry": [{"changes": [{"value": {"statuses": [{
            "id": "wamid.sent-1", "status": "sent",
        }]}}]}]}
        response = self.client.post("/webhooks/whatsapp", data=json.dumps(event),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        recipient.refresh_from_db()
        broadcast.refresh_from_db()
        self.assertEqual(recipient.status, BroadcastRecipient.SENT)
        self.assertEqual(broadcast.count_sent, 1)
        self.assertEqual(broadcast.count_unknown, 0)


class SmsCodeConfirmTests(TestCase):
    """With live SMS configured, money flows confirm with a SINGLE-USE 6-digit
    code sent by SMS — the chat never carries the transaction PIN."""

    def setUp(self):
        # Mirror VtuTests' setup by reusing its fixtures via a fresh instance.
        self._vt = VtuTests("test_airtime")
        self._vt.setUp()
        self.user = self._vt.user
        self.client = self._vt.client
        self.inbound = self._vt.inbound
        self.last_reply = self._vt.last_reply
        self.bal = self._vt.bal

    @override_settings(TERMII={"BASE_URL": "https://v3.api.termii.com", "API_KEY": "tk-key",
                               "SENDER_ID": "Zitch", "CHANNEL": "dnd"})
    def test_confirm_uses_sms_code_not_pin(self):
        sent = {}
        with patch("whatsapp.router.send_sms",
                   return_value={"success": True}) as sms:
            self.inbound("airtime", "s1")
            self.inbound("1", "s2")
            self.inbound("08099998888", "s3")
            self.inbound("200", "s4")
            self.assertIn("6-digit code", self.last_reply())
            sent["msg"] = sms.call_args[0][1]
        code = re.search(r"\b(\d{6})\b", sent["msg"]).group(1)
        # The PIN must NOT confirm while a code is armed…
        self.inbound("1234", "s5")
        self.assertIn("isn't right", self.last_reply())
        # …the SMS code does.
        self.inbound(code, "s6")
        self.assertIn("Airtime receipt", self.last_reply())
        self.assertEqual(self.bal(), Decimal("19800"))


class ProductionConfirmSafetyTests(TestCase):
    @override_settings(
        DEBUG=False,
        TESTING=False,
        # Both halves of the premise are pinned: production, and no SMS rail to fall
        # back to. An empty key here rather than an absent one, so the test keeps
        # meaning what it says if a Termii key ever reaches the test environment.
        TERMII={"BASE_URL": "", "API_KEY": "", "SENDER_ID": "Zitch", "CHANNEL": "dnd"},
    )
    def test_production_never_falls_back_to_requesting_pin_in_chat(self):
        from whatsapp.router import _arm_confirm, _confirm_prompt

        user, _ = make_user()
        action = PendingAction.objects.create(
            user=user,
            msisdn=MSISDN,
            action_type="airtime",
            state="amount",
            payload={"amount": "200"},
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        self.assertFalse(_arm_confirm(action, user))
        self.assertFalse(PendingAction.objects.filter(pk=action.pk).exists())
        self.assertNotIn("your PIN", _confirm_prompt(action))
        self.assertIn("Zitch app", _confirm_prompt(action))


class AuditLogImmutabilityTests(TestCase):
    """The audit trail is append-only at the ORM layer: a back-office bug or a
    compromised operator must not be able to rewrite or delete history."""

    def test_existing_row_cannot_be_edited(self):
        row = AuditLog.objects.create(actor_type="admin", actor_id="1",
                                      action="wallet.manual_credit", target="u_1")
        row.action = "tampered"
        with self.assertRaises(ValueError):
            row.save()
        self.assertEqual(AuditLog.objects.get(pk=row.pk).action, "wallet.manual_credit")

    def test_row_cannot_be_deleted(self):
        row = AuditLog.objects.create(actor_type="admin", actor_id="1", action="user.freeze")
        with self.assertRaises(ValueError):
            row.delete()
        self.assertTrue(AuditLog.objects.filter(pk=row.pk).exists())


try:
    import PIL  # noqa: F401
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False


class WhatsAppReceiptTests(TestCase):
    """The transaction receipt is a branded JPEG sent as an inline image when the
    channel is live, degrading to a document and then to text."""

    @unittest.skipUnless(_HAS_PIL, "Pillow not installed")
    def test_render_receipt_is_a_jpeg(self):
        from whatsapp.receipt import render_receipt
        b = render_receipt("Airtime receipt",
                           [("Network", "MTN"), ("Amount", "₦500.00")], "ZTC-1")
        self.assertEqual(b[:2], b"\xff\xd8")   # JPEG start-of-image marker
        self.assertGreater(len(b), 1000)

    @unittest.skipUnless(_HAS_PIL, "Pillow not installed")
    def test_reply_receipt_sends_an_inline_image_when_live(self):
        """An image renders in the thread and can be forwarded or saved to the
        gallery in one gesture; a document arrives as a file card that has to be
        opened first. The receipt is something people SHOW someone, so it is sent
        as an image and the document is only the fallback."""
        from whatsapp import providers
        from whatsapp.router import reply_receipt
        with patch.object(providers, "wa_live", return_value=True), \
             patch.object(providers, "upload_media", return_value="mid-123") as up, \
             patch.object(providers, "send_image_media", return_value={"success": True}) as si, \
             patch.object(providers, "send_document") as sd:
            text = reply_receipt("2348011112222", "Airtime receipt",
                                 [("Network", "MTN"), ("Amount", "₦500.00")], ref="ZTC-1")
        up.assert_called_once()
        si.assert_called_once()
        sd.assert_not_called()
        self.assertEqual(si.call_args.args[1], "mid-123")
        self.assertIn("Airtime receipt", si.call_args.kwargs.get("caption", ""))
        self.assertIn("Airtime receipt", text)

    @unittest.skipUnless(_HAS_PIL, "Pillow not installed")
    def test_reply_receipt_falls_back_to_a_document_if_the_image_is_refused(self):
        from whatsapp import providers
        from whatsapp.router import reply_receipt
        with patch.object(providers, "wa_live", return_value=True), \
             patch.object(providers, "upload_media", return_value="mid-123"), \
             patch.object(providers, "send_image_media", return_value={"success": False}), \
             patch.object(providers, "send_document", return_value={"success": True}) as sd, \
             patch("whatsapp.router.send_text") as st:
            reply_receipt("2348011112222", "Airtime receipt", [("Network", "MTN")], ref="ZTC-1")
        sd.assert_called_once()
        self.assertTrue(sd.call_args.args[2].endswith(".jpg"))   # filename
        st.assert_not_called()

    @unittest.skipUnless(_HAS_PIL, "Pillow not installed")
    def test_reply_receipt_still_sends_text_when_the_media_upload_fails(self):
        """A completed transaction always produces a receipt. If Meta's media store
        refuses the upload, the user gets the text one rather than nothing."""
        from whatsapp import providers
        from whatsapp.router import reply_receipt
        with patch.object(providers, "wa_live", return_value=True), \
             patch.object(providers, "upload_media", return_value=""), \
             patch.object(providers, "send_image_media") as si, \
             patch("whatsapp.router.send_text") as st:
            reply_receipt("2348011112222", "Airtime receipt", [("Network", "MTN")], ref="ZTC-1")
        si.assert_not_called()
        st.assert_called_once()

    def test_reply_receipt_text_fallback_in_mock(self):
        from whatsapp.router import reply_receipt
        text = reply_receipt("2348011112222", "Airtime receipt", [("Network", "MTN")], ref="ZTC-1")
        self.assertIn("Airtime receipt", text)
        self.assertTrue(WaMessageLog.objects.filter(
            msisdn="2348011112222", text__icontains="Airtime receipt").exists())


class WhatsAppHealthTests(TestCase):
    """"The bot isn't replying" is a symptom with several unrelated causes. These
    pin the one thing that separates them: whether inbound work is being drained."""

    def _queue(self, msisdn=MSISDN, mid="stuck-1", age_minutes=0):
        from whatsapp.models import WebhookEvent

        # A queue row cannot exist without a callback having arrived, and the
        # verdict checks reachability first — so the fixture has to record the
        # call too, or it describes a state production can never be in.
        WebhookEvent.objects.create(source="whatsapp", verified=True, http_status=200)
        row = WaMessageLog.objects.create(
            msisdn=msisdn, direction=WaMessageLog.IN, wa_message_id=mid, text="hello",
        )
        if age_minutes:
            WaMessageLog.objects.filter(pk=row.pk).update(
                created=timezone.now() - timedelta(minutes=age_minutes))
        return row

    def test_a_quiet_healthy_channel_says_so(self):
        from whatsapp.health import whatsapp_diagnostics
        report = whatsapp_diagnostics()
        self.assertEqual(report["queue"]["unprocessed"], 0)
        self.assertFalse(report["queue"]["worker_appears_stalled"])

    def test_an_old_backlog_names_the_worker(self):
        """The webhook keeps answering 200 while the worker is down, so from every
        other angle the channel looks healthy. The age of the oldest unprocessed
        message is the only signal that says otherwise."""
        from whatsapp.health import whatsapp_diagnostics
        self._queue(age_minutes=9)
        with patch("whatsapp.providers.wa_mode", return_value="live"), \
             patch("whatsapp.providers.wa_live", return_value=True):
            report = whatsapp_diagnostics()
        self.assertEqual(report["queue"]["unprocessed"], 1)
        self.assertTrue(report["queue"]["worker_appears_stalled"])
        self.assertIn("processing_error", report["verdict"])
        self.assertIn("zitch-whatsapp-worker", report["verdict"])

    def test_a_message_that_just_arrived_is_not_called_a_stall(self):
        from whatsapp.health import whatsapp_diagnostics
        self._queue()
        with patch("whatsapp.providers.wa_mode", return_value="live"), \
             patch("whatsapp.providers.wa_live", return_value=True):
            report = whatsapp_diagnostics()
        self.assertFalse(report["queue"]["worker_appears_stalled"])
        self.assertIn("draining", report["verdict"])

    def test_a_handover_is_reported_rather_than_looking_like_an_outage(self):
        from whatsapp.health import whatsapp_diagnostics
        from whatsapp.models import WebhookEvent

        WebhookEvent.objects.create(source="whatsapp", verified=True, http_status=200)
        ConversationState.objects.create(msisdn=MSISDN, status=ConversationState.HUMAN)
        with patch("whatsapp.providers.wa_mode", return_value="live"), \
             patch("whatsapp.providers.wa_live", return_value=True):
            report = whatsapp_diagnostics()
        self.assertIn(MSISDN, report["handed_to_human"])
        self.assertIn("human agent", report["verdict"])

    def test_sandbox_mode_is_named_as_the_reason_nothing_is_sent(self):
        from whatsapp.health import whatsapp_diagnostics
        with patch("whatsapp.providers.wa_mode", return_value="sandbox"), \
             patch("whatsapp.providers.wa_live", return_value=False):
            self.assertIn("SANDBOX", whatsapp_diagnostics()["verdict"])

    def test_the_report_never_carries_a_token(self):
        from whatsapp.health import whatsapp_diagnostics
        with override_settings(WHATSAPP={"MODE": "live", "TOKEN": "wa_supersecret",
                                         "APP_SECRET": "s", "BASE_URL": "x",
                                         "PHONE_NUMBER_ID": "p", "VERIFY_TOKEN": "v",
                                         "BUSINESS_NUMBER": "2348000000000"}):
            self.assertNotIn("wa_supersecret", json.dumps(whatsapp_diagnostics()))


class WhatsAppQueueAdminTests(TestCase):
    """The operator has no shell. Draining a stuck message and un-muting a handed-over
    conversation both have to be possible from the admin changelist."""

    def setUp(self):
        self.client = Client()
        # Low-entropy on purpose: a realistic-looking fixture password trips the
        # secret scanner, and a test credential is not worth teaching it to ignore
        # things. Matches the style used elsewhere (portal.tests).
        self.staff = User.objects.create_superuser(
            username="ops", email="ops@zitch.test", password="pw-ops-1")
        self.user, _ = make_user()
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN,
                                    status=WhatsAppLink.ACTIVE)
        self.client.force_login(self.staff)

    @override_settings(WHATSAPP_PROCESS_INLINE=False)
    def test_process_now_replies_to_a_message_the_worker_never_picked_up(self):
        event = {"entry": [{"changes": [{"value": {"messages": [
            {"from": MSISDN, "id": "stuck-9", "type": "text", "text": {"body": "balance"}}]}}]}]}
        self.client.post("/webhooks/whatsapp", data=json.dumps(event),
                         content_type="application/json")
        row = WaMessageLog.objects.get(wa_message_id="stuck-9")
        self.assertIsNone(row.processed_at)

        self.client.post("/admin/whatsapp/wamessagelog/",
                         {"action": "process_now", "_selected_action": [str(row.pk)]},
                         follow=True)
        row.refresh_from_db()
        self.assertIsNotNone(row.processed_at)
        self.assertTrue(WaMessageLog.objects.filter(
            msisdn=MSISDN, direction=WaMessageLog.OUT).exists())

    def test_return_to_bot_un_mutes_a_conversation_and_is_audited(self):
        convo = ConversationState.objects.create(
            msisdn=MSISDN, status=ConversationState.HUMAN, ai_enabled=False)
        self.client.post("/admin/whatsapp/conversationstate/",
                         {"action": "return_to_bot", "_selected_action": [str(convo.pk)]},
                         follow=True)
        convo.refresh_from_db()
        self.assertEqual(convo.status, ConversationState.BOT)
        self.assertTrue(convo.ai_enabled)
        self.assertTrue(AuditLog.objects.filter(
            action="conversation.return_to_bot", target=f"wa:{MSISDN}").exists())

    @override_settings(WHATSAPP_PROCESS_INLINE=False)
    def test_a_message_that_cannot_be_queued_leaves_a_trace(self):
        """The worst version of "the bot isn't responding": nothing is stored, so the
        queue reads empty and every health check says the channel is idle and fine."""
        from whatsapp.models import WebhookEvent

        event = {"entry": [{"changes": [{"value": {"messages": [
            {"from": MSISDN, "id": "unstorable-1", "type": "text",
             "text": {"body": "balance"}}]}}]}]}
        with patch("whatsapp.jobs.enqueue_inbound", side_effect=RuntimeError("no queue key")):
            with self.assertRaises(RuntimeError):
                self.client.post("/webhooks/whatsapp", data=json.dumps(event),
                                 content_type="application/json")
        self.assertTrue(WebhookEvent.objects.filter(
            source="whatsapp", http_status=500,
            action="enqueue_failed:RuntimeError").exists())


class WhatsAppWebDrainTests(TestCase):
    """Replies must not depend on one background service nobody can see failing.

    The webhook stores and acks; when the worker is gone that ack is the whole
    interaction, and the customer gets silence from a channel that reports 200 to
    Meta. These pin the web service's own drain — the thing that makes a reply
    arrive whether or not the worker exists.
    """

    def setUp(self):
        self.client = Client()
        self.user, _ = make_user()
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN,
                                    status=WhatsAppLink.ACTIVE)

    def _inbound(self, body="balance", mid="drain-1"):
        event = {"entry": [{"changes": [{"value": {"messages": [
            {"from": MSISDN, "id": mid, "type": "text", "text": {"body": body}}]}}]}]}
        return self.client.post("/webhooks/whatsapp", data=json.dumps(event),
                                content_type="application/json")

    @override_settings(WHATSAPP_PROCESS_INLINE=False)
    def test_a_reply_arrives_with_no_worker_running(self):
        """The production path, with the worker service absent — which is how it
        behaves when it was never created, crashed at boot, or was OOM-killed."""
        from whatsapp.jobs import _drain_worker

        # Run the drain body synchronously so the test observes its effect rather
        # than racing a daemon thread; the threading wrapper is covered below.
        with patch("whatsapp.jobs.threading.Thread") as thread:
            response = self._inbound()
        self.assertEqual(response.status_code, 200)
        thread.assert_called_once()
        self.assertTrue(thread.call_args.kwargs["daemon"])

        _drain_worker(5)
        row = WaMessageLog.objects.get(wa_message_id="drain-1")
        self.assertIsNotNone(row.processed_at)
        self.assertTrue(WaMessageLog.objects.filter(
            msisdn=MSISDN, direction=WaMessageLog.OUT).exists())

    @override_settings(WHATSAPP_PROCESS_INLINE=False)
    def test_the_webhook_is_acknowledged_even_if_the_drain_cannot_start(self):
        """The drain is a safety net. Meta's acknowledgement is the contract, and
        it must not depend on the net — a thread that cannot start (process thread
        limit) must not turn into a 500 and a redelivery loop."""
        with patch("whatsapp.jobs.threading.Thread", side_effect=RuntimeError("no threads")):
            response = self._inbound(mid="drain-2")
        self.assertEqual(response.status_code, 200)
        # Still durably queued: the worker or the next webhook picks it up.
        self.assertIsNone(WaMessageLog.objects.get(wa_message_id="drain-2").processed_at)

    def test_only_one_drain_runs_at_a_time_per_process(self):
        """Unbounded drains would let a burst of inbound messages consume every
        HTTP thread in the web process."""
        from whatsapp import jobs

        self.assertTrue(jobs._DRAIN_LOCK.acquire(blocking=False))
        try:
            with patch("whatsapp.jobs.threading.Thread") as thread:
                self.assertFalse(jobs.drain_in_background())
            thread.assert_not_called()
        finally:
            jobs._DRAIN_LOCK.release()

    @override_settings(WHATSAPP_WEB_DRAIN=False)
    def test_the_safety_net_can_be_switched_off(self):
        from whatsapp.jobs import drain_in_background

        with patch("whatsapp.jobs.threading.Thread") as thread:
            self.assertFalse(drain_in_background())
        thread.assert_not_called()

    def test_a_failing_drain_releases_its_lock_and_closes_its_connection(self):
        """A safety net that leaks the lock disables itself permanently after one
        bad message; one that leaks connections exhausts Postgres instead."""
        from whatsapp import jobs

        with patch("whatsapp.jobs.process_inbound_batch", side_effect=RuntimeError("boom")), \
             patch("django.db.connections.close_all") as close_all:
            jobs._DRAIN_LOCK.acquire()
            jobs._drain_worker(5)
        close_all.assert_called_once()
        self.assertTrue(jobs._DRAIN_LOCK.acquire(blocking=False))
        jobs._DRAIN_LOCK.release()


class WhatsAppWebhookReachabilityTests(TestCase):
    """The one cause no other number can show: Meta never reached us. A callback
    that was never called leaves no queue row, so every other reading says the
    channel is idle and healthy."""

    def test_never_being_called_is_named_rather_than_read_as_healthy(self):
        from whatsapp.health import whatsapp_diagnostics

        with patch("whatsapp.providers.wa_mode", return_value="live"), \
             patch("whatsapp.providers.wa_live", return_value=True):
            report = whatsapp_diagnostics()
        self.assertFalse(report["webhook"]["ever_accepted_a_call"])
        self.assertIn("never successfully called", report["verdict"])

    def test_a_wrong_app_secret_is_named_rather_than_looking_like_no_traffic(self):
        from whatsapp.health import whatsapp_diagnostics
        from whatsapp.models import WebhookEvent

        WebhookEvent.objects.create(source="whatsapp", verified=False,
                                    outcome=WebhookEvent.REJECTED_SIGNATURE,
                                    http_status=401)
        with patch("whatsapp.providers.wa_mode", return_value="live"), \
             patch("whatsapp.providers.wa_live", return_value=True):
            report = whatsapp_diagnostics()
        self.assertEqual(report["webhook"]["rejected_signature"], 1)
        self.assertIn("WHATSAPP_APP_SECRET", report["verdict"])

    def test_an_accepted_call_moves_the_verdict_past_the_webhook(self):
        from whatsapp.health import whatsapp_diagnostics
        from whatsapp.models import WebhookEvent

        WebhookEvent.objects.create(source="whatsapp", verified=True, http_status=200)
        with patch("whatsapp.providers.wa_mode", return_value="live"), \
             patch("whatsapp.providers.wa_live", return_value=True):
            report = whatsapp_diagnostics()
        self.assertTrue(report["webhook"]["ever_accepted_a_call"])
        self.assertIn("processing inbound messages", report["verdict"])


class WrongPhoneNumberIdTests(TestCase):
    """A signed call naming an unknown phone-number id is the one failure the boot
    guards cannot catch: they prove WHATSAPP_PHONE_NUMBER_ID is SET, never that it is
    the id of the number being messaged. The webhook records such a call verified=True
    and drops it with a 400, so any health read keyed on `verified` reports a healthy
    channel while every single message is being refused."""

    def _call(self, outcome, action=""):
        from .models import WebhookEvent

        WebhookEvent.objects.create(source="whatsapp", outcome=outcome,
                                    verified=True, action=action)

    def test_reached_is_false_when_every_call_is_refused_on_the_id(self):
        from . import health
        from .models import WebhookEvent

        self._call(WebhookEvent.BAD_BODY, "invalid_phone_number_id")
        snap = health.whatsapp_diagnostics()
        self.assertFalse(snap["webhook"]["ever_accepted_a_call"])
        self.assertEqual(snap["webhook"]["rejected_wrong_phone_number_id"], 1)

    def test_healthz_agrees_with_the_snapshot(self):
        from .models import WebhookEvent

        self._call(WebhookEvent.BAD_BODY, "invalid_phone_number_id")
        reached = Client().get("/healthz").json()["integrations"]["whatsapp_webhook_reached"]
        self.assertFalse(reached)

    def test_one_accepted_call_flips_both_to_true(self):
        from . import health
        from .models import WebhookEvent

        self._call(WebhookEvent.BAD_BODY, "invalid_phone_number_id")
        self._call(WebhookEvent.ACCEPTED)
        self.assertTrue(health.whatsapp_diagnostics()["webhook"]["ever_accepted_a_call"])
        self.assertTrue(Client().get("/healthz").json()
                        ["integrations"]["whatsapp_webhook_reached"])

    def test_verdict_names_the_phone_number_id_not_the_subscription(self):
        from . import health

        verdict = health._verdict("live", 0, False, 0, [], accepted_ever=False,
                                  wrong_number_id=4)
        self.assertIn("WHATSAPP_PHONE_NUMBER_ID", verdict)
        self.assertNotIn("SUBSCRIBED", verdict)

    def test_a_genuinely_unreached_webhook_still_names_the_subscription(self):
        from . import health

        verdict = health._verdict("live", 0, False, 0, [], accepted_ever=False,
                                  wrong_number_id=0)
        self.assertIn("SUBSCRIBED", verdict)


class OutboundSendFailureTests(TestCase):
    """A rejected Graph send (expired WHATSAPP_TOKEN, recipient not on the app's
    allowed-testers list, etc.) never raises — reply() returns normally, so the
    inbound job marks the message processed and no backlog ever forms. Without
    recording the failure on the OUT row, it left no trace anywhere: not in the
    queue, not in the logs, not in whatsapp_diagnostics(). This is the failure
    that made "Meta reached us, everything reports healthy, user gets nothing"
    possible even with the right APP_SECRET and PHONE_NUMBER_ID."""

    def test_reply_records_the_error_on_the_out_row_when_the_send_is_rejected(self):
        from whatsapp.router import reply

        with patch("whatsapp.router.send_text",
                   return_value={"success": False, "error_code": 190}):
            reply(MSISDN, "hi")
        row = WaMessageLog.objects.get(msisdn=MSISDN, direction=WaMessageLog.OUT)
        self.assertEqual(row.processing_error, "190")

    def test_reply_leaves_the_out_row_clean_when_the_send_succeeds(self):
        from whatsapp.router import reply

        with patch("whatsapp.router.send_text",
                   return_value={"success": True, "message_id": "wamid.1"}):
            reply(MSISDN, "hi")
        row = WaMessageLog.objects.get(msisdn=MSISDN, direction=WaMessageLog.OUT)
        self.assertEqual(row.processing_error, "")

    def test_diagnostics_count_recent_send_failures(self):
        from . import health

        WaMessageLog.objects.create(msisdn=MSISDN, direction=WaMessageLog.OUT,
                                    text="hi", processing_error="190")
        snap = health.whatsapp_diagnostics()
        self.assertEqual(snap["outbound"]["send_failures_last_hour"], 1)
        self.assertEqual(snap["outbound"]["last_send_error"], "190")

    def test_old_send_failures_age_out_of_the_window(self):
        from . import health

        old = WaMessageLog.objects.create(msisdn=MSISDN, direction=WaMessageLog.OUT,
                                          text="hi", processing_error="190")
        WaMessageLog.objects.filter(pk=old.pk).update(
            created=timezone.now() - health.SEND_FAILURE_WINDOW - timedelta(minutes=1))
        snap = health.whatsapp_diagnostics()
        self.assertEqual(snap["outbound"]["send_failures_last_hour"], 0)

    def test_verdict_names_send_failures_ahead_of_a_healthy_looking_queue(self):
        from . import health

        verdict = health._verdict("live", 0, False, 0, [], accepted_ever=True,
                                  send_failures=3, last_send_error="190")
        self.assertIn("190", verdict)
        self.assertIn("WHATSAPP_TOKEN", verdict)

    def test_healthy_channel_names_neither_token_nor_send_failures(self):
        from . import health

        verdict = health._verdict("live", 0, False, 0, [], accepted_ever=True)
        self.assertNotIn("WHATSAPP_TOKEN", verdict)


class FallbackSenderLoggingTests(TestCase):
    """reply() records whether Meta accepted the send; the senders that FALL BACK
    did not. They attempted a send, dropped the result, and wrote a row saying
    "replied" either way — so `send_failures` undercounted, and worst on the paths
    a new user hits first: the menu goes out through reply_list/reply_buttons, so a
    dead token produced a spotless outbound log for the very first reply missed."""

    class _Resp:
        def __init__(self, ok, status, payload):
            self.ok, self.status_code, self.content = ok, status, b"x"
            self._payload = payload

        def json(self):
            return self._payload

    def _refused(self):
        return self._Resp(False, 401, {"error": {"code": 190, "type": "OAuthException"}})

    def _live(self):
        return patch.multiple("whatsapp.providers",
                              wa_live=lambda: True,
                              _cfg=lambda: {"BASE_URL": "https://graph.facebook.com/v20.0",
                                            "PHONE_NUMBER_ID": "1", "TOKEN": "t"})

    def _last_error(self):
        return WaMessageLog.objects.filter(
            direction=WaMessageLog.OUT).latest("created").processing_error

    def test_reply_list_records_a_refused_send(self):
        from .router import reply_list

        with self._live(), patch("whatsapp.providers.requests.post", return_value=self._refused()):
            reply_list("2348010000000", "Menu", [("1", "Airtime", "")])
        self.assertEqual(self._last_error(), "190")

    def test_reply_buttons_records_a_refused_send(self):
        from .router import reply_buttons

        with self._live(), patch("whatsapp.providers.requests.post", return_value=self._refused()):
            reply_buttons("2348010000000", "Confirm?", [("yes", "Yes")])
        self.assertEqual(self._last_error(), "190")

    def test_reply_image_records_a_refused_send(self):
        from .router import reply_image

        with self._live(), patch("whatsapp.providers.requests.post", return_value=self._refused()):
            reply_image("2348010000000", None, "MTN")
        self.assertEqual(self._last_error(), "190")

    def test_a_mocked_channel_still_records_a_clean_row(self):
        """Not live => the send is mocked and succeeds; the row must stay clean or
        every dev/sandbox reply would read as a failure."""
        from .router import reply_list

        reply_list("2348010000000", "Menu", [("1", "Airtime", "")])
        self.assertEqual(self._last_error(), "")

    def test_the_menu_path_is_counted_by_diagnostics(self):
        from . import health
        from .router import reply_list

        with self._live(), patch("whatsapp.providers.requests.post", return_value=self._refused()):
            reply_list("2348010000000", "Menu", [("1", "Airtime", "")])
        self.assertEqual(health.whatsapp_diagnostics()["outbound"]["send_failures_last_hour"], 1)


class InlineOverrideTests(TestCase):
    """WHATSAPP_PROCESS_INLINE must be settable from the environment. The web-drain
    daemon thread is reaped without a trace when a hobby-tier instance recycles
    after the response — attempts stay 0, nothing is logged — and with no worker
    service the queue then just sits. Inline is the one execution context such a
    host guarantees; before the override, production had no way to choose it."""

    WA = {"MODE": "live", "VERIFY_TOKEN": "v", "TOKEN": "t", "APP_SECRET": "shh",
          "BASE_URL": "x", "PHONE_NUMBER_ID": "phone-1",
          "BUSINESS_NUMBER": "2348000000000"}

    def _post_hi(self):
        event = {"entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "phone-1"},
            "messages": [{"id": "wamid.inline-1", "from": MSISDN,
                          "type": "text", "text": {"body": "menu"}}]}}]}]}
        body = json.dumps(event).encode()
        sig = hmac.new(b"shh", body, hashlib.sha256).hexdigest()
        return Client().post("/webhooks/whatsapp", data=body,
                             content_type="application/json",
                             HTTP_X_HUB_SIGNATURE_256=f"sha256={sig}")

    def test_env_true_turns_inline_on_in_production(self):
        from zitch_api.settings import env_bool

        with patch.dict("os.environ", {"WHATSAPP_PROCESS_INLINE": "true"}):
            self.assertTrue(env_bool("WHATSAPP_PROCESS_INLINE", False))

    def test_unset_keeps_the_dev_default(self):
        from zitch_api.settings import env_bool

        self.assertTrue(env_bool("WHATSAPP_PROCESS_INLINE", True))
        self.assertFalse(env_bool("WHATSAPP_PROCESS_INLINE", False))

    def test_inline_processing_answers_within_the_webhook_request(self):
        """One signed webhook POST, no worker, no drain thread — the reply must
        already be in the OUT log when the response returns."""
        with override_settings(WHATSAPP=self.WA, WHATSAPP_PROCESS_INLINE=True):
            res = self._post_hi()
        self.assertEqual(res.status_code, 200)
        row = WaMessageLog.objects.filter(direction=WaMessageLog.IN,
                                          msisdn=MSISDN).latest("created")
        self.assertIsNotNone(row.processed_at)
        self.assertTrue(WaMessageLog.objects.filter(
            direction=WaMessageLog.OUT, msisdn=MSISDN).exists())

    def test_a_router_crash_in_production_inline_does_not_500_the_webhook(self):
        """Meta redelivers on 500 for a row that is already queued and counted, and
        enough 500s get the callback throttled. In production inline mode the job
        function records the failure on the row instead of raising."""
        with override_settings(WHATSAPP=self.WA, WHATSAPP_PROCESS_INLINE=True,
                               DEBUG=False, TESTING=False), \
             patch("whatsapp.jobs.handle_inbound",
                   side_effect=RuntimeError("router bug")):
            res = self._post_hi()
        self.assertEqual(res.status_code, 200)
        row = WaMessageLog.objects.filter(direction=WaMessageLog.IN,
                                          msisdn=MSISDN).latest("created")
        self.assertIsNone(row.processed_at)
        self.assertEqual(row.processing_attempts, 1)
        self.assertTrue(row.processing_error)
