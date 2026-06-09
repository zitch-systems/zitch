"""WhatsApp channel tests (slice 1): webhook, linking, balance, NGN transfer.

All run in MOCK mode (no Meta/Monnify keys), so the webhook accepts unsigned
bodies and the payout settles automatically — the full flow is exercised offline.
"""
import hashlib
import hmac
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from accounts.models import AccessToken
from transfers.models import Bank
from utility.models import CablePlan, DataPlan
from wallet.models import Transaction
from wallet.services import credit, get_or_create_wallet

from .models import PendingAction, WaMessageLog, WhatsAppLink

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
        res = self.client.post("/api/whatsapp/link/start/",
                               data=json.dumps({"access_token": self.token}), content_type="application/json")
        code = res.json()["code"]
        self.inbound(f"LINK {code}", "m1")
        link = WhatsAppLink.objects.get(wa_msisdn=MSISDN)
        self.assertEqual(link.status, WhatsAppLink.ACTIVE)
        self.assertEqual(link.user_id, self.user.id)
        self.assertIn("Linked", self.last_reply())

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
