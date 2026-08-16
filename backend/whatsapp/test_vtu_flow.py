"""Airtime and data as ONE Flow, and the AI path that bypasses it entirely.

Two rules, and they are separate on purpose:

* A MENU pick (3) opens the Flow — what to buy, network, details, PIN, four
  pages of one session instead of four chat round-trips with a place to get
  stuck at each.
* Free-form chat ("recharge tobi 2k") never opens it. The AI interprets and the
  deterministic layer executes, because someone who typed the whole instruction
  has already answered every question the form would ask.
"""
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from utility.models import DataPlan
from whatsapp import router
from whatsapp.flows import (FLOW_PIN_STATE, FLOW_VTU_STATE, PIN_CHAIN, VTU_AIRTIME,
                            VTU_DATA, VTU_NETWORK, VTU_SCREEN, handle_flow_request,
                            sign_flow_token)
from whatsapp.models import PendingAction
from whatsapp.test_flows import MSISDN, _make_user


def _vtu_action(user, **payload):
    return PendingAction.objects.create(
        user=user, msisdn=MSISDN, action_type="airtime", state=FLOW_VTU_STATE,
        payload={"vtu_step": "kind", "pin_attempts": 0, **payload},
        expires_at=timezone.now() + timedelta(minutes=10))


def _submit(pa, data):
    return handle_flow_request({"action": "data_exchange",
                                "flow_token": sign_flow_token(pa), "data": data})


class TheLadderIsOneSessionTests(TestCase):

    def setUp(self):
        self.user = _make_user()

    def test_menu_three_opens_the_flow_on_its_root(self):
        with patch.object(router, "flows_live", return_value=True), \
             patch.object(router, "send_flow", return_value={"success": True}) as sent, \
             patch.object(router, "reply"):
            router._start_vtu(self.user, MSISDN)
        self.assertEqual(sent.call_args.kwargs["screen"], VTU_SCREEN)
        pa = PendingAction.objects.get(msisdn=MSISDN)
        self.assertEqual(pa.state, FLOW_VTU_STATE)

    def test_a_failed_send_still_leaves_airtime_reachable(self):
        """Failing closed would take airtime away from every deploy without
        Flows. Nothing on these pages is secret — the PIN has its own."""
        with patch.object(router, "flows_live", return_value=True), \
             patch.object(router, "send_flow", return_value={"success": False}), \
             patch.object(router, "reply_list") as menu, \
             patch.object(router, "reply"):
            router._start_vtu(self.user, MSISDN)
        menu.assert_called_once()

    def test_airtime_walks_kind_then_network_then_details_then_pin(self):
        pa = _vtu_action(self.user)
        self.assertEqual(_submit(pa, {"kind": "airtime"})["screen"], VTU_NETWORK)
        pa.refresh_from_db()
        self.assertEqual(pa.action_type, "airtime")
        detail = _submit(pa, {"network": "1"})
        self.assertEqual(detail["screen"], VTU_AIRTIME)
        self.assertEqual(detail["data"]["phone"], self.user.phone)
        pa.refresh_from_db()
        resp = _submit(pa, {"amount": "500"})
        # Chains into the PIN page of the SAME session, not a second message.
        self.assertEqual(resp["screen"], PIN_CHAIN)
        pa.refresh_from_db()
        self.assertEqual(pa.state, FLOW_PIN_STATE)
        self.assertEqual(pa.payload["phone"], self.user.phone)
        self.assertEqual(Decimal(pa.payload["amount"]), Decimal("500"))

    def test_data_takes_the_plan_page_instead_of_an_amount(self):
        DataPlan.objects.create(network="1", plan_code="mtn-1gb", name="1GB",
                                validity="30 days", price=Decimal("500"), active=True)
        pa = _vtu_action(self.user)
        _submit(pa, {"kind": "data"})
        pa.refresh_from_db()
        self.assertEqual(pa.action_type, "data")   # picks the data executor later
        resp = _submit(pa, {"network": "1"})
        self.assertEqual(resp["screen"], VTU_DATA)
        self.assertEqual(resp["data"]["plans"], [{"id": "mtn-1gb", "title": "1GB • 30 days • ₦500.00"}])
        self.assertEqual(resp["data"]["phone"], self.user.phone)
        pa.refresh_from_db()
        resp = _submit(pa, {"plan": "mtn-1gb"})
        self.assertEqual(resp["screen"], PIN_CHAIN)
        pa.refresh_from_db()
        self.assertEqual(pa.payload["plan_code"], "mtn-1gb")
        self.assertEqual(pa.payload["phone"], self.user.phone)

    def test_an_international_account_number_is_prefilled_in_local_form(self):
        self.user.phone = "+234 801 000 0001"
        self.user.save(update_fields=["phone"])
        pa = _vtu_action(self.user, vtu_kind="airtime", vtu_step="network")
        resp = _submit(pa, {"network": "1"})
        self.assertEqual(resp["data"]["phone"], "08010000001")

    def test_published_forms_bind_the_phone_prefill(self):
        asset = Path(__file__).with_name("flow_assets") / "pin_flow.json"
        screens = {s["id"]: s for s in json.loads(asset.read_text())["screens"]}
        for screen_id in (VTU_AIRTIME, VTU_DATA):
            form = next(c for c in screens[screen_id]["layout"]["children"]
                        if c.get("type") == "Form")
            self.assertEqual(form["init-values"]["phone"], "${data.phone}")

    def test_a_plan_from_another_network_is_refused(self):
        """Routing is by (network, plan_code); a mismatched pair is not something
        the provider can recover from."""
        DataPlan.objects.create(network="2", plan_code="glo-1gb", name="1GB",
                                validity="30 days", price=Decimal("500"), active=True)
        DataPlan.objects.create(network="1", plan_code="mtn-1gb", name="1GB",
                                validity="30 days", price=Decimal("500"), active=True)
        pa = _vtu_action(self.user, vtu_kind="data", vtu_step="details", net="1")
        resp = _submit(pa, {"plan": "glo-1gb", "phone": "08031234567"})
        self.assertEqual(resp["screen"], VTU_DATA)
        self.assertIn("Pick a plan", resp["data"]["error"])

    def test_a_network_with_no_plans_ends_the_session_rather_than_rendering_an_empty_list(self):
        pa = _vtu_action(self.user, vtu_kind="data", vtu_step="network")
        resp = _submit(pa, {"network": "1"})
        self.assertEqual(resp["screen"], "SUCCESS")
        self.assertEqual(resp["data"]["status"], "❌ Not completed")

    def test_below_the_minimum_re_renders_rather_than_ending(self):
        pa = _vtu_action(self.user, vtu_kind="airtime", vtu_step="details", net="1")
        resp = _submit(pa, {"amount": "10", "phone": "08031234567"})
        self.assertEqual(resp["screen"], VTU_AIRTIME)
        self.assertIn("Minimum", resp["data"]["error"])
        pa.refresh_from_db()
        self.assertEqual(pa.state, FLOW_VTU_STATE)   # still on the form

    def test_a_bad_phone_number_re_renders(self):
        pa = _vtu_action(self.user, vtu_kind="airtime", vtu_step="details", net="1")
        resp = _submit(pa, {"amount": "500", "phone": "123"})
        self.assertEqual(resp["screen"], VTU_AIRTIME)
        self.assertIn("phone number", resp["data"]["error"])

    def test_an_account_with_no_pin_is_told_before_the_pad_appears(self):
        self.user.transaction_pin = ""
        self.user.save(update_fields=["transaction_pin"])
        pa = _vtu_action(self.user, vtu_kind="airtime", vtu_step="details", net="1")
        resp = _submit(pa, {"amount": "500", "phone": "08031234567"})
        self.assertEqual(resp["screen"], "SUCCESS")
        self.assertIn("set pin", resp["data"]["message"])

    def test_a_new_instruction_leaves_the_form(self):
        pa = _vtu_action(self.user)
        with patch.object(router, "reply") as reply, \
             patch.object(router, "handle_inbound") as inbound:
            router._advance(pa, self.user, MSISDN, "check my balance")
        inbound.assert_called_once_with(MSISDN, "check my balance")
        self.assertIn("leaving that purchase", reply.call_args[0][1])


class ChatKeepsGoingThroughTheAiTests(TestCase):
    """Menu picks open the Flow; typed instructions do not."""

    def setUp(self):
        self.user = _make_user()

    def test_a_fully_specified_airtime_message_never_opens_the_form(self):
        with patch.object(router, "_start_vtu") as form, \
             patch.object(router, "_arm_confirm", return_value=True), \
             patch.object(router, "reply"):
            handled = router.dispatch_intent(self.user, MSISDN, {
                "name": "buy_airtime",
                "input": {"amount": 2000, "phone": "08031234567", "network": "MTN"}})
        self.assertTrue(handled)
        form.assert_not_called()
        self.assertEqual(PendingAction.objects.get(msisdn=MSISDN).state, "pin")

    def test_recharging_a_named_person_asks_for_the_number(self):
        """"recharge tobi 2k" used to top up the SENDER's own line: the name was
        dropped, the missing number read as "me", and the confirm card showed the
        number it had guessed — so nothing revealed the mistake until the wrong
        person had the airtime."""
        with patch.object(router, "reply") as reply:
            router._begin_airtime(self.user, MSISDN, 2000, None, None, recipient_ref="tobi")
        said = reply.call_args[0][1]
        self.assertIn("tobi", said)
        self.assertIn("what number", said.lower())
        pa = PendingAction.objects.get(msisdn=MSISDN)
        self.assertEqual(pa.state, "phone")
        self.assertNotEqual(pa.payload.get("phone"), self.user.phone)

    def test_the_amount_already_given_is_not_asked_for_twice(self):
        """The ladder carries what the message already said — 2k was in the
        sentence and 0803 names the network — so supplying the number is the
        LAST step, not the first of three."""
        router._begin_airtime(self.user, MSISDN, 2000, None, None, recipient_ref="tobi")
        pa = PendingAction.objects.get(msisdn=MSISDN)
        with patch.object(router, "_arm_confirm", return_value=True) as arm, \
             patch.object(router, "reply_image") as card, \
             patch.object(router, "reply") as reply:
            router._advance_airtime(pa, self.user, MSISDN, "08031234567")
        arm.assert_called_once()                    # reached the confirm, not another question
        pa.refresh_from_db()
        self.assertEqual(pa.payload["phone"], "08031234567")
        self.assertEqual(pa.payload["net"], "1")    # inferred from the 0803 prefix
        self.assertIn("2,000", card.call_args[0][2])
        self.assertFalse(any("How much" in str(c) for c in reply.call_args_list))

    def test_no_recipient_named_still_means_the_senders_own_line(self):
        """"2k airtime" with no target is the common case and must stay one step.
        The named-person branch must not have swallowed it."""
        with patch.object(router, "_arm_confirm", return_value=True) as arm, \
             patch.object(router, "reply"):
            router._begin_airtime(self.user, MSISDN, 2000, None, "MTN")
        arm.assert_called_once()
        pa = PendingAction.objects.get(msisdn=MSISDN)
        self.assertEqual(pa.payload["phone"], self.user.phone)
