"""End-to-end audit of the WhatsApp Flow, driven through the real handler.

Three things WhatsApp enforces on the device, and reports as one opaque
"Couldn't load content. Try again later." with no screen and no key named:

1. the screen must exist in the published Flow
2. the response must declare EXACTLY the properties that screen declares
3. the navigation must be a route the published Flow declares (a same-screen
   re-render is always legal)

and one it enforces silently, by drawing nothing at all:

4. a Dropdown bound to an empty array does not render — the customer gets a
   blank panel with a spinner and no way forward but the X

Every test here walks a real session through `handle_flow_request` and asserts
all four on every response, so a new branch that answers a malformed or
unreachable screen fails here rather than on a customer's phone.
"""
import json
import os
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from transfers.models import Bank
from utility.models import DataPlan
from wallet.services import credit, get_or_create_wallet

from . import flows
from .flows import (FLOW_PIN_STATE, SUCCESS_SCREEN, handle_flow_request,
                    sign_flow_token)
from .models import PendingAction

User = get_user_model()
MSISDN = "2348011112222"

_FLOW_JSON = os.path.join(os.path.dirname(__file__), "flow_assets", "pin_flow.json")


def _published():
    with open(_FLOW_JSON, encoding="utf-8") as fh:
        doc = json.load(fh)
    return (
        {s["id"]: set((s.get("data") or {}).keys()) for s in doc["screens"]},
        {k: set(v) for k, v in (doc.get("routing_model") or {}).items()},
        {s["id"] for s in doc["screens"] if s.get("terminal")},
        doc,
    )


def _dropdown_fields(doc) -> dict:
    """screen id -> the data keys its Dropdowns are bound to.

    These are the arrays that must never be empty. Read from the published
    layout rather than hardcoded, so a Dropdown added later is covered without
    anyone remembering to come back here.
    """
    out: dict = {}

    def walk(node, screen):
        if isinstance(node, dict):
            if node.get("type") in ("Dropdown", "RadioButtonsGroup", "CheckboxGroup"):
                src = str(node.get("data-source") or "")
                if src.startswith("${data.") and src.endswith("}"):
                    out.setdefault(screen, set()).add(src[len("${data."):-1])
            for value in node.values():
                walk(value, screen)
        elif isinstance(node, list):
            for value in node:
                walk(value, screen)

    for s in doc["screens"]:
        walk(s.get("layout"), s["id"])
    return out


def _make_user(balance="50000"):
    user = User.objects.create(username="2348011112222", phone="08011112222",
                               email="e2e@zitch.test", first_name="Ada", last_name="Eze",
                               tier=3, email_verified=True, phone_verified=True,
                               bvn_verified=True, nin_verified=True, face_verified=True)
    user.set_transaction_pin("1234")
    user.save()
    get_or_create_wallet(user)
    credit(user, Decimal(balance), "Seed")
    return user


class FlowContractMixin:
    """assertScreen — the four invariants, on every response."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.declared, cls.routes, cls.terminals, doc = _published()
        cls.dropdowns = _dropdown_fields(doc)

    def assertScreen(self, response, standing_on="", note=""):
        where = f" [{note}]" if note else ""
        screen = response.get("screen")
        self.assertIn(screen, self.declared, f"unknown screen {screen!r}{where}")

        sent = set((response.get("data") or {}).keys())
        self.assertEqual(
            sent, self.declared[screen],
            f"{screen} data mismatch{where}: missing={sorted(self.declared[screen] - sent)} "
            f"undeclared={sorted(sent - self.declared[screen])}")

        if standing_on and standing_on != screen:
            self.assertIn(
                screen, self.routes.get(standing_on, set()),
                f"{standing_on} -> {screen} is not a declared route{where}")

        for field in self.dropdowns.get(screen, ()):
            self.assertTrue(
                response["data"].get(field),
                f"{screen}.{field} is empty{where} — a Flow Dropdown bound to an "
                "empty array renders a blank panel the customer cannot use")
        return response


@override_settings(WHATSAPP={"MODE": "sandbox", "VERIFY_TOKEN": "v", "TOKEN": "",
                             "APP_SECRET": "", "PHONE_NUMBER_ID": "1",
                             "BASE_URL": "https://graph.test"},
                   WHATSAPP_FLOW={"ID": "1", "CTA": "Confirm with PIN"},
                   WHATSAPP_PROCESS_INLINE=True)
class PaymentFlowE2ETests(FlowContractMixin, TestCase):
    """The money path: armed -> INIT -> wrong PIN -> right PIN -> outcome."""

    def setUp(self):
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#000",
                            active=True, popular=True)
        self.user = _make_user()

    def _armed(self, action_type="transfer", payload=None):
        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type=action_type,
            state=FLOW_PIN_STATE,
            payload=payload or {"amount": "1000", "account": "0123456789",
                                "bank_code": "058", "bank_name": "GTBank",
                                "name": "ADA EZE", "pin_attempts": 0,
                                "flow_screen": flows.PIN_SCREEN},
            expires_at=timezone.now() + timezone.timedelta(minutes=10))

    def _exchange(self, pa, data):
        return handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_flow_token(pa), "data": data})

    def _init(self, pa):
        return handle_flow_request({"action": "INIT", "flow_token": sign_flow_token(pa)})

    def test_init_renders_the_payment_card(self):
        pa = self._armed()
        self.assertScreen(self._init(pa), note="INIT")

    def test_a_wrong_pin_navigates_to_the_retry_twin(self):
        """The retry is a DIFFERENT screen id on purpose: WhatsApp keeps a
        form's client-side value on a same-id reply, so a re-render would come
        back holding the digits that were just refused."""
        pa = self._armed()
        resp = self._exchange(pa, {"pin": "9999"})
        self.assertScreen(resp, standing_on=flows.PIN_SCREEN, note="wrong PIN")
        self.assertEqual(resp["screen"], flows.PIN_RETRY)
        self.assertTrue(resp["data"]["error"])

    def test_the_whole_wrong_pin_budget_stays_renderable(self):
        pa = self._armed()
        standing = flows.PIN_SCREEN
        for attempt in range(4):
            resp = self._exchange(pa, {"pin": "9999"})
            self.assertScreen(resp, standing_on=standing, note=f"wrong PIN #{attempt + 1}")
            standing = resp["screen"]
            pa.refresh_from_db() if PendingAction.objects.filter(pk=pa.pk).exists() else None
            if resp["screen"] == SUCCESS_SCREEN:
                break

    def test_a_correct_pin_holds_the_panel_open_while_pending(self):
        """Only a settled success closes the panel. Anything else keeps the
        outcome where the customer was already looking."""
        pa = self._armed()
        with patch("whatsapp.router.execute_payout") as payout:
            payout.return_value = self._pending_txn()
            resp = self._exchange(pa, {"pin": "1234"})
        self.assertScreen(resp, standing_on=flows.PIN_SCREEN, note="pending")
        self.assertNotEqual(resp["screen"], SUCCESS_SCREEN)

    def _pending_txn(self):
        from wallet.models import Transaction

        return Transaction.objects.create(
            user=self.user, amount=Decimal("1000"), service="Transfer to ADA EZE",
            direction=Transaction.OUT, transaction_status=Transaction.PENDING,
            reference="ZTCHE2E0001")

    def test_an_expired_token_answers_a_renderable_terminal(self):
        pa = self._armed()
        pa.expires_at = timezone.now() - timezone.timedelta(minutes=1)
        pa.save(update_fields=["expires_at"])
        self.assertScreen(self._exchange(pa, {"pin": "1234"}), note="expired")

    def test_a_forged_token_answers_a_renderable_terminal(self):
        self.assertScreen(handle_flow_request(
            {"action": "data_exchange", "flow_token": "999.deadbeef", "data": {"pin": "1234"}}),
            note="forged token")

    def test_a_ping_is_answered_without_a_screen(self):
        self.assertEqual(handle_flow_request({"action": "ping"}), {"data": {"status": "active"}})

    def test_junk_payloads_never_raise(self):
        for payload in (None, [], "", {"action": "nonsense"}, {"data": {"error_message": "x"}},
                        {"action": "data_exchange"}, {"action": "INIT"}):
            with self.subTest(payload=payload):
                resp = handle_flow_request(payload)
                self.assertIsInstance(resp, dict)
                if "screen" in resp:
                    self.assertScreen(resp, note=f"junk {payload!r}")

    def test_the_unlock_card_is_not_labelled_a_payment(self):
        """An idle re-auth moves no money, so the card must not say it does."""
        from .router import _send_pin_flow

        pa = self._armed("unlock", {"pin_attempts": 0, "resume": "balance"})
        sent = {}
        with patch("whatsapp.router.send_flow",
                   side_effect=lambda *a, **k: sent.update(k) or {"success": True}):
            _send_pin_flow(pa, self.user)
        self.assertEqual(sent["header"], "Confirm identity")

    def test_the_balance_line_says_available(self):
        from .router import _flow_fields

        fields = _flow_fields(self._armed())
        self.assertIn("Available balance", fields["balance"])


@override_settings(WHATSAPP={"MODE": "sandbox", "VERIFY_TOKEN": "v", "TOKEN": "",
                             "APP_SECRET": "", "PHONE_NUMBER_ID": "1",
                             "BASE_URL": "https://graph.test"},
                   WHATSAPP_FLOW={"ID": "1", "CTA": "Confirm with PIN"})
class VtuFlowE2ETests(FlowContractMixin, TestCase):
    """Airtime/data: kind -> network -> details -> PIN, all one open session."""

    def setUp(self):
        self.user = _make_user()
        DataPlan.objects.create(network="1", plan_type="1", name="1.5GB", validity="30 days",
                                plan_code="mtn-1500", price=Decimal("1000"), active=True)

    def _session(self, step=flows.VTU_KIND_STEP, payload=None):
        base = {"vtu_step": step, "vtu_kind": "data", "net": "1"}
        base.update(payload or {})
        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="vtu",
            state=flows.FLOW_VTU_STATE, payload=base,
            expires_at=timezone.now() + timezone.timedelta(minutes=10))

    def _exchange(self, pa, data):
        return handle_flow_request({"action": "data_exchange",
                                    "flow_token": sign_flow_token(pa), "data": data})

    def test_each_page_of_the_ladder_renders(self):
        pa = self._session()
        kind = self._exchange(pa, {"kind": "data"})
        self.assertScreen(kind, standing_on=flows.VTU_SCREEN, note="kind -> network")
        pa.refresh_from_db()
        plans = self._exchange(pa, {"network": "1"})
        self.assertScreen(plans, standing_on=kind["screen"], note="network -> data")

    def test_a_network_with_no_plans_says_so_instead_of_a_blank_panel(self):
        """The regression: a Dropdown bound to an empty array renders nothing at
        all, so the customer sees a spinner on a blank sheet."""
        DataPlan.objects.all().delete()
        pa = self._session(flows.VTU_NETWORK_STEP)
        resp = self._exchange(pa, {"network": "1"})
        self.assertScreen(resp, standing_on=flows.VTU_NETWORK, note="no plans")
        self.assertEqual(resp["screen"], SUCCESS_SCREEN)
        self.assertIn("no mtn data plans", resp["data"]["message"].lower())

    def test_the_re_render_path_also_refuses_an_empty_dropdown(self):
        """INIT/BACK re-renders whichever page the session is on. It used to
        build the data page without the emptiness check the submit path had."""
        DataPlan.objects.all().delete()
        pa = self._session(flows.VTU_DETAILS_STEP)
        resp = handle_flow_request({"action": "INIT", "flow_token": sign_flow_token(pa)})
        self.assertScreen(resp, note="re-render with no plans")
        self.assertEqual(resp["screen"], SUCCESS_SCREEN)


@override_settings(WHATSAPP={"MODE": "sandbox", "VERIFY_TOKEN": "v", "TOKEN": "",
                             "APP_SECRET": "", "PHONE_NUMBER_ID": "1",
                             "BASE_URL": "https://graph.test"},
                   WHATSAPP_FLOW={"ID": "1", "CTA": "Confirm with PIN"})
class TransferFormE2ETests(FlowContractMixin, TestCase):
    def setUp(self):
        self.user = _make_user()

    def test_the_form_renders_when_banks_exist(self):
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#000",
                            active=True, popular=True)
        self.assertScreen(flows._transfer_form_screen(), note="transfer form")

    def test_no_banks_means_the_chat_ladder_not_a_blank_form(self):
        """A Dropdown with an empty data-source does not render. An unseeded or
        fully-deactivated Bank table is not hypothetical, and the chat
        interrogation still works — so emptiness picks the rung, rather than
        being discovered on the customer's screen."""
        from .router import _start_transfer

        Bank.objects.all().delete()
        with patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.send_flow") as send, \
             patch("whatsapp.router.reply") as reply:
            _start_transfer(self.user, MSISDN)
        send.assert_not_called()
        self.assertIn("How much", reply.call_args.args[1])
        self.assertEqual(
            PendingAction.objects.get(msisdn=MSISDN).state, "amount")
