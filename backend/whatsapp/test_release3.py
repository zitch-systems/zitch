"""Release 3 Flow screens: airtime or data on one page, the redesigned send-money
form, and two clean-ups of the published document (no "(optional)" in a label,
no partnership notice on a form).

The same two promises as Release 2, for every new screen:

* with its flag OFF (the default) nothing changes - the screen is never sent and
  never answered, because Meta serves the Flow JSON published by hand AFTER the
  deploy, and an unpublished screen is "Couldn't load content";
* with its flag ON every answer is renderable: a declared screen, exactly its
  declared properties, a declared route from the screen the device is on, and
  no empty Dropdown/RadioButtonsGroup (FlowContractMixin.assertScreen).
"""
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

import whatsapp
from transfers.models import Bank
from utility.models import DataPlan
from wallet.models import Transaction

from . import flows, release3
from .flows import sign_flow_token
from .models import PendingAction
from .test_release2 import MSISDN, _exchange, _MoneyBase

_DOC = json.loads((Path(whatsapp.__file__).parent / "flow_assets" / "pin_flow.json").read_text())
_BY_ID = {s["id"]: s for s in _DOC["screens"]}
NEW = ("SEND_MONEY", "SEND_MONEY_CHAIN", "VTU_FORM", "VTU_FORM_CHAIN", "VTU_PLAN")
VTU_ON = {"RESULT_SCREEN": True, "VTU_ONE_SCREEN": True}
SEND_ON = {"RESULT_SCREEN": True, "TRANSFER_FORM_V2": True}
OWN = "08031234567"   # an MTN prefix, so the network is detected
#: Added after this release (see test_release4); kept out of this release's checks.
RELEASE4 = ("BILL_TYPE", "BILL_TYPE_CHAIN", "CABLE_SCREEN", "CABLE_PLAN", "SIGNUP_BVN",
            "SIGNUP_BVN_RETRY", "SIGNUP_ADDRESS")
RELEASE5 = ("SIGNUP_PIN", "SIGNUP_PIN_RETRY")


class FlagDefaultsTests(SimpleTestCase):

    FLAGS = (flows.vtu_one_screen_live, flows.transfer_form_v2_live)

    @override_settings(WHATSAPP_FLOW={"FLOW_ID": "1", "RESULT_SCREEN": True, "MENU_FORM": True})
    def test_off_by_default(self):
        self.assertEqual([f() for f in self.FLAGS], [False, False])

    @override_settings(WHATSAPP_FLOW={"VTU_ONE_SCREEN": True, "TRANSFER_FORM_V2": True})
    def test_each_reads_its_own_key(self):
        self.assertEqual([f() for f in self.FLAGS], [True, True])

    def test_the_settings_module_reads_each_flag_from_its_env_var(self):
        import inspect

        from django.conf import settings

        from zitch_api import settings as module

        source = inspect.getsource(module)
        for name in ("VTU_ONE_SCREEN", "TRANSFER_FORM_V2"):
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


class Release3FlowJsonTests(SimpleTestCase):

    #: The 35 screens published before this release, and a digest of what the
    #: device holds us to on each: title, declared data, terminal flag and every
    #: submit action. Release 3 is additive - a change to any of these would
    #: break a session on the currently published Flow - so the digest is frozen.
    OLD = ["PRIVACY_NOTICE", "SIGNUP_DETAILS", "DETAILS_CHAIN", "SIGNUP_SCREEN", "SIGNUP_CHAIN",
           "SIGNUP_PHONE", "VERIFY_PASSWORD", "SIGNUP_PHONE_CODE", "SIGNUP_PASSWORD",
           "EMAIL_SCREEN", "LOGIN_SCREEN", "LOGIN_RETRY", "MENU", "TRANSFER_FORM",
           "TRANSFER_CHAIN", "VTU_SCREEN", "VTU_CHAIN", "VTU_NETWORK", "VTU_AIRTIME", "VTU_DATA",
           "BILL_SCREEN", "BILL_CHAIN", "BILL_AMOUNT", "IDENTITY_SCREEN", "IDENTITY_RETRY",
           "IDENTITY_CHAIN", "CODE_SCREEN", "CODE_RETRY", "PIN_SCREEN", "PIN_CHAIN", "PIN_RETRY",
           "PIN_CONFIRM", "PIN_CONFIRM_RETRY", "RESULT", "SUCCESS"]
    # Release 5 retitled these pages to the approved design, gave the three PIN
    # pages their own `title` and `cta`, and gave MENU's rows a second line
    # (`description`) - all gated by WHATSAPP_FLOW['DESIGN_V5'].
    OLD_CONTRACT_SHA256 = "4ee480da79bc2cf262d8dd7ae3418f5317bd94d7496a944513f904c5178a5115"

    def test_the_existing_screens_contract_is_unchanged(self):
        contract = {}
        for sid in self.OLD:
            screen = _BY_ID[sid]
            contract[sid] = {
                "title": screen["title"], "data": screen["data"],
                "terminal": screen.get("terminal", False),
                "actions": [n["on-click-action"] for n in _walk(screen["layout"])
                            if "on-click-action" in n
                            and n["on-click-action"].get("name") != "open_url"]}
        digest = hashlib.sha256(json.dumps(contract, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest, self.OLD_CONTRACT_SHA256)
        self.assertEqual([s["id"] for s in _DOC["screens"]
                          if s["id"] not in NEW + RELEASE4 + RELEASE5], self.OLD)

    def test_existing_routes_are_kept_and_only_the_menu_gains_new_ones(self):
        expected_menu = ["TRANSFER_CHAIN", "VTU_CHAIN", "BILL_CHAIN", "SEND_MONEY_CHAIN",
                         "VTU_FORM_CHAIN", "BILL_TYPE_CHAIN", "RESULT", "SUCCESS"]
        self.assertEqual(_DOC["routing_model"]["MENU"], expected_menu)
        incoming = {t for targets in _DOC["routing_model"].values() for t in targets}
        # The roots stay roots (openable from the chat); the twins and the plan
        # page are only ever routed into.
        self.assertNotIn("SEND_MONEY", incoming)
        self.assertNotIn("VTU_FORM", incoming)
        self.assertLessEqual({"SEND_MONEY_CHAIN", "VTU_FORM_CHAIN", "VTU_PLAN"}, incoming)

    def test_version_size_and_format(self):
        path = Path(whatsapp.__file__).parent / "flow_assets" / "pin_flow.json"
        raw = path.read_text()
        self.assertEqual(_DOC["version"], "6.3")   # 6.3 for the airtime ChipsSelector
        self.assertEqual(len(_DOC["screens"]), 49)   # 40 + Release 4's 7 + Release 5's 2
        self.assertLess(len(raw.encode()), 750_000)
        self.assertEqual(raw, json.dumps(_DOC, indent=2, ensure_ascii=True) + "\n")

    def test_only_known_components_and_a_footer_last(self):
        allowed = {"SingleColumnLayout", "Image", "TextHeading", "TextBody", "TextCaption",
                   "TextInput", "Dropdown", "RadioButtonsGroup", "ChipsSelector", "Form", "Footer"}
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
                declared = set(_BY_ID[sid]["data"])
                self.assertLessEqual(set(re.findall(r"\$\{data\.(\w+)\}", text)), declared)
                form = _form(sid)
                names = {c.get("name") for c in form["children"] if c.get("name")}
                payload = form["children"][-1]["on-click-action"]["payload"]
                self.assertEqual(payload, {k: "${form.%s}" % k for k in payload})
                self.assertLessEqual(set(payload), names)
                self.assertLessEqual(set(form.get("init-values", {})), names)

    def test_twins_are_identical_to_their_roots(self):
        for root, twin in (("SEND_MONEY", "SEND_MONEY_CHAIN"), ("VTU_FORM", "VTU_FORM_CHAIN")):
            self.assertEqual({**_BY_ID[twin], "id": root}, _BY_ID[root])
            self.assertEqual(_DOC["routing_model"][twin], _DOC["routing_model"][root])

    def test_airtime_data_design_copy(self):
        screen = _BY_ID["VTU_FORM"]
        self.assertEqual(screen["title"], "Airtime / Data")
        self.assertIn("Airtime or data?", [c.get("text") for c in screen["layout"]["children"]])
        kind = _field("VTU_FORM", "kind")
        self.assertEqual(kind["type"], "RadioButtonsGroup")
        self.assertEqual([o["title"] for o in kind["data-source"]], ["Airtime", "Data"])
        phone = _field("VTU_FORM", "phone")
        self.assertEqual((phone["label"], phone["helper-text"]),
                         ("Phone number", "Change it to buy for someone else"))
        self.assertEqual(_field("VTU_FORM", "network")["type"], "Dropdown")
        self.assertEqual(_field("VTU_FORM", "amount")["helper-text"], "${data.amount_help}")
        self.assertEqual(_form("VTU_FORM")["init-values"],
                         {"kind": "${data.kind}", "phone": "${data.phone}",
                          "network": "${data.network}"})
        plan = _BY_ID["VTU_PLAN"]
        self.assertEqual(plan["title"], "Choose a plan")
        self.assertEqual(_field("VTU_PLAN", "plan")["type"], "RadioButtonsGroup")

    def test_send_money_design_copy_and_field_order(self):
        self.assertEqual(_BY_ID["SEND_MONEY"]["title"], "Send money")
        inputs = [c for c in _form("SEND_MONEY")["children"] if c.get("type") == "TextInput"]
        self.assertEqual([(c["name"], c["label"]) for c in inputs],
                         [("account_number", "Account number"), ("bank", "Bank"),
                          ("amount", "Amount (₦)"), ("narration", "Note")])
        account, bank, amount, note = inputs
        self.assertEqual((account["helper-text"], account["min-chars"], account["max-chars"]),
                         ("10 digits", 10, 10))
        self.assertEqual(bank["helper-text"], "Detected from the account or type to search")
        self.assertFalse(bank["required"])
        self.assertEqual(amount["helper-text"], "${data.balance}")
        self.assertFalse(note["required"])

    def test_the_narration_labels_lost_their_doubled_optional(self):
        for sid in ("VTU_AIRTIME", "VTU_DATA"):
            self.assertEqual(_field(sid, "narration")["label"], "Narration")
        # The approved electricity amount page asks only "How much?".
        self.assertNotIn("narration", [c.get("name") for c in _form("BILL_AMOUNT")["children"]])


# --------------------------------------------------------------------------- #
# 1. airtime or data on one page
# --------------------------------------------------------------------------- #
class _VtuBase(_MoneyBase):

    def setUp(self):
        super().setUp()
        self.user.phone = OWN
        self.user.save(update_fields=["phone"])
        DataPlan.objects.create(network="1", plan_code="mtn-1gb", name="1GB",
                                validity="30 days", price=Decimal("500"), active=True)

    def open(self, text="3"):
        sent, flow = self.say(text)
        kwargs = flow.call_args.kwargs
        self.assertEqual((kwargs["screen"], kwargs["cta"]), (flows.VTU_FORM, "Buy airtime or data"))
        self.assertEqual(kwargs["header"], "")
        self.assertScreen({"screen": kwargs["screen"], "data": kwargs["screen_data"]})
        self.assertIn("Tap *Buy airtime or data*", kwargs["body"])
        return PendingAction.objects.get(msisdn=MSISDN), kwargs["screen_data"]

    def submit(self, pa, standing_on=flows.VTU_FORM, **data):
        return self.assertScreen(_exchange(sign_flow_token(pa), data), standing_on=standing_on)


@override_settings(WHATSAPP_FLOW=VTU_ON)
class VtuFormTests(_VtuBase):

    def test_menu_three_opens_the_one_page_form_prefilled(self):
        pa, first = self.open()
        self.assertEqual((pa.state, pa.action_type), (flows.FLOW_VTU_STATE, "airtime"))
        self.assertTrue(release3.is_vtu_form(pa))
        self.assertEqual(first["kind"], "airtime")
        self.assertEqual(first["phone"], OWN)
        self.assertEqual(first["network"], "1")
        self.assertEqual(first["network_hint"], "Detected from the number")
        self.assertEqual(first["amount_help"], "Available balance ₦20,000.00 · Minimum ₦50")
        self.assertEqual([n["title"] for n in first["networks"]], ["MTN", "GLO", "Airtel", "9mobile"])

    def test_typing_data_opens_it_on_data(self):
        _, first = self.open("data")
        self.assertEqual(first["kind"], "data")

    def test_an_unknown_prefix_still_preselects_a_listed_network_and_says_check(self):
        self.user.phone = "08010000001"
        self.user.save(update_fields=["phone"])
        _, first = self.open()
        self.assertEqual(first["network"], "1")
        self.assertIn("Check the network", first["network_hint"])

    def test_airtime_on_one_page_then_pin_pays_with_the_chat_executor(self):
        pa, _ = self.open()
        for data, wanted in (({"kind": "x"}, "airtime or data"),
                             ({"kind": "airtime", "phone": "123"}, "valid phone"),
                             ({"kind": "airtime", "phone": OWN, "network": "9"}, "network"),
                             ({"kind": "airtime", "phone": OWN, "network": "1"}, "airtime amount"),
                             ({"kind": "airtime", "phone": OWN, "network": "1", "amount": "20"},
                              "Minimum airtime"),
                             ({"kind": "airtime", "phone": OWN, "network": "1",
                               "amount": "500", "broke": True}, "Insufficient balance")):
            with self.subTest(data=data), \
                 patch("whatsapp.router._insufficient", return_value=bool(data.pop("broke", False))):
                resp = self.submit(pa, **data)
                self.assertEqual(resp["screen"], flows.VTU_FORM)
                self.assertIn(wanted, resp["data"]["error"])
                self.assertTrue(resp["data"]["error"].startswith("⚠️ "))
        confirm = self.submit(pa, kind="airtime", phone=OWN, network="1", amount="500")
        self.assertEqual(confirm["screen"], flows.PIN_CHAIN)
        self.assertEqual(confirm["data"]["amount"], "₦500.00")
        self.assertEqual(confirm["data"]["recipient"], "Airtime: MTN")
        self.assertIn(OWN, confirm["data"]["details"])
        pa.refresh_from_db()
        self.assertEqual((pa.state, pa.action_type), (flows.FLOW_PIN_STATE, "airtime"))
        self.assertEqual(pa.payload["meta"], {"phone": OWN, "network": "1"})

        with patch("whatsapp.router.reply"), patch("whatsapp.router.reply_receipt"):
            done = _exchange(sign_flow_token(pa), {"pin": "1234"})
        self.assertScreen(done, standing_on=flows.PIN_CHAIN)
        self.assertEqual(done["data"]["status"], "✅ Successful")
        txn = Transaction.objects.get(user=self.user, idempotency_key=f"wa-{pa.pk}")
        self.assertEqual(txn.amount, Decimal("500"))

    def test_a_number_on_another_network_is_questioned_once(self):
        pa, _ = self.open()
        glo = "08051234567"
        resp = self.submit(pa, kind="airtime", phone=glo, network="1", amount="500")
        self.assertEqual(resp["screen"], flows.VTU_FORM)
        self.assertIn("looks like a GLO number, but MTN is selected", resp["data"]["error"])
        # Tapping Continue again with the same choice keeps it (ported numbers).
        kept = self.submit(pa, kind="airtime", phone=glo, network="1", amount="500")
        self.assertEqual(kept["screen"], flows.PIN_CHAIN)
        pa.refresh_from_db()
        self.assertEqual(pa.payload["net"], "1")

    def test_choosing_the_right_network_goes_straight_through(self):
        pa, _ = self.open()
        resp = self.submit(pa, kind="airtime", phone="08051234567", network="2", amount="500")
        self.assertEqual(resp["screen"], flows.PIN_CHAIN)
        self.assertEqual(resp["data"]["recipient"], "Airtime: GLO")

    def test_data_takes_the_plan_page_then_pin(self):
        pa, _ = self.open()
        plans = self.submit(pa, kind="data", phone=OWN, network="1", amount="")
        self.assertEqual(plans["screen"], flows.VTU_PLAN)
        self.assertEqual(plans["data"]["plans"],
                         [{"id": "mtn-1gb", "title": "1GB", "description": "₦500.00 · 30 days"}])
        self.assertEqual(plans["data"]["summary"], "MTN data plans")
        pa.refresh_from_db()
        self.assertEqual(pa.action_type, "data")
        bad = self.submit(pa, standing_on=flows.VTU_PLAN, plan="glo-1gb")
        self.assertEqual(bad["screen"], flows.VTU_PLAN)
        self.assertIn("Pick a plan", bad["data"]["error"])
        confirm = self.submit(pa, standing_on=flows.VTU_PLAN, plan="mtn-1gb")
        self.assertEqual(confirm["screen"], flows.PIN_CHAIN)
        self.assertEqual(confirm["data"]["recipient"], "Plan: 1GB")
        pa.refresh_from_db()
        self.assertEqual(pa.payload["plan_code"], "mtn-1gb")
        self.assertEqual(pa.state, flows.FLOW_PIN_STATE)

    def test_back_from_the_plan_page_resubmits_the_first_page(self):
        """The device's back arrow does not tell the endpoint; a submit without
        `plan` is the first page again."""
        pa, _ = self.open()
        self.submit(pa, kind="data", phone=OWN, network="1")
        resp = self.submit(pa, kind="airtime", phone=OWN, network="1", amount="500")
        self.assertEqual(resp["screen"], flows.PIN_CHAIN)
        self.assertEqual(resp["data"]["recipient"], "Airtime: MTN")

    def test_a_network_with_no_plans_ends_rather_than_rendering_an_empty_list(self):
        pa, _ = self.open()
        resp = self.submit(pa, kind="data", phone="08051234567", network="2")
        self.assertEqual(resp["screen"], flows.RESULT_SCREEN)
        self.assertIn("No GLO data plans", resp["data"]["message"])
        self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())

    def test_init_rerenders_the_page_the_session_is_on(self):
        pa, _ = self.open()
        init = _exchange(sign_flow_token(pa), action="INIT")
        self.assertScreen(init)
        self.assertEqual(init["screen"], flows.VTU_FORM)
        self.submit(pa, kind="data", phone=OWN, network="1")
        pa.refresh_from_db()
        self.assertEqual(_exchange(sign_flow_token(pa), action="INIT")["screen"], flows.VTU_PLAN)

    def test_a_failed_send_falls_back_to_the_four_page_ladder(self):
        from .router import handle_inbound

        calls = []

        def send(msisdn, token, **kwargs):
            calls.append(kwargs["screen"])
            return {"success": kwargs["screen"] != flows.VTU_FORM}

        with patch("whatsapp.router.reply"), patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.send_flow", side_effect=send):
            handle_inbound(MSISDN, "3")
        self.assertEqual(calls, [flows.VTU_FORM, flows.VTU_SCREEN])
        pa = PendingAction.objects.get(msisdn=MSISDN)
        self.assertFalse(release3.is_vtu_form(pa))

    def test_typing_airtime_falls_back_to_the_chat_ladder_when_the_send_fails(self):
        from .router import handle_inbound

        with patch("whatsapp.router.reply"), patch("whatsapp.router.reply_list"), \
             patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.send_flow", return_value={"success": False}):
            handle_inbound(MSISDN, "airtime")
        pa = PendingAction.objects.get(msisdn=MSISDN)
        self.assertEqual((pa.action_type, pa.state), ("airtime", "network"))

    def test_a_blocked_account_is_told_and_nothing_opens(self):
        with patch("whatsapp.router._blocked_from_spending", return_value=True):
            sent, flow = self.say("3")
        flow.assert_not_called()
        self.assertFalse(PendingAction.objects.exists())

    def test_a_session_survives_the_flag_being_turned_back_off(self):
        pa, _ = self.open()
        with self.settings(WHATSAPP_FLOW={"RESULT_SCREEN": True}):
            resp = self.submit(pa, kind="airtime", phone=OWN, network="1", amount="500")
        self.assertEqual(resp["screen"], flows.PIN_CHAIN)


@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True})
class VtuFormFlagOffTests(_VtuBase):

    def test_menu_three_is_the_four_page_ladder_as_before(self):
        _, flow = self.say("3")
        self.assertEqual(flow.call_args.kwargs["screen"], flows.VTU_SCREEN)
        self.assertEqual(flow.call_args.kwargs["screen_data"], {"error": ""})
        self.assertFalse(release3.is_vtu_form(PendingAction.objects.get(msisdn=MSISDN)))

    def test_typing_airtime_or_data_is_the_chat_ladder_as_before(self):
        for word in ("airtime", "data"):
            with self.subTest(word=word):
                PendingAction.objects.all().delete()
                _, flow = self.say(word)
                flow.assert_not_called()
                self.assertEqual(PendingAction.objects.get(msisdn=MSISDN).action_type, word)


# --------------------------------------------------------------------------- #
# 2. the send-money form
# --------------------------------------------------------------------------- #
RESOLVED = {"success": True, "name": "JOHN DOE"}


class _SendBase(_MoneyBase):

    def open(self):
        sent, flow = self.say("2")
        kwargs = flow.call_args.kwargs
        self.assertEqual((kwargs["screen"], kwargs["cta"]), (flows.SEND_MONEY, "Send money"))
        self.assertScreen({"screen": kwargs["screen"], "data": kwargs["screen_data"]})
        self.assertIn("Tap *Send money*", kwargs["body"])
        return PendingAction.objects.get(msisdn=MSISDN), kwargs["screen_data"]

    def submit(self, pa, standing_on=flows.SEND_MONEY, **data):
        payload = {"account_number": "", "bank": "", "amount": "", "narration": "", **data}
        return self.assertScreen(_exchange(sign_flow_token(pa), payload), standing_on=standing_on)


@override_settings(WHATSAPP_FLOW=SEND_ON)
class SendMoneyFormTests(_SendBase):

    def test_send_money_opens_the_new_form_with_the_balance(self):
        pa, first = self.open()
        self.assertEqual(first, {"balance": "Available balance ₦20,000.00", "error": ""})
        self.assertEqual((pa.action_type, pa.state), ("transfer", flows.FLOW_FORM_STATE))
        self.assertTrue(release3.is_send_money(pa))

    def test_refusals_stay_on_the_form_with_the_transfer_forms_own_wording(self):
        pa, _ = self.open()
        for data, wanted in (({"amount": "x", "account_number": "0123456789"}, "amount as a number"),
                             ({"amount": "5000", "account_number": "123"}, "exactly 10 digits"),
                             ({"amount": "5000", "account_number": "0123456789",
                               "bank": "zzzz"}, "could not find"),
                             ({"amount": "30000", "account_number": "0123456789"},
                              "Insufficient balance")):
            with self.subTest(data=data):
                resp = self.submit(pa, **data)
                self.assertEqual(resp["screen"], flows.SEND_MONEY)
                self.assertIn(wanted, resp["data"]["error"])
                self.assertEqual(resp["data"]["balance"], "Available balance ₦20,000.00")

    def test_a_typed_bank_name_check_then_pin(self):
        pa, _ = self.open()
        with patch("utility.providers.payout_resolve_account", return_value=RESOLVED) as enquiry:
            confirm = self.submit(pa, account_number="0123456789", bank="gtbank",
                                  amount="5000", narration="Rent")
        enquiry.assert_called_once_with("0123456789", "058")
        self.assertEqual(confirm["screen"], flows.PIN_CHAIN)
        self.assertEqual(confirm["data"]["recipient"], "To: JOHN DOE")
        self.assertIn("Account: GTBank · ", confirm["data"]["details"])
        self.assertEqual(confirm["data"]["narration"], "Note: Rent")
        pa.refresh_from_db()
        self.assertEqual(pa.state, flows.FLOW_PIN_STATE)

    def test_a_blank_bank_is_detected_from_the_account_number(self):
        pa, _ = self.open()
        with patch("whatsapp.router.nuban_bank_candidates", return_value=[Bank.objects.get()]), \
             patch("utility.providers.payout_resolve_account", return_value=RESOLVED):
            confirm = self.submit(pa, account_number="0123456789", amount="5000")
        self.assertEqual(confirm["screen"], flows.PIN_CHAIN)
        self.assertIn("Account: GTBank · ", confirm["data"]["details"])

    def test_a_legacy_bank_search_key_is_ignored(self):
        """SEND_MONEY has no bank list; the old picker's search branch must not
        answer it with a 'pick from the list' it cannot show."""
        pa, _ = self.open()
        resp = self.submit(pa, bank_search="gt", amount="x", account_number="0123456789")
        self.assertIn("amount as a number", resp["data"]["error"])

    def test_a_failed_send_falls_back_to_the_previous_form(self):
        from .router import handle_inbound

        calls = []

        def send(msisdn, token, **kwargs):
            calls.append(kwargs["screen"])
            return {"success": kwargs["screen"] != flows.SEND_MONEY}

        with patch("whatsapp.router.reply"), patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.send_flow", side_effect=send):
            handle_inbound(MSISDN, "2")
        self.assertEqual(calls, [flows.SEND_MONEY, flows.TRANSFER_FORM])
        self.assertFalse(release3.is_send_money(PendingAction.objects.get(msisdn=MSISDN)))

    def test_init_and_a_flag_flip_back_keep_the_session_on_its_screens(self):
        pa, _ = self.open()
        self.assertEqual(self.assertScreen(_exchange(sign_flow_token(pa), action="INIT"))["screen"],
                         flows.SEND_MONEY)
        with self.settings(WHATSAPP_FLOW={"RESULT_SCREEN": True}):
            resp = self.submit(pa, amount="x", account_number="0123456789")
        self.assertEqual(resp["screen"], flows.SEND_MONEY)


@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True})
class SendMoneyFlagOffTests(_SendBase):

    def test_send_money_is_the_previous_form_as_before(self):
        _, flow = self.say("2")
        self.assertEqual(flow.call_args.kwargs["screen"], flows.TRANSFER_FORM)
        self.assertEqual(set(flow.call_args.kwargs["screen_data"]), {"banks", "error", "hint"})
        self.assertFalse(release3.is_send_money(PendingAction.objects.get(msisdn=MSISDN)))


# --------------------------------------------------------------------------- #
# the menu form opens the new twins
# --------------------------------------------------------------------------- #
@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "MENU_FORM": True,
                                  "VTU_ONE_SCREEN": True, "TRANSFER_FORM_V2": True})
class MenuOpensTheNewFormsTests(_VtuBase):

    def menu_token(self):
        _, flow = self.say("menu")
        self.assertEqual(flow.call_args.kwargs["screen"], flows.MENU_SCREEN)
        return flow.call_args.args[1]

    def test_send_money_opens_on_its_twin(self):
        token = self.menu_token()
        resp = self.assertScreen(_exchange(token, {"item": "2"}), standing_on=flows.MENU_SCREEN)
        self.assertEqual(resp["screen"], flows.SEND_MONEY_CHAIN)
        self.assertEqual(resp["data"]["balance"], "Available balance ₦20,000.00")
        err = self.assertScreen(_exchange(token, {"amount": "x", "account_number": "1"}),
                                standing_on=flows.SEND_MONEY_CHAIN)
        self.assertEqual(err["screen"], flows.SEND_MONEY_CHAIN)
        with patch("utility.providers.payout_resolve_account", return_value=RESOLVED):
            confirm = _exchange(token, {"amount": "5000", "account_number": "0123456789",
                                        "bank": "GTBank", "narration": ""})
        self.assertScreen(confirm, standing_on=flows.SEND_MONEY_CHAIN)
        self.assertEqual(confirm["screen"], flows.PIN_CHAIN)

    def test_airtime_and_data_opens_on_its_twin(self):
        token = self.menu_token()
        resp = self.assertScreen(_exchange(token, {"item": "3"}), standing_on=flows.MENU_SCREEN)
        self.assertEqual(resp["screen"], flows.VTU_FORM_CHAIN)
        self.assertEqual((resp["data"]["phone"], resp["data"]["network"]), (OWN, "1"))
        err = self.assertScreen(_exchange(token, {"kind": "airtime", "phone": OWN, "network": "1",
                                                  "amount": ""}),
                                standing_on=flows.VTU_FORM_CHAIN)
        self.assertEqual(err["screen"], flows.VTU_FORM_CHAIN)
        plans = self.assertScreen(_exchange(token, {"kind": "data", "phone": OWN, "network": "1",
                                                    "amount": ""}),
                                  standing_on=flows.VTU_FORM_CHAIN)
        self.assertEqual(plans["screen"], flows.VTU_PLAN)
        confirm = self.assertScreen(_exchange(token, {"plan": "mtn-1gb"}),
                                    standing_on=flows.VTU_PLAN)
        self.assertEqual(confirm["screen"], flows.PIN_CHAIN)

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "MENU_FORM": True})
    def test_with_the_release3_flags_off_the_menu_opens_the_old_twins(self):
        token = self.menu_token()
        self.assertEqual(_exchange(token, {"item": "3"})["screen"], flows.VTU_CHAIN)
        token = self.menu_token()
        self.assertEqual(_exchange(token, {"item": "2"})["screen"], flows.TRANSFER_CHAIN)
