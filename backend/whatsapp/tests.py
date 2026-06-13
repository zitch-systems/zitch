"""WhatsApp channel tests (slice 1): webhook, linking, balance, NGN transfer.

All run in MOCK mode (no Meta/Monnify keys), so the webhook accepts unsigned
bodies and the payout settles automatically — the full flow is exercised offline.
"""
import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from accounts.models import AccessToken
from transfers.models import Bank
from utility.models import CablePlan, DataPlan
from wallet.models import Transaction
from wallet.services import credit, get_or_create_wallet

from .models import (
    AuditLog, Broadcast, BroadcastRecipient, ConversationState,
    PendingAction, WaMessageLog, WhatsAppLink,
)

User = get_user_model()
MSISDN = "2348011112222"


def make_user(phone="08010000001", email="ada@zitch.test", pin="1234", balance="50000"):
    u = User.objects.create(username=phone, phone=phone, email=email,
                            first_name="Ada", last_name="Eze", tier=1)
    u.set_transaction_pin(pin)
    u.save()
    get_or_create_wallet(u)
    if Decimal(balance) > 0:
        credit(u, Decimal(balance), "Seed")
    return u, AccessToken.issue(u).key


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
                                 "BASE_URL": "x", "PHONE_NUMBER_ID": "", "BUSINESS_NUMBER": ""})
    def test_signature_enforced_when_secret_set(self):
        body = json.dumps({"entry": []}).encode()
        bad = self.client.post("/webhooks/whatsapp", data=body, content_type="application/json",
                               HTTP_X_HUB_SIGNATURE_256="sha256=deadbeef")
        self.assertEqual(bad.status_code, 401)
        good_sig = hmac.new(b"shh", body, hashlib.sha256).hexdigest()
        good = self.client.post("/webhooks/whatsapp", data=body, content_type="application/json",
                                HTTP_X_HUB_SIGNATURE_256=f"sha256={good_sig}")
        self.assertEqual(good.status_code, 200)


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

    # --- linking ---
    def test_unlinked_number_gets_link_flow(self):
        self.inbound("hello", "m1")
        self.assertIn("Link WhatsApp", self.last_reply())
        self.assertFalse(WhatsAppLink.objects.filter(status=WhatsAppLink.ACTIVE).exists())

    def test_link_code_links_number(self):
        # The code must be sent from the number on the user's Zitch account
        # (national significant number matches MSISDN's last 10 digits).
        self.user.phone = "08011112222"
        self.user.save(update_fields=["phone"])
        res = self.client.post("/api/whatsapp/link/start/",
                               data=json.dumps({"access_token": self.token}), content_type="application/json")
        code = res.json()["code"]
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

    # --- balance ---
    def test_balance(self):
        self.link()
        self.inbound("balance", "m1")
        self.assertIn("50,000.00", self.last_reply())

    # --- transfer ---
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
        self.assertIn("Sent", self.last_reply())
        self.assertEqual(self.balance(), Decimal("45000"))
        self.assertEqual(
            Transaction.objects.filter(user=self.user, direction=Transaction.OUT,
                                       transaction_status=Transaction.SUCCESS).count(), 1)

    def test_oneline_paste_transfer(self):
        self.link()
        self.inbound("0123456789 GTBank John Doe 5000", "p1")  # single bank match -> confirm
        self.assertIn("Confirm transfer", self.last_reply())
        self.inbound("1234", "p2")
        self.assertIn("Sent", self.last_reply())
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
        self.assertIn("airtime sent", self.last_reply())
        self.assertEqual(self.bal(), Decimal("19800"))

    def test_data(self):
        self.inbound("data", "d1")
        self.inbound("1", "d2")               # MTN -> plan list
        self.assertIn("1GB", self.last_reply())
        self.inbound("1", "d3")               # pick plan -> ask phone
        self.inbound("me", "d4")              # phone -> confirm
        self.assertIn("Confirm data", self.last_reply())
        self.inbound("1234", "d5")
        self.assertIn("sent to", self.last_reply())
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
        self.assertIn("activated", self.last_reply())
        self.assertEqual(self.bal(), Decimal("11000"))

    def test_wrong_network_reprompts(self):
        self.inbound("airtime", "n1")
        self.inbound("9", "n2")               # invalid network
        self.assertIn("network", self.last_reply().lower())
        self.assertEqual(self.bal(), Decimal("20000"))

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
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)
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

    def test_freeform_airtime_prefilled_confirm(self):
        with self._stub({"name": "buy_airtime",
                         "input": {"amount": 200, "phone": "08099998888", "network": "MTN"}}):
            self.inbound("load 200 mtn airtime for 08099998888", "a1")
        self.assertIn("Confirm airtime", self.last_reply())

    def test_clarify_shows_menu(self):
        with self._stub({"name": "clarify", "input": {"reason": "unsupported"}}):
            self.inbound("tell me a joke", "c1")
        self.assertIn("Reply with a number", self.last_reply())

    def test_parsed_intent_is_recorded(self):
        with self._stub({"name": "check_balance", "input": {}}):
            self.inbound("balance pls", "r1")
        row = WaMessageLog.objects.get(wa_message_id="r1", direction=WaMessageLog.IN)
        self.assertEqual(row.intent_json.get("name"), "check_balance")

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
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("50000"))  # untouched

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
        self.staff_token = AccessToken.issue(self.staff).key

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
        res, _ = self.post_as("/api/whatsapp/ops/handover/", self.token, {"msisdn": MSISDN})  # normal user
        self.assertEqual(res.status_code, 403)

    def test_ops_requires_role_capability(self):
        """A staff account with no role group is read_only: no wa/broadcast caps."""
        bare = User.objects.create(username="ro", phone="08098888888", email="ro@zitch.test", is_staff=True)
        bare_token = AccessToken.issue(bare).key
        res, _ = self.post_as("/api/whatsapp/ops/handover/", bare_token, {"msisdn": MSISDN})
        self.assertEqual(res.status_code, 403)
        res, _ = self.post_as("/api/whatsapp/ops/broadcast/", bare_token, {"template_name": "promo"})
        self.assertEqual(res.status_code, 403)

    def test_finance_role_cannot_broadcast(self):
        """The finance role has money/users caps but not wa/broadcast."""
        from django.contrib.auth.models import Group

        fin = User.objects.create(username="fin", phone="08097777777", email="fin@zitch.test", is_staff=True)
        fin.groups.add(Group.objects.get_or_create(name="finance")[0])
        fin_token = AccessToken.issue(fin).key
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
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["queued"], 1)        # only the opted-in user
        self.assertEqual(body["sent"], 1)
        recips = BroadcastRecipient.objects.all()
        self.assertEqual(recips.count(), 1)
        self.assertEqual(recips.first().wa_msisdn, MSISDN)
        self.assertTrue(AuditLog.objects.filter(action="broadcast.send").exists())

    def test_utility_broadcast_ignores_opt_in(self):
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN,
                                    status=WhatsAppLink.ACTIVE, marketing_opt_in=False)
        res, body = self.post_as("/api/whatsapp/ops/broadcast/", self.staff_token,
                                 {"template_name": "txn_alert", "category": "utility"})
        self.assertEqual(body["sent"], 1)          # utility reaches non-opted-in users
