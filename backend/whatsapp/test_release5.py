"""Release 5: the approved design on the published pages, and the bank's bill charge.

Two things are pinned here:

* WHATSAPP_FLOW['DESIGN_V5'] keeps the contract of the Flow that is live until the
  new one is published. The PIN pages' `title` / `cta`, the "How much?" page
  without its details block, and SIGNUP_PIN are all contract changes, and the
  live Flow rejects an undeclared property ("Couldn't load content").
* The partner bank refuses a bill without its catalogue charge and takes it from
  the customer's account on top of the bill, so the customer sees it before the
  PIN and the wallet is debited the bill plus the charge in one row.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from accounts.models import User
from utility.models import WemaBiller
from wallet.models import Transaction

from . import flows, router
from .flows import sign_flow_token
from .models import PendingAction, WaOnboarding
# Imported as modules, not names: a TestCase class named at module level here
# would be collected and run a second time as part of this module.
from . import test_release2 as r2, test_release4 as r4
from .test_release2 import _exchange
from .test_release4 import _BY_ID, PHONE, SIGNUP_ON

V5 = {"DESIGN_V5": True}
OLD_PIN = {"balance", "amount", "recipient", "details", "narration", "error"}


class PinContractTests(SimpleTestCase):

    def test_off_keeps_the_live_flows_pin_contract(self):
        self.assertEqual(set(flows._pin_screen({"amount": "₦500.00"})["data"]), OLD_PIN)
        self.assertEqual(set(router.pin_screen_send_data("Create a 6-digit PIN")), OLD_PIN)

    @override_settings(WHATSAPP_FLOW=V5)
    def test_on_matches_every_published_pin_page(self):
        declared = set(_BY_ID["PIN_SCREEN"]["data"])
        self.assertEqual(declared, OLD_PIN | {"cta", "title"})
        for sid in ("PIN_CHAIN", "PIN_RETRY"):
            self.assertEqual(set(_BY_ID[sid]["data"]), declared, sid)
        pay = flows._pin_screen({"amount": "₦500.00", "cta": "Pay"})["data"]
        self.assertEqual(set(pay), declared)
        self.assertEqual((pay["title"], pay["cta"]), ("Confirm payment", "Pay"))
        signup = flows._pin_screen("Create a 6-digit PIN")["data"]
        self.assertEqual((signup["title"], signup["cta"]), ("Create your PIN", "Confirm"))
        self.assertEqual(set(router.pin_screen_send_data("x")), declared)

    def test_the_pin_pages_take_their_title_and_button_from_the_data(self):
        for sid in ("PIN_SCREEN", "PIN_CHAIN", "PIN_RETRY"):
            screen = _BY_ID[sid]
            self.assertEqual(screen["title"], "${data.title}")
            footer = screen["layout"]["children"][-1]["children"][-1]
            self.assertEqual((footer["type"], footer["label"]), ("Footer", "${data.cta}"))


class _BillPages:
    open = r2.BillFormTests.open
    details = r2.BillFormTests.details


@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "BILL_FORM": True})
class BillChargeTests(_BillPages, r2._MoneyBase):

    def setUp(self):
        super().setUp()
        WemaBiller.objects.filter(service_id="ikeja-electric").update(charge=Decimal("100"))

    def test_the_old_amount_page_keeps_its_details_block(self):
        page = self.details(self.open())
        self.assertEqual(page["screen"], flows.BILL_AMOUNT)
        self.assertIn("details", page["data"])

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "BILL_FORM": True, **V5})
    def test_the_fee_is_shown_before_the_pin_and_debited_with_the_bill(self):
        pa = self.open()
        page = self.details(pa)
        self.assertEqual(set(page["data"]), set(_BY_ID["BILL_AMOUNT"]["data"]))
        self.assertTrue(page["data"]["address"])
        # The balance check counts the fee: ₦19,950 + ₦100 is more than ₦20,000.
        broke = _exchange(sign_flow_token(pa), {"amount": "19950"})
        self.assertIn("Insufficient balance", broke["data"]["error"])
        confirm = self.assertScreen(_exchange(sign_flow_token(pa), {"amount": "3000"}),
                                    standing_on=flows.BILL_AMOUNT)
        self.assertEqual(confirm["screen"], flows.PIN_CHAIN)
        self.assertEqual(confirm["data"]["amount"], "₦3,000.00")
        self.assertIn("Bank fee: ₦100.00", confirm["data"]["details"])
        self.assertIn("Total: ₦3,100.00", confirm["data"]["details"])
        self.assertEqual((confirm["data"]["title"], confirm["data"]["cta"]), ("Confirm payment", "Pay"))
        with patch("whatsapp.router.reply"), patch("whatsapp.router.reply_receipt"):
            done = _exchange(sign_flow_token(pa), {"pin": "1234"})
        self.assertEqual(done["data"]["status"], "✅ Successful", done["data"]["message"])
        txn = Transaction.objects.get(user=self.user, idempotency_key=f"wa-{pa.pk}")
        self.assertEqual(txn.amount, Decimal("3100"))

    def test_no_charge_means_no_fee_lines(self):
        WemaBiller.objects.filter(service_id="ikeja-electric").update(charge=Decimal("0"))
        pa = self.open()
        self.details(pa)
        confirm = _exchange(sign_flow_token(pa), {"amount": "3000"})
        self.assertNotIn("Bank fee", confirm["data"]["details"])
        self.assertIn("Total (no fee): ₦3,000.00", confirm["data"]["details"])


@override_settings(WHATSAPP_FLOW={**SIGNUP_ON, **V5})
class SignupPinTests(r4.SignupBvnTests):
    """Every Release 4 sign-up test again, with the "Create your PIN" page after
    the address in place of the old two-step PIN."""

    def test_the_address_is_kept_as_the_app_keeps_it_then_the_pin(self):
        self.to_address()
        pin = self.submit(flows.SIGNUP_ADDRESS, house_number="12", street="Allen Avenue",
                          state="Lagos", lga="Ikeja")
        self.assertEqual(pin["screen"], flows.SIGNUP_PIN)
        self.assertEqual(set(pin["data"]), set(_BY_ID["SIGNUP_PIN"]["data"]))
        user = User.objects.get(phone=PHONE)
        self.assertEqual(user.address, "12 Allen Avenue, Ikeja, Lagos")

        for data, wanted in (({"pin": "24681", "confirm_pin": "24681"}, "6 digits"),
                             ({"pin": "246810", "confirm_pin": "246811"}, "didn't match"),
                             ({"pin": "123456", "confirm_pin": "123456"}, "")):
            with self.subTest(data=data):
                resp = self.submit(standing_on=pin["screen"], **data)
                self.assertEqual(resp["screen"], flows.SIGNUP_PIN_RETRY)
                self.assertIn(wanted, resp["data"]["error"])
                self.assertTrue(resp["data"]["error"])
                pin = resp
        user.refresh_from_db()
        self.assertFalse(user.transaction_pin)

        wallet = user.wallet
        wallet.account_number = "0123456789"
        wallet.save(update_fields=["account_number"])
        with patch("whatsapp.router._send_account_details"), \
             patch("whatsapp.router._kyc_continue_after_account"):
            done = self.submit(standing_on=flows.SIGNUP_PIN_RETRY, pin="246810", confirm_pin="246810")
        self.assertIn("account is ready", done["data"]["message"])
        user.refresh_from_db()
        self.assertTrue(user.transaction_pin)
        self.assertFalse(WaOnboarding.objects.filter(msisdn=self.ob.msisdn).exists())

    def test_a_flag_turned_off_mid_signup_falls_back_to_the_old_pin(self):
        self.to_address()
        with override_settings(WHATSAPP_FLOW=SIGNUP_ON):
            pin = self.submit(flows.SIGNUP_ADDRESS, house_number="12", street="Allen Avenue",
                              state="Lagos", lga="Ikeja")
        self.assertEqual(pin["screen"], flows.PIN_CHAIN)
        self.assertNotIn("title", pin["data"])
        self.assertFalse(PendingAction.objects.exists())
