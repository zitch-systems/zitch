"""Release 4 Flow screens: "Which bill?" with cable TV paid in the form, and the
BVN and home-address steps inside the sign-up form.

The same two promises as Releases 2 and 3, for every new screen:

* with its flag OFF (the default) nothing changes - the screen is never sent and
  never answered, because Meta serves the Flow JSON published by hand AFTER the
  deploy, and an unpublished screen is "Couldn't load content";
* with its flag ON every answer is renderable: a declared screen, exactly its
  declared properties, a declared route from the screen the device is on, and
  no empty Dropdown/RadioButtonsGroup (FlowContractMixin.assertScreen).
"""
import json
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

import whatsapp
from accounts.models import User
from utility.catalogue_fixtures import map_cable
from wallet.models import Transaction

from . import flows, release4
from .flows import sign_flow_token, sign_onboarding_token
from .lgas import LGAS_BY_STATE
from .models import PendingAction, WaOnboarding, WhatsAppLink
from .test_flow_e2e import FlowContractMixin
from .test_release2 import DETAILS, GOOD_PW, MSISDN, NEW_MSISDN, _exchange, _MoneyBase, _signup_ob

_PATH = Path(whatsapp.__file__).parent / "flow_assets" / "pin_flow.json"
_DOC = json.loads(_PATH.read_text())
_BY_ID = {s["id"]: s for s in _DOC["screens"]}
NEW = ("BILL_TYPE", "BILL_TYPE_CHAIN", "CABLE_SCREEN", "CABLE_PLAN", "SIGNUP_BVN",
       "SIGNUP_BVN_RETRY", "SIGNUP_ADDRESS")
CABLE_ON = {"RESULT_SCREEN": True, "BILL_FORM": True, "BILL_CABLE_FORM": True}
SIGNUP_ON = {"RESULT_SCREEN": True, "DETAILS_SCREEN": True, "VERIFY_PASSWORD_SCREEN": True,
             "SIGNUP_BVN_FORM": True}


class FlagDefaultsTests(SimpleTestCase):

    FLAGS = (flows.bill_cable_form_live, flows.signup_bvn_live)

    @override_settings(WHATSAPP_FLOW={"FLOW_ID": "1", "RESULT_SCREEN": True, "BILL_FORM": True})
    def test_off_by_default(self):
        self.assertEqual([f() for f in self.FLAGS], [False, False])

    @override_settings(WHATSAPP_FLOW={"BILL_CABLE_FORM": True, "SIGNUP_BVN_FORM": True})
    def test_each_reads_its_own_key(self):
        self.assertEqual([f() for f in self.FLAGS], [True, True])

    def test_the_settings_module_reads_each_flag_from_its_env_var(self):
        import inspect

        from django.conf import settings

        from zitch_api import settings as module

        source = inspect.getsource(module)
        for name in ("BILL_CABLE_FORM", "SIGNUP_BVN_FORM"):
            with self.subTest(name=name):
                self.assertIn(f'"{name}": os.environ.get("WHATSAPP_FLOW_{name}", "")', source)
                self.assertIs(settings.WHATSAPP_FLOW[name], False)


# --------------------------------------------------------------------------- #
# the Flow JSON
# --------------------------------------------------------------------------- #
def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _form(sid):
    return next(c for c in _BY_ID[sid]["layout"]["children"] if c.get("type") == "Form")


def _field(sid, name):
    return next(c for c in _form(sid)["children"] if c.get("name") == name)


class Release4FlowJsonTests(SimpleTestCase):

    def test_the_new_screens_are_published_with_their_routes(self):
        routes = _DOC["routing_model"]
        self.assertLessEqual(set(NEW), set(_BY_ID))
        self.assertEqual(routes["BILL_TYPE"], ["BILL_CHAIN", "CABLE_SCREEN", "RESULT", "SUCCESS"])
        self.assertEqual(routes["CABLE_SCREEN"], ["CABLE_PLAN", "RESULT", "SUCCESS"])
        self.assertEqual(routes["CABLE_PLAN"], ["PIN_CHAIN", "RESULT", "SUCCESS"])
        self.assertEqual(routes["SIGNUP_BVN"], ["SIGNUP_BVN_RETRY", "SIGNUP_ADDRESS", "RESULT", "SUCCESS"])
        self.assertEqual(routes["SIGNUP_BVN_RETRY"], ["SIGNUP_ADDRESS", "RESULT", "SUCCESS"])
        # Release 5 adds the "Create your PIN" page after the address; PIN_CHAIN
        # stays for the Flow-flag-off path.
        self.assertEqual(routes["SIGNUP_ADDRESS"], ["SIGNUP_PIN", "PIN_CHAIN", "RESULT", "SUCCESS"])
        self.assertIn("BILL_TYPE_CHAIN", routes["MENU"])
        # Every signup page that could go to the PIN can now go to the BVN or
        # the address instead; nothing else gained a route into them.
        signup = {"SIGNUP_DETAILS", "DETAILS_CHAIN", "SIGNUP_PHONE", "SIGNUP_PHONE_CODE",
                  "SIGNUP_PASSWORD", "VERIFY_PASSWORD"}
        for target in ("SIGNUP_BVN", "SIGNUP_ADDRESS"):
            sources = {sid for sid, to in routes.items() if target in to}
            self.assertEqual(sources - {"SIGNUP_BVN", "SIGNUP_BVN_RETRY"}, signup, target)
        incoming = {t for targets in routes.values() for t in targets}
        self.assertNotIn("BILL_TYPE", incoming)       # a root: the chat opens on it
        self.assertLessEqual({"BILL_TYPE_CHAIN", "CABLE_SCREEN", "CABLE_PLAN", "SIGNUP_BVN",
                              "SIGNUP_BVN_RETRY", "SIGNUP_ADDRESS"}, incoming)

    def test_meta_per_screen_component_limits(self):
        """At most two EmbeddedLinks per screen: Meta refused a draft with three
        (COMPONENT_TYPE_MAX_NUMBER_PER_SCREEN)."""
        for screen in _DOC["screens"]:
            with self.subTest(sid=screen["id"]):
                links = [n for n in _walk(screen["layout"]) if n.get("type") == "EmbeddedLink"]
                self.assertLessEqual(len(links), 2)

    def test_only_known_components_and_a_footer_last(self):
        allowed = {"SingleColumnLayout", "Image", "TextHeading", "TextBody", "TextCaption",
                   "TextInput", "Dropdown", "RadioButtonsGroup", "EmbeddedLink", "Form", "Footer"}
        for sid in NEW:
            with self.subTest(sid=sid):
                found = {n["type"] for n in _walk(_BY_ID[sid]["layout"])
                         if isinstance(n.get("type"), str) and n["type"][0].isupper()}
                self.assertLessEqual(found, allowed)
                self.assertIn("SUCCESS", _DOC["routing_model"][sid])
                form = _BY_ID[sid]["layout"]["children"][-1]
                self.assertEqual(form["type"], "Form")
                self.assertEqual([c["type"] for c in form["children"]].count("Footer"), 1)
                self.assertEqual(form["children"][-1]["type"], "Footer")

    def test_every_binding_names_a_declared_property_or_a_field(self):
        for sid in NEW:
            with self.subTest(sid=sid):
                text = json.dumps(_BY_ID[sid]["layout"])
                self.assertLessEqual(set(re.findall(r"\$\{data\.(\w+)\}", text)),
                                     set(_BY_ID[sid]["data"]))
                form = _form(sid)
                names = {c.get("name") for c in form["children"] if c.get("name")}
                self.assertLessEqual(set(re.findall(r"\$\{form\.(\w+)\}", text)), names)
                payload = form["children"][-1]["on-click-action"]["payload"]
                self.assertEqual(payload, {k: "${form.%s}" % k for k in payload})

    def test_the_twin_is_identical_to_its_root(self):
        self.assertEqual({**_BY_ID["BILL_TYPE_CHAIN"], "id": "BILL_TYPE"}, _BY_ID["BILL_TYPE"])
        self.assertEqual(_DOC["routing_model"]["BILL_TYPE_CHAIN"],
                         _DOC["routing_model"]["BILL_TYPE"])

    def test_design_copy(self):
        bill = _field("BILL_TYPE", "bill")
        self.assertEqual([(o["id"], o["title"]) for o in bill["data-source"]],
                         [("electricity", "Electricity"), ("cable", "Cable TV")])
        self.assertEqual(_field("CABLE_SCREEN", "provider")["type"], "Dropdown")
        self.assertEqual(_field("CABLE_PLAN", "package")["type"], "RadioButtonsGroup")
        bvn = _BY_ID["SIGNUP_BVN"]
        self.assertEqual(bvn["title"], "Verify your BVN")
        texts = [c.get("text") for c in bvn["layout"]["children"]]
        self.assertIn("Step 3 of 5", texts)
        self.assertEqual(_field("SIGNUP_BVN", "bvn")["label"], "Bank Verification Number (BVN)")
        code = _field("SIGNUP_BVN", "code")
        self.assertEqual((code["label"], code["visible"], code["required"]),
                         ("SMS code", "${data.code_sent}", False))
        links = [c["text"] for c in _form("SIGNUP_BVN")["children"] if c["type"] == "EmbeddedLink"]
        self.assertEqual(links, ["Resend code", "${data.alt_text}"])
        address = _BY_ID["SIGNUP_ADDRESS"]
        self.assertIn("Step 4 of 5", [c.get("text") for c in address["layout"]["children"]])
        self.assertEqual([c.get("label") for c in _form("SIGNUP_ADDRESS")["children"][:4]],
                         ["House number", "Street address", "State", "Local government area (LGA)"])
        self.assertEqual(_field("SIGNUP_ADDRESS", "state")["on-select-action"],
                         {"name": "data_exchange", "payload": {"pick_state": True, "state": "${form.state}"}})

    def test_every_screen_shares_one_lighter_logo_and_the_document_fits(self):
        logos = {n["src"] for n in _walk(_DOC) if n.get("type") == "Image" and n.get("alt-text") == "Zitch"}
        self.assertEqual(len(logos), 1)
        self.assertLess(len(next(iter(logos))), 9_500)
        raw = _PATH.read_text()
        self.assertEqual(len(_DOC["screens"]), 49)
        self.assertLess(len(raw.encode()), 750_000)
        self.assertEqual(raw, json.dumps(_DOC, indent=2, ensure_ascii=True) + "\n")


class LgaDataTests(SimpleTestCase):

    def test_the_app_list_every_state_and_dropdown_limits(self):
        from accounts.views import _NIGERIAN_STATES

        self.assertEqual(set(LGAS_BY_STATE), set(_NIGERIAN_STATES))
        self.assertEqual(sum(map(len, LGAS_BY_STATE.values())), 774)
        self.assertLessEqual(max(map(len, LGAS_BY_STATE.values())), 200)
        self.assertLessEqual(max(len(lga) for lgas in LGAS_BY_STATE.values() for lga in lgas), 30)
        self.assertIn("Ikeja", LGAS_BY_STATE["Lagos"])


# --------------------------------------------------------------------------- #
# 1. "Which bill?" and cable TV
# --------------------------------------------------------------------------- #
@override_settings(WHATSAPP_FLOW=CABLE_ON)
class BillTypeTests(_MoneyBase):

    def setUp(self):
        super().setUp()
        map_cable()

    def open(self):
        sent, flow = self.say("4")                    # menu 4: Pay a bill
        kwargs = flow.call_args.kwargs
        self.assertEqual((kwargs["screen"], kwargs["cta"]), (flows.BILL_TYPE, "Pay a bill"))
        self.assertScreen({"screen": kwargs["screen"], "data": kwargs["screen_data"]})
        self.assertIn("*electricity*", kwargs["body"])  # the chat path offered beside it
        pa = PendingAction.objects.get(msisdn=MSISDN)
        self.assertEqual(pa.state, flows.FLOW_BILL_STATE)
        self.assertTrue(release4.is_bill_type(pa))
        return pa

    def submit(self, pa, standing_on, **data):
        return self.assertScreen(_exchange(sign_flow_token(pa), data), standing_on=standing_on)

    def to_cable(self, pa):
        page = self.submit(pa, flows.BILL_TYPE, bill="cable")
        self.assertEqual(page["screen"], flows.CABLE_SCREEN)
        self.assertEqual([p["title"] for p in page["data"]["providers"]], ["GoTV", "DSTV", "StarTimes"])
        return page

    def test_an_unknown_choice_stays_on_the_page(self):
        pa = self.open()
        resp = self.submit(pa, flows.BILL_TYPE, bill="water")
        self.assertEqual(resp["screen"], flows.BILL_TYPE)
        self.assertIn("electricity or cable", resp["data"]["error"])

    def test_electricity_goes_on_to_the_existing_bill_pages(self):
        pa = self.open()
        page = self.submit(pa, flows.BILL_TYPE, bill="electricity")
        self.assertEqual(page["screen"], flows.BILL_CHAIN)
        self.assertEqual(page["data"]["discos"][0], {"id": "1", "title": "Ikeja Electric (IKEDC)"})
        amount = self.submit(pa, flows.BILL_CHAIN, disco="1", meter_type="prepaid", meter="01234567890")
        self.assertEqual(amount["screen"], flows.BILL_AMOUNT)
        pa.refresh_from_db()
        self.assertEqual(pa.action_type, "electricity")

    def test_cable_details_are_checked(self):
        pa = self.open()
        self.to_cable(pa)
        for data, wanted in (({"provider": "9", "smartcard": "1234567890"}, "provider"),
                             ({"provider": "1", "smartcard": "12 3"}, "valid smartcard")):
            with self.subTest(data=data):
                resp = self.submit(pa, flows.CABLE_SCREEN, **data)
                self.assertEqual(resp["screen"], flows.CABLE_SCREEN)
                self.assertIn(wanted, resp["data"]["error"])
        with patch("whatsapp.router.vtu_verify_customer", return_value={"success": False}):
            refused = self.submit(pa, flows.CABLE_SCREEN, provider="1", smartcard="1234567890")
        self.assertIn("Couldn't validate that smartcard", refused["data"]["error"])

    def test_verified_card_package_pin_pays_with_the_chat_executor(self):
        pa = self.open()
        self.to_cable(pa)
        verified = {"success": True, "customer_name": "ADA OBI"}
        with patch("whatsapp.router.vtu_verify_customer", return_value=verified) as verify:
            plans = self.submit(pa, flows.CABLE_SCREEN, provider="1", smartcard="1234567890")
        verify.assert_called_once_with("gotv", "1234567890")
        self.assertEqual(plans["screen"], flows.CABLE_PLAN)
        self.assertEqual(plans["data"]["verified"], "✓ Smartcard verified · ADA OBI")
        self.assertEqual(plans["data"]["summary"], "GoTV · Smartcard 1234567890")
        self.assertEqual(plans["data"]["packages"],
                         [{"id": "gotv-max", "title": "GOtv Max", "description": "₦5,700.00"}])
        self.assertEqual(plans["data"]["balance"], "Available balance ₦20,000.00")
        pa.refresh_from_db()
        self.assertEqual(pa.action_type, "cable")

        bad = self.submit(pa, flows.CABLE_PLAN, package="dstv-compact")
        self.assertEqual(bad["screen"], flows.CABLE_PLAN)
        self.assertIn("Pick a package", bad["data"]["error"])
        with patch("whatsapp.router.vtu_verify_customer", return_value=verified) as verify, \
             patch("whatsapp.router._insufficient", return_value=True):
            broke = self.submit(pa, flows.CABLE_PLAN, package="gotv-max")
        self.assertIn("Insufficient balance", broke["data"]["error"])
        with patch("whatsapp.router.vtu_verify_customer", return_value=verified) as verify:
            confirm = self.submit(pa, flows.CABLE_PLAN, package="gotv-max")
        verify.assert_called_once_with("gotv", "1234567890", "gotv-max")
        self.assertEqual(confirm["screen"], flows.PIN_CHAIN)
        self.assertEqual(confirm["data"]["amount"], "₦5,700.00")
        self.assertEqual(confirm["data"]["recipient"], "Package: GoTV GOtv Max")
        self.assertIn("1234567890", confirm["data"]["details"])
        pa.refresh_from_db()
        self.assertEqual((pa.state, pa.action_type), (flows.FLOW_PIN_STATE, "cable"))
        self.assertEqual(pa.payload["meta"],
                         {"iuc": "1234567890", "provider": "1", "plan_code": "gotv-max"})

        with patch("whatsapp.router.reply"), patch("whatsapp.router.reply_receipt"):
            done = _exchange(sign_flow_token(pa), {"pin": "1234"})
        self.assertScreen(done, standing_on=flows.PIN_CHAIN)
        self.assertEqual(done["data"]["status"], "✅ Successful")
        txn = Transaction.objects.get(user=self.user, idempotency_key=f"wa-{pa.pk}")
        self.assertEqual(txn.amount, Decimal("5700"))

    def test_a_limit_ends_the_form(self):
        pa = self.open()
        self.to_cable(pa)
        with patch("whatsapp.router.vtu_verify_customer", return_value={"success": True, "customer_name": "A"}):
            self.submit(pa, flows.CABLE_SCREEN, provider="1", smartcard="1234567890")
            with patch("whatsapp.router.send_limit_error", return_value="Over your daily limit."):
                resp = self.submit(pa, flows.CABLE_PLAN, package="gotv-max")
        self.assertEqual(resp["screen"], flows.RESULT_SCREEN)
        self.assertFalse(PendingAction.objects.filter(msisdn=MSISDN).exists())

    def test_back_to_the_first_page_and_electricity_instead(self):
        """The device's back arrow does not tell the endpoint: a submit carrying
        `bill` is the first page again, whatever the step."""
        pa = self.open()
        self.to_cable(pa)
        with patch("whatsapp.router.vtu_verify_customer", return_value={"success": True, "customer_name": "A"}):
            self.submit(pa, flows.CABLE_SCREEN, provider="1", smartcard="1234567890")
        page = self.submit(pa, flows.BILL_TYPE, bill="electricity")
        self.assertEqual(page["screen"], flows.BILL_CHAIN)
        pa.refresh_from_db()
        self.assertEqual(pa.action_type, "electricity")

    def test_init_rerenders_the_page_the_session_is_on(self):
        pa = self.open()
        self.assertEqual(_exchange(sign_flow_token(pa), action="INIT")["screen"], flows.BILL_TYPE)
        self.to_cable(pa)
        self.assertEqual(_exchange(sign_flow_token(pa), action="INIT")["screen"], flows.CABLE_SCREEN)


@override_settings(WHATSAPP_FLOW={**CABLE_ON, "MENU_FORM": True})
class BillTypeMenuTests(_MoneyBase):

    def test_the_menu_opens_the_twin(self):
        from .release2 import sign_menu_token

        link = WhatsAppLink.objects.get(wa_msisdn=MSISDN)
        resp = self.assertScreen(_exchange(sign_menu_token(link), {"item": "4"}),
                                 standing_on=flows.MENU_SCREEN)
        self.assertEqual(resp["screen"], flows.BILL_TYPE_CHAIN)
        pa = PendingAction.objects.get(msisdn=MSISDN)
        self.assertTrue(release4.is_bill_type(pa))
        again = _exchange(sign_flow_token(pa), {"bill": "x"})
        self.assertEqual(again["screen"], flows.BILL_TYPE_CHAIN)


@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "BILL_FORM": True})
class BillTypeFlagOffTests(_MoneyBase):

    def test_the_electricity_form_is_sent_as_before(self):
        _, flow = self.say("4")
        self.assertEqual(flow.call_args.kwargs["screen"], flows.BILL_SCREEN)
        self.assertFalse(release4.is_bill_type(PendingAction.objects.get(msisdn=MSISDN)))


# --------------------------------------------------------------------------- #
# 2. the BVN and the address inside sign-up
# --------------------------------------------------------------------------- #
PHONE = "08077770009"


def _otp(pa, user, msisdn, digits, in_flow=False):
    """The bank accepted the BVN and sent its SMS code (router._account_submit_identity)."""
    pa.payload.update({"tracking_id": "T-1", "using_bvn": True})
    pa.save(update_fields=["payload"])
    return "otp"


@override_settings(WHATSAPP_FLOW=SIGNUP_ON)
class SignupBvnTests(FlowContractMixin, TestCase):

    def setUp(self):
        cache.clear()
        self.bank = patch("wallet.views._wema_funding_enabled", return_value=True)
        self.allowed = patch("utility.providers.partnership_new_business_allowed", return_value=True)
        self.bank.start()
        self.allowed.start()
        self.addCleanup(self.bank.stop)
        self.addCleanup(self.allowed.stop)
        self.ob = _signup_ob()
        self.token = sign_onboarding_token(self.ob)
        with patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}) as sms:
            first = _exchange(self.token, {**DETAILS, "phone": PHONE})
        self.assertEqual(first["screen"], flows.VERIFY_PASSWORD)
        code = sms.call_args[0][1].split("Zitch: ")[1][:6]
        self.bvn_page = self.assertScreen(
            _exchange(self.token, {"phone_code": code, "password": GOOD_PW,
                                   "confirm_password": GOOD_PW}),
            standing_on=flows.VERIFY_PASSWORD)

    def submit(self, standing_on=flows.SIGNUP_BVN, **data):
        return self.assertScreen(_exchange(self.token, data), standing_on=standing_on)

    def to_address(self):
        with patch("whatsapp.router._account_submit_identity", side_effect=_otp):
            self.submit(bvn="22345678901")
        with patch("wallet.views.complete_wema_provisioning", return_value=({"success": True}, 200)), \
             patch("whatsapp.router.customer_funding_account",
                   return_value={"account_number": "0123456789", "account_name": "NGOZI ADE"}):
            page = self.submit(code="123456")
        self.assertEqual(page["screen"], flows.SIGNUP_ADDRESS)
        return page

    def test_the_account_is_opened_before_the_bvn_page_without_a_pin(self):
        self.assertEqual(self.bvn_page["screen"], flows.SIGNUP_BVN)
        self.assertEqual(self.bvn_page["data"]["summary"], "✓ Phone number verified")
        self.assertFalse(self.bvn_page["data"]["code_sent"])
        user = User.objects.get(phone=PHONE)
        self.assertTrue(user.check_password(GOOD_PW))
        self.assertTrue(user.phone_verified)
        self.assertFalse(user.transaction_pin)
        self.assertTrue(WhatsAppLink.objects.filter(user=user, wa_msisdn=NEW_MSISDN).exists())
        self.ob.refresh_from_db()
        self.assertEqual(self.ob.step, flows.FLOW_BVN_STATE)
        self.assertNotIn("flow_pw_hash", self.ob.payload)

    def test_a_bvn_must_be_eleven_digits(self):
        resp = self.submit(bvn="123")
        self.assertEqual(resp["screen"], flows.SIGNUP_BVN)
        self.assertIn("11 digits", resp["data"]["error"])

    def test_the_code_box_appears_on_the_same_page_once_the_bank_sends_it(self):
        with patch("whatsapp.router._account_submit_identity", side_effect=_otp) as submit:
            resp = self.submit(bvn="22345678901")
        pa = submit.call_args.args[0]
        self.assertEqual((pa.action_type, pa.payload["id_type"]), ("add_account", "bvn"))
        self.assertEqual(submit.call_args.args[3], "22345678901")
        self.assertEqual(resp["screen"], flows.SIGNUP_BVN)
        self.assertTrue(resp["data"]["code_sent"])
        self.assertEqual((resp["data"]["alt_link"], resp["data"]["alt_text"]),
                         (True, "Use face check instead"))
        self.assertIn("Code sent", resp["data"]["notice"])

    def test_a_wrong_code_lands_on_the_empty_twin_then_the_right_one_goes_on(self):
        with patch("whatsapp.router._account_submit_identity", side_effect=_otp):
            self.submit(bvn="22345678901")
        with patch("wallet.views.complete_wema_provisioning",
                   return_value=({"message": "Invalid OTP."}, 422)):
            wrong = self.submit(code="000000")
        self.assertEqual(wrong["screen"], flows.SIGNUP_BVN_RETRY)
        self.assertIn("Invalid OTP", wrong["data"]["error"])
        with patch("wallet.views.complete_wema_provisioning", return_value=({"success": True}, 200)) as done, \
             patch("whatsapp.router.customer_funding_account",
                   return_value={"account_number": "0123456789", "account_name": "NGOZI ADE"}):
            page = self.submit(standing_on=flows.SIGNUP_BVN_RETRY, code="123456")
        done.assert_called_once()
        self.assertEqual(done.call_args.args[1:], ("123456", "T-1"))
        self.assertEqual(page["screen"], flows.SIGNUP_ADDRESS)
        self.assertEqual(page["data"]["summary"], "✓ BVN verified · NGOZI ADE")
        self.assertFalse(PendingAction.objects.filter(msisdn=NEW_MSISDN).exists())

    def test_a_refused_bvn_offers_do_this_later(self):
        with patch("whatsapp.router._account_submit_identity", return_value="fail"):
            resp = self.submit(bvn="22345678901")
        self.assertEqual(resp["screen"], flows.SIGNUP_BVN)
        self.assertEqual((resp["data"]["alt_link"], resp["data"]["alt_text"]), (True, "Do this later"))
        self.assertIn("Do this later", resp["data"]["error"])
        later = self.submit(alt=True)
        self.assertEqual(later["screen"], flows.SIGNUP_ADDRESS)

    def test_face_check_instead(self):
        with patch("whatsapp.router._account_submit_identity", side_effect=_otp):
            self.submit(bvn="22345678901")
        mode = self.submit(alt=True)
        self.assertFalse(mode["data"]["code_sent"])
        self.assertIn("face check link", mode["data"]["notice"])
        with patch("whatsapp.router._account_submit_identity", return_value="face") as submit:
            sent = self.submit(bvn="22345678901")
        self.assertEqual(submit.call_args.args[0].payload["verification_method"], "face")
        self.assertIn("Open the face check link in the chat", sent["data"]["notice"])
        with patch("whatsapp.router.customer_funding_account", return_value={}), \
             patch("wallet.identity.accepted_identity_pending", return_value=False):
            waiting = self.submit()
        self.assertIn("hasn't confirmed", waiting["data"]["error"])
        with patch("whatsapp.router.customer_funding_account",
                   return_value={"account_number": "0123456789", "account_name": "NGOZI ADE"}):
            page = self.submit()
        self.assertEqual(page["screen"], flows.SIGNUP_ADDRESS)

    def test_resend_asks_the_bank_for_the_same_code(self):
        with patch("whatsapp.router._account_submit_identity", side_effect=_otp):
            self.submit(bvn="22345678901")
        with patch("whatsapp.router.wema_provider.resend_wallet_otp",
                   return_value={"success": True}) as resend:
            resp = self.submit(resend=True)
        resend.assert_called_once_with(PHONE, "T-1", bvn=True)
        self.assertIn("send the code again", resp["data"]["notice"])

    def test_an_expired_attempt_lets_next_start_a_new_one(self):
        with patch("whatsapp.router._account_submit_identity", side_effect=_otp):
            self.submit(bvn="22345678901")
        with patch("wallet.views.complete_wema_provisioning",
                   return_value=({"message": "OTP expired."}, 400)):
            resp = self.submit(code="123456")
        self.assertEqual(resp["screen"], flows.SIGNUP_BVN)
        self.assertFalse(resp["data"]["code_sent"])
        self.assertIn("Tap Next to get a new code", resp["data"]["error"])

    def test_the_address_is_kept_as_the_app_keeps_it_then_the_pin(self):
        page = self.to_address()
        self.assertEqual(page["data"]["lgas"], [{"id": "choose", "title": "Choose your state first"}])
        self.assertIn({"id": "FCT", "title": "FCT (Abuja)"}, page["data"]["states"])
        picked = self.submit(flows.SIGNUP_ADDRESS, pick_state=True, state="Lagos")
        self.assertEqual(picked["screen"], flows.SIGNUP_ADDRESS)
        self.assertIn({"id": "Ikeja", "title": "Ikeja"}, picked["data"]["lgas"])
        for data, wanted in (({"street": "Allen Avenue", "state": "Lagos", "lga": "Ikeja"}, "house number"),
                             ({"house_number": "12", "street": "Allen", "state": "Atlantis", "lga": "Ikeja"}, "state"),
                             ({"house_number": "12", "street": "Allen", "state": "Lagos", "lga": "Gusau"}, "local government")):
            with self.subTest(data=data):
                resp = self.submit(flows.SIGNUP_ADDRESS, **data)
                self.assertEqual(resp["screen"], flows.SIGNUP_ADDRESS)
                self.assertIn(wanted, resp["data"]["error"])
        pin = self.submit(flows.SIGNUP_ADDRESS, house_number="12", street="Allen  Avenue",
                          state="lagos", lga="Ikeja")
        self.assertEqual(pin["screen"], flows.PIN_CHAIN)
        user = User.objects.get(phone=PHONE)
        self.assertEqual(user.address, "12 Allen Avenue, Ikeja, Lagos")
        self.assertFalse(user.address_verified)

        wallet = user.wallet
        wallet.account_number = "0123456789"
        wallet.save(update_fields=["account_number"])
        with patch("whatsapp.router._send_account_details") as details, \
             patch("whatsapp.router._kyc_continue_after_account") as rollin:
            _exchange(self.token, {"pin": "246810"})
            done = _exchange(self.token, {"pin": "246810"})
        self.assertIn("account is ready", done["data"].get("message", "") + done["data"].get("status", ""))
        user.refresh_from_db()
        self.assertTrue(user.check_transaction_pin("246810") if hasattr(user, "check_transaction_pin")
                        else check_password("246810", user.transaction_pin))
        self.assertIn("Welcome to Zitch, Ngozi!", details.call_args.kwargs["intro"])
        rollin.assert_called_once()
        self.assertFalse(WaOnboarding.objects.filter(msisdn=NEW_MSISDN).exists())


@override_settings(WHATSAPP_FLOW=SIGNUP_ON)
class SignupBvnBankOffTests(FlowContractMixin, TestCase):

    def test_without_the_bank_the_form_goes_straight_to_the_address(self):
        cache.clear()
        ob = _signup_ob()
        token = sign_onboarding_token(ob)
        with patch("wallet.views._wema_funding_enabled", return_value=False), \
             patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}) as sms:
            _exchange(token, {**DETAILS, "phone": PHONE})
            code = sms.call_args[0][1].split("Zitch: ")[1][:6]
            resp = self.assertScreen(
                _exchange(token, {"phone_code": code, "password": GOOD_PW,
                                  "confirm_password": GOOD_PW}),
                standing_on=flows.VERIFY_PASSWORD)
        self.assertEqual(resp["screen"], flows.SIGNUP_ADDRESS)
        self.assertEqual(resp["data"]["summary"], "Saved for Tier 3. Your bank checks it only after Tier 2.")

    def test_a_number_that_already_has_an_account_ends_the_form(self):
        cache.clear()
        ob = _signup_ob()
        token = sign_onboarding_token(ob)
        with patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}) as sms:
            _exchange(token, {**DETAILS, "phone": PHONE})
            code = sms.call_args[0][1].split("Zitch: ")[1][:6]
        User.objects.create(username=PHONE, phone=PHONE, email="other@example.com")
        resp = _exchange(token, {"phone_code": code, "password": GOOD_PW, "confirm_password": GOOD_PW})
        self.assertEqual(resp["screen"], flows.RESULT_SCREEN)
        self.assertFalse(WaOnboarding.objects.filter(msisdn=NEW_MSISDN).exists())


@override_settings(WHATSAPP_FLOW={**SIGNUP_ON, "SIGNUP_BVN_FORM": False})
class SignupBvnFlagOffTests(FlowContractMixin, TestCase):

    def test_the_password_goes_straight_to_the_pin_as_before(self):
        cache.clear()
        ob = _signup_ob()
        token = sign_onboarding_token(ob)
        with patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}) as sms:
            _exchange(token, {**DETAILS, "phone": PHONE})
            code = sms.call_args[0][1].split("Zitch: ")[1][:6]
        resp = _exchange(token, {"phone_code": code, "password": GOOD_PW, "confirm_password": GOOD_PW})
        self.assertEqual(resp["screen"], flows.PIN_CHAIN)
        self.assertFalse(User.objects.filter(phone=PHONE).exists())
