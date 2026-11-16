"""Release 2 Flow screens: one-page signup details, the SMS code + app password
on one page, the one-page sign-in, paying a bill in the secure form, the menu as
a form, and the confirm layout.

Two promises are tested for every screen:

* with its flag OFF (the default) nothing changes - the screen is never sent and
  never answered, because Meta serves the Flow JSON published by hand AFTER the
  deploy, and an unpublished screen is "Couldn't load content";
* with its flag ON every answer is renderable: a declared screen, exactly its
  declared properties, a declared route from the screen the device is on, and
  no empty Dropdown/RadioButtonsGroup (FlowContractMixin.assertScreen).
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from transfers.models import Bank
from utility.catalogue_fixtures import map_billers
from wallet.models import Transaction

from . import flows, release2
from .flows import handle_flow_request, sign_flow_token, sign_onboarding_token
from .models import PendingAction, WaMessageLog, WaOnboarding, WhatsAppLink
from .test_flow_e2e import FlowContractMixin
from .tests import give_account, make_user

MSISDN = "2348010000001"          # make_user()'s phone, as a WhatsApp number
NEW_MSISDN = "2348099990001"      # a number with no account (signup / sign-in)
GOOD_PW = "Zitch!2026pay"
ALL_ON = {"RESULT_SCREEN": True, "DETAILS_SCREEN": True, "VERIFY_PASSWORD_SCREEN": True,
          "LOGIN_ONE_SCREEN": True, "BILL_FORM": True, "MENU_FORM": True}


def _exchange(token, data=None, action="data_exchange"):
    return handle_flow_request({"action": action, "flow_token": token, "data": data or {}})


class FlagDefaultsTests(SimpleTestCase):
    """Every Release 2 gate is read from WHATSAPP_FLOW and is OFF unless set."""

    FLAGS = (flows.details_screen_live, flows.verify_password_live,
             flows.login_one_screen_live, flows.bill_form_live, flows.menu_form_live)

    @override_settings(WHATSAPP_FLOW={"FLOW_ID": "1", "RESULT_SCREEN": True})
    def test_all_off_by_default(self):
        self.assertEqual([f() for f in self.FLAGS], [False] * len(self.FLAGS))

    @override_settings(WHATSAPP_FLOW=ALL_ON)
    def test_each_reads_its_own_key(self):
        self.assertEqual([f() for f in self.FLAGS], [True] * len(self.FLAGS))

    def test_the_settings_module_reads_each_flag_from_its_env_var(self):
        """WHATSAPP_FLOW_<NAME>=true is how an operator turns one on."""
        import inspect

        from django.conf import settings

        from zitch_api import settings as module

        source = inspect.getsource(module)
        for name in ("DETAILS_SCREEN", "VERIFY_PASSWORD_SCREEN", "LOGIN_ONE_SCREEN",
                     "BILL_FORM", "MENU_FORM"):
            with self.subTest(name=name):
                self.assertIn(f'"{name}": os.environ.get("WHATSAPP_FLOW_{name}", "")', source)
                # Unset in the test environment, so off.
                self.assertIs(settings.WHATSAPP_FLOW[name], False)


# --------------------------------------------------------------------------- #
# 1. sign-up details on one page
# --------------------------------------------------------------------------- #
def _signup_ob(step=flows.FLOW_SIGNUP_STATE, **payload):
    return WaOnboarding.objects.create(
        msisdn=NEW_MSISDN, step=step,
        payload={"details_form": True, "flow_screen": flows.SIGNUP_DETAILS, **payload},
        expires_at=timezone.now() + timedelta(minutes=15))


DETAILS = {"first_name": "Ngozi", "last_name": "Ade", "email": "ngozi@example.com",
           "phone": "08099990001"}


class SignupEntryTests(TestCase):

    def _start(self):
        from .router import _start_onboarding

        with patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.send_flow", return_value={"success": True}) as send, \
             patch("whatsapp.router.reply"):
            _start_onboarding(NEW_MSISDN)
        return send.call_args.kwargs

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True})
    def test_flag_off_opens_the_two_page_form_as_before(self):
        sent = self._start()
        self.assertEqual(sent["screen"], flows.SIGNUP_SCREEN)
        self.assertEqual(WaOnboarding.objects.get().payload, {})

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "DETAILS_SCREEN": True})
    def test_flag_on_opens_the_one_page_form(self):
        sent = self._start()
        self.assertEqual(sent["screen"], flows.SIGNUP_DETAILS)
        self.assertEqual(sent["header"], "Create your Zitch account")
        self.assertEqual(sent["screen_data"], {"error": ""})
        ob = WaOnboarding.objects.get()
        self.assertTrue(ob.payload["details_form"])

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "DETAILS_SCREEN": True,
                                      "PRIVACY_SCREEN": True})
    def test_the_privacy_path_continues_on_the_chained_twin(self):
        sent = self._start()
        self.assertEqual(sent["screen"], flows.PRIVACY_NOTICE)
        ob = WaOnboarding.objects.get()
        resp = _exchange(sign_onboarding_token(ob), {"consent": True})
        self.assertEqual(resp["screen"], flows.DETAILS_CHAIN)
        # ...and an error there re-renders the twin, never the root.
        resp = _exchange(sign_onboarding_token(ob), {**DETAILS, "first_name": "N"})
        self.assertEqual(resp["screen"], flows.DETAILS_CHAIN)
        self.assertIn("first and last name", resp["data"]["error"])

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "PRIVACY_SCREEN": True})
    def test_flag_off_the_privacy_path_is_unchanged(self):
        self._start()
        ob = WaOnboarding.objects.get()
        self.assertEqual(_exchange(sign_onboarding_token(ob), {})["screen"], flows.SIGNUP_CHAIN)


@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "DETAILS_SCREEN": True})
class SignupDetailsPageTests(FlowContractMixin, TestCase):

    def submit(self, ob, **overrides):
        resp = _exchange(sign_onboarding_token(ob), {**DETAILS, **overrides})
        return self.assertScreen(resp, standing_on=flows.SIGNUP_DETAILS)

    def test_the_same_validation_as_the_two_page_form(self):
        User.objects.create(username="08011110000", phone="08011110000", email="taken@example.com")
        ob = _signup_ob()
        for overrides, wanted in (
                ({"first_name": "N"}, "first and last name"),
                ({"last_name": ""}, "first and last name"),
                ({"email": "not-an-email"}, "email address"),
                ({"email": "TAKEN@example.com"}, "already on a Zitch account"),
                ({"phone": "0809999"}, "11-digit number"),
                ({"phone": "08011110000"}, "already has a Zitch account")):
            with self.subTest(overrides=overrides):
                resp = self.submit(ob, **overrides)
                self.assertEqual(resp["screen"], flows.SIGNUP_DETAILS)
                self.assertIn(wanted, resp["data"]["error"])
                self.assertTrue(resp["data"]["error"].startswith("⚠️"))
        ob.refresh_from_db()
        self.assertEqual(ob.step, flows.FLOW_SIGNUP_STATE)
        self.assertNotIn("email", ob.payload)

    def test_names_are_checked_before_the_phone(self):
        """Validation order matches the two-page form: details, then phone."""
        resp = self.submit(_signup_ob(), first_name="N", phone="1")
        self.assertIn("first and last name", resp["data"]["error"])

    def test_the_whatsapp_number_skips_the_code_and_goes_to_the_pin(self):
        ob = _signup_ob()
        with patch("whatsapp.router.send_sms") as sms:
            resp = self.submit(ob)
        sms.assert_not_called()
        self.assertEqual(resp["screen"], flows.PIN_CHAIN)
        ob.refresh_from_db()
        self.assertEqual(ob.step, flows.FLOW_PIN_STATE)
        self.assertEqual({k: ob.payload[k] for k in DETAILS},
                         {**DETAILS, "email": "ngozi@example.com"})

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "DETAILS_SCREEN": True,
                                      "PASSWORD_SCREEN": True})
    def test_with_the_password_page_live_the_details_survive_its_step_only_save(self):
        ob = _signup_ob()
        resp = self.submit(ob)
        self.assertEqual(resp["screen"], flows.SIGNUP_PASSWORD)
        ob.refresh_from_db()
        self.assertEqual(ob.step, flows.FLOW_PASSWORD_STATE)
        self.assertEqual(ob.payload["first_name"], "Ngozi")
        self.assertEqual(ob.payload["phone"], "08099990001")

    def test_another_number_gets_the_existing_code_page_while_verify_is_off(self):
        ob = _signup_ob()
        with patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}) as sms:
            resp = self.submit(ob, phone="08077770009")
        self.assertEqual(sms.call_args[0][0], "08077770009")
        self.assertEqual(resp["screen"], flows.SIGNUP_PHONE_CODE)
        ob.refresh_from_db()
        self.assertEqual(ob.step, flows.FLOW_PHONE_CODE_STATE)

    def test_no_sms_rail_keeps_the_page_open(self):
        resp = self.submit(_signup_ob(), phone="08077770010")   # sms_live is False in tests
        self.assertEqual(resp["screen"], flows.SIGNUP_DETAILS)
        self.assertIn("couldn't send an SMS", resp["data"]["error"])

    def test_a_whole_signup_on_the_one_page_form_creates_the_account(self):
        ob = _signup_ob()
        token = sign_onboarding_token(ob)
        self.submit(ob)
        self.assertEqual(_exchange(token, {"pin": "246810"})["screen"], flows.PIN_CONFIRM)
        with patch("wallet.views._wema_funding_enabled", return_value=False):
            done = _exchange(token, {"pin": "246810"})
        self.assertEqual(done["screen"], flows.RESULT_SCREEN)
        user = User.objects.get(phone="08099990001")
        self.assertEqual((user.first_name, user.last_name, user.email),
                         ("Ngozi", "Ade", "ngozi@example.com"))
        self.assertTrue(user.phone_verified)

    def test_init_re_renders_the_page_the_session_is_on(self):
        ob = _signup_ob()
        resp = _exchange(sign_onboarding_token(ob), action="INIT")
        self.assertEqual(resp, {"screen": flows.SIGNUP_DETAILS, "data": {"error": ""}})


# --------------------------------------------------------------------------- #
# 2. verify phone + password on one page
# --------------------------------------------------------------------------- #
@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "DETAILS_SCREEN": True,
                                  "VERIFY_PASSWORD_SCREEN": True})
class VerifyPasswordTests(FlowContractMixin, TestCase):

    def setUp(self):
        cache.clear()
        self.ob = _signup_ob()
        self.token = sign_onboarding_token(self.ob)
        with patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}) as sms:
            resp = _exchange(self.token, {**DETAILS, "phone": "08077770009"})
        self.assertScreen(resp, standing_on=flows.SIGNUP_DETAILS)
        self.first = resp
        self.code = sms.call_args[0][1].split("Zitch: ")[1][:6]

    def submit(self, **data):
        return self.assertScreen(_exchange(self.token, data), standing_on=flows.VERIFY_PASSWORD)

    def test_a_different_number_lands_on_the_combined_page(self):
        self.assertEqual(self.first["screen"], flows.VERIFY_PASSWORD)
        self.assertIn("•••••0009", self.first["data"]["summary"])
        self.ob.refresh_from_db()
        self.assertEqual(self.ob.step, flows.FLOW_VERIFY_PW_STATE)

    def test_password_rules_are_signup_passwords_and_cost_no_code_attempt(self):
        for data, wanted in (({"password": "", "confirm_password": ""}, "choose a password"),
                             ({"password": "abc", "confirm_password": "abd"}, "don't match"),
                             ({"password": "abcdefgh", "confirm_password": "abcdefgh"}, "number")):
            with self.subTest(data=data):
                resp = self.submit(phone_code=self.code, **data)
                self.assertEqual(resp["screen"], flows.VERIFY_PASSWORD)
                self.assertIn(wanted, resp["data"]["error"])
                self.assertNotIn(data["password"] or "\0", str(resp))
        self.ob.refresh_from_db()
        self.assertEqual(int(self.ob.payload.get("phone_code_attempts") or 0), 0)
        self.assertNotIn("flow_pw_hash", self.ob.payload)

    def test_a_wrong_code_is_retried_on_the_same_page(self):
        resp = self.submit(phone_code="000000", password=GOOD_PW, confirm_password=GOOD_PW)
        self.assertEqual(resp["screen"], flows.VERIFY_PASSWORD)
        self.assertIn("isn't right", resp["data"]["error"])
        self.ob.refresh_from_db()
        self.assertNotIn("flow_pw_hash", self.ob.payload)

    def test_three_wrong_codes_end_the_signup(self):
        for _ in range(2):
            self.submit(phone_code="000000", password=GOOD_PW, confirm_password=GOOD_PW)
        resp = self.submit(phone_code="000000", password=GOOD_PW, confirm_password=GOOD_PW)
        self.assertEqual(resp["screen"], flows.RESULT_SCREEN)
        self.assertFalse(WaOnboarding.objects.exists())

    def test_the_right_code_and_password_go_on_to_the_pin_holding_only_a_hash(self):
        resp = self.submit(phone_code=self.code, password=GOOD_PW, confirm_password=GOOD_PW)
        self.assertEqual(resp["screen"], flows.PIN_CHAIN)
        self.ob.refresh_from_db()
        self.assertEqual(self.ob.step, flows.FLOW_PIN_STATE)
        self.assertTrue(self.ob.payload["phone_verified_flow"])
        self.assertTrue(check_password(GOOD_PW, self.ob.payload["flow_pw_hash"]))
        self.assertNotIn(GOOD_PW, str(self.ob.payload))
        # ...and the account it creates signs in with that password.
        _exchange(self.token, {"pin": "246810"})
        with patch("wallet.views._wema_funding_enabled", return_value=False):
            _exchange(self.token, {"pin": "246810"})
        user = User.objects.get(phone="08077770009")
        self.assertTrue(user.check_password(GOOD_PW))
        self.assertTrue(user.phone_verified)

    def test_resend_is_rate_limited_then_sends_a_fresh_code(self):
        with patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}) as sms:
            early = self.submit(resend=True)
            self.assertIn("wait 30 seconds", early["data"]["error"])
            sms.assert_not_called()
            self.ob.refresh_from_db()
            self.ob.payload["verify_resent_at"] = (timezone.now() - timedelta(minutes=1)).isoformat()
            self.ob.save(update_fields=["payload"])
            again = self.submit(resend=True)
        self.assertEqual(again["data"]["error"], "")
        self.assertIn("new 6-digit code", again["data"]["summary"])
        fresh = sms.call_args[0][1].split("Zitch: ")[1][:6]
        ok = self.submit(phone_code=fresh, password=GOOD_PW, confirm_password=GOOD_PW)
        self.assertEqual(ok["screen"], flows.PIN_CHAIN)

    def test_resend_is_capped(self):
        self.ob.refresh_from_db()
        self.ob.payload["verify_resends"] = 3
        self.ob.save(update_fields=["payload"])
        with patch("whatsapp.router.send_sms") as sms:
            resp = self.submit(resend=True)
        sms.assert_not_called()
        self.assertIn("3 new codes", resp["data"]["error"])

    def test_the_whatsapp_number_gets_just_the_password_page(self):
        """No SMS needed: the existing SIGNUP_PASSWORD page, even with the old
        PASSWORD_SCREEN flag off - this release collects the password."""
        ob = WaOnboarding.objects.create(
            msisdn="2348066660001", step=flows.FLOW_SIGNUP_STATE,
            payload={"details_form": True, "flow_screen": flows.SIGNUP_DETAILS},
            expires_at=timezone.now() + timedelta(minutes=15))
        resp = _exchange(sign_onboarding_token(ob),
                         {**DETAILS, "email": "own@example.com", "phone": "08066660001"})
        self.assertScreen(resp, standing_on=flows.SIGNUP_DETAILS)
        self.assertEqual(resp["screen"], flows.SIGNUP_PASSWORD)

    def test_the_two_page_phone_step_also_uses_the_combined_page(self):
        ob = WaOnboarding.objects.create(
            msisdn="2348066660002", step=flows.FLOW_PHONE_STATE,
            payload={"first_name": "Ada", "last_name": "Obi", "email": "a2@example.com"},
            expires_at=timezone.now() + timedelta(minutes=15))
        with patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}):
            resp = _exchange(sign_onboarding_token(ob), {"phone": "08077770012"})
        self.assertScreen(resp, standing_on=flows.SIGNUP_PHONE)
        self.assertEqual(resp["screen"], flows.VERIFY_PASSWORD)

    def test_a_code_typed_in_the_chat_is_pointed_back_at_the_form(self):
        from .router import handle_inbound

        with patch("whatsapp.router.reply") as reply:
            handle_inbound(NEW_MSISDN, self.code)
        self.assertIn("secure screen", reply.call_args[0][1])
        self.assertTrue(WaOnboarding.objects.filter(step=flows.FLOW_VERIFY_PW_STATE).exists())


@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True})
class VerifyPasswordFlagOffTests(TestCase):

    def test_a_different_number_still_gets_the_code_page(self):
        ob = WaOnboarding.objects.create(
            msisdn=NEW_MSISDN, step=flows.FLOW_PHONE_STATE,
            payload={"first_name": "Ada", "last_name": "Obi", "email": "a3@example.com"},
            expires_at=timezone.now() + timedelta(minutes=15))
        with patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}):
            resp = _exchange(sign_onboarding_token(ob), {"phone": "08077770013"})
        self.assertEqual(resp["screen"], flows.SIGNUP_PHONE_CODE)

    def test_the_whatsapp_number_still_skips_the_password(self):
        ob = WaOnboarding.objects.create(
            msisdn=NEW_MSISDN, step=flows.FLOW_PHONE_STATE,
            payload={"first_name": "Ada", "last_name": "Obi", "email": "a4@example.com"},
            expires_at=timezone.now() + timedelta(minutes=15))
        resp = _exchange(sign_onboarding_token(ob), {"phone": "08099990001"})
        self.assertEqual(resp["screen"], flows.PIN_CHAIN)


# --------------------------------------------------------------------------- #
# 3. connect an existing account: code + PIN on one page
# --------------------------------------------------------------------------- #
class OneScreenLoginTests(FlowContractMixin, TestCase):

    def setUp(self):
        cache.clear()
        self.enterContext(patch("requests.sessions.Session.request",
                                side_effect=AssertionError("Unexpected network call")))
        self.user = User.objects.create_user(
            username="08099990001", phone="08099990001", email="owner@example.com",
            phone_verified=True, email_verified=True, transaction_pin=make_password("638291"))

    def start(self):
        from .login_flow import _token, start_login

        with patch("whatsapp.providers.flows_live", return_value=True), \
             patch("whatsapp.providers.send_flow", return_value={"success": True}) as send, \
             patch("whatsapp.login_flow.send_email", return_value={"success": True}), \
             patch("whatsapp.login_flow.secrets.randbelow", return_value=123456):
            start_login(NEW_MSISDN)
        self.sent = send.call_args.kwargs
        return _token(WaOnboarding.objects.get(msisdn=NEW_MSISDN))

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True})
    def test_flag_off_keeps_the_two_page_sign_in(self):
        token = self.start()
        self.assertEqual(self.sent["screen"], flows.CODE_SCREEN)
        self.assertEqual(_exchange(token, {"number": "123456"})["screen"], flows.PIN_CHAIN)

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "LOGIN_ONE_SCREEN": True})
    def test_code_and_pin_on_one_page_connect_the_number(self):
        token = self.start()
        self.assertEqual(self.sent["screen"], flows.LOGIN_SCREEN)
        self.assertEqual(self.sent["on_open"], "data_exchange")
        opened = self.assertScreen(_exchange(token, action="INIT"))
        self.assertEqual(opened["screen"], flows.LOGIN_SCREEN)
        done = _exchange(token, {"number": "123456", "pin": "638291"})
        self.assertScreen(done, standing_on=flows.LOGIN_SCREEN)
        self.assertEqual(done["data"]["status"], "Done")
        self.assertEqual(WhatsAppLink.objects.get().user_id, self.user.pk)
        self.assertFalse(WaOnboarding.objects.exists())

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "LOGIN_ONE_SCREEN": True})
    def test_a_wrong_code_lands_on_the_empty_twin_and_never_checks_the_pin(self):
        token = self.start()
        with patch("whatsapp.login_flow.evaluate_transaction_pin") as verify:
            resp = _exchange(token, {"number": "000000", "pin": "638291"})
        verify.assert_not_called()
        self.assertScreen(resp, standing_on=flows.LOGIN_SCREEN)
        self.assertEqual(resp["screen"], flows.LOGIN_RETRY)
        self.assertIn("Incorrect code", resp["data"]["error"])
        # A re-tapped card opens on the root, never the twin (131009).
        self.assertEqual(_exchange(token, action="INIT")["screen"], flows.LOGIN_SCREEN)
        ended = _exchange(token, {"number": "000000", "pin": "638291"})
        self.assertScreen(ended, standing_on=flows.LOGIN_RETRY)
        self.assertEqual(ended["screen"], flows.RESULT_SCREEN)
        self.assertFalse(WhatsAppLink.objects.exists())

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "LOGIN_ONE_SCREEN": True})
    def test_right_code_wrong_pin_asks_only_for_the_pin_again(self):
        token = self.start()
        resp = _exchange(token, {"number": "123456", "pin": "111111"})
        self.assertScreen(resp, standing_on=flows.LOGIN_SCREEN)
        self.assertEqual(resp["screen"], flows.PIN_RETRY)
        self.user.refresh_from_db()
        self.assertEqual(self.user.pin_failed_attempts, 1)   # the shared counter
        done = _exchange(token, {"pin": "638291"})
        self.assertScreen(done, standing_on=flows.PIN_RETRY)
        self.assertTrue(WhatsAppLink.objects.exists())

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "LOGIN_ONE_SCREEN": True})
    def test_the_screen_says_how_to_recover_a_forgotten_pin_and_the_chat_answers(self):
        from .router import handle_inbound

        captions = [c for c in BY_ID_LOGIN["layout"]["children"][-1]["children"]
                    if c.get("type") == "TextCaption"]
        self.assertEqual(captions[0]["text"], "Forgot your PIN? Chat with our customer support 24/7 to reset it.")
        self.start()
        with patch("whatsapp.router.reply") as reply:
            handle_inbound(NEW_MSISDN, "reset pin")
        self.assertIn("support", reply.call_args[0][1])
        self.assertTrue(WaOnboarding.objects.exists())   # the sign-in stays open


# --------------------------------------------------------------------------- #
# 4. pay a bill in the secure form
# --------------------------------------------------------------------------- #
class _MoneyBase(FlowContractMixin, TestCase):

    def setUp(self):
        cache.clear()
        self.user, _ = make_user(balance="20000")
        give_account(self.user)
        map_billers()
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=MSISDN, status=WhatsAppLink.ACTIVE)
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#000",
                            active=True, popular=True)

    def say(self, text):
        from .router import handle_inbound

        sent = []
        with patch("whatsapp.router.reply", side_effect=lambda m, t, **k: sent.append(t)), \
             patch("whatsapp.router.reply_list",
                   side_effect=lambda m, b, rows, **k: sent.append(b)), \
             patch("whatsapp.router.reply_buttons",
                   side_effect=lambda m, b, *a, **k: sent.append(b)), \
             patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.send_flow", return_value={"success": True}) as flow:
            handle_inbound(MSISDN, text)
        return sent, flow


@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "BILL_FORM": True})
class BillFormTests(_MoneyBase):

    def open(self):
        sent, flow = self.say("4")                    # menu 4: Pay a bill
        kwargs = flow.call_args.kwargs
        self.assertEqual((kwargs["screen"], kwargs["cta"]), (flows.BILL_SCREEN, "Pay a bill"))
        self.assertEqual(kwargs["screen_data"]["discos"][0], {"id": "1", "title": "Ikeja Electric"})
        self.assertIn("which bill", sent[-1])          # the chat path offered beside it
        pa = PendingAction.objects.get(msisdn=MSISDN)
        self.assertEqual((pa.action_type, pa.state), ("electricity", flows.FLOW_BILL_STATE))
        return pa

    def details(self, pa, **overrides):
        data = {"disco": "1", "meter_type": "prepaid", "meter": "01234567890", **overrides}
        return self.assertScreen(_exchange(sign_flow_token(pa), data),
                                 standing_on=flows.BILL_SCREEN)

    def test_details_are_validated(self):
        pa = self.open()
        for overrides, wanted in (({"disco": "99"}, "disco"), ({"meter_type": "x"}, "prepaid"),
                                  ({"meter": "12 3"}, "valid meter")):
            with self.subTest(overrides=overrides):
                resp = self.details(pa, **overrides)
                self.assertEqual(resp["screen"], flows.BILL_SCREEN)
                self.assertIn(wanted, resp["data"]["error"])

    def test_a_meter_the_provider_refuses_stays_on_the_form(self):
        pa = self.open()
        with patch("whatsapp.router.vtu_verify_customer", return_value={"success": False}):
            resp = self.details(pa)
        self.assertEqual(resp["screen"], flows.BILL_SCREEN)
        self.assertIn("Couldn't validate that meter", resp["data"]["error"])

    def test_verified_meter_amount_pin_pays_with_the_chat_executor(self):
        pa = self.open()
        with patch("whatsapp.router.vtu_verify_customer",
                   wraps=__import__("whatsapp.router", fromlist=["x"]).vtu_verify_customer) as verify:
            amount_page = self.details(pa)
        verify.assert_called_once_with("ikeja-electric", "01234567890", "prepaid")
        self.assertEqual(amount_page["screen"], flows.BILL_AMOUNT)
        self.assertTrue(amount_page["data"]["verified"].startswith("✓ Meter verified · "))
        self.assertTrue(amount_page["data"]["address"])
        self.assertEqual(amount_page["data"]["balance"], "Available balance ₦20,000.00")
        self.assertIn("Meter: 01234567890 (prepaid)", amount_page["data"]["details"])

        low = self.assertScreen(_exchange(sign_flow_token(pa), {"amount": "50"}),
                                standing_on=flows.BILL_AMOUNT)
        self.assertEqual(low["screen"], flows.BILL_AMOUNT)
        self.assertIn("at least", low["data"]["error"])
        broke = _exchange(sign_flow_token(pa), {"amount": "900000"})
        self.assertIn("Insufficient balance", broke["data"]["error"])

        confirm = self.assertScreen(_exchange(sign_flow_token(pa), {"amount": "3000",
                                                                    "narration": "Flat 2"}),
                                    standing_on=flows.BILL_AMOUNT)
        self.assertEqual(confirm["screen"], flows.PIN_CHAIN)
        self.assertEqual(confirm["data"]["amount"], "₦3,000.00")
        self.assertEqual(confirm["data"]["balance"], "Available balance ₦20,000.00")
        self.assertEqual(confirm["data"]["recipient"], "Biller: Ikeja Electric · Prepaid")
        self.assertEqual(confirm["data"]["narration"], "Note: Flat 2")
        pa.refresh_from_db()
        self.assertEqual(pa.state, flows.FLOW_PIN_STATE)

        with patch("whatsapp.router.reply"), patch("whatsapp.router.reply_receipt"):
            done = _exchange(sign_flow_token(pa), {"pin": "1234"})
        self.assertScreen(done, standing_on=flows.PIN_CHAIN)
        self.assertEqual(done["data"]["status"], "✅ Successful")
        self.assertTrue(done["data"]["message"].endswith("Receipt sent to your chat."))
        txn = Transaction.objects.get(user=self.user, idempotency_key=f"wa-{pa.pk}")
        self.assertEqual(txn.amount, Decimal("3000"))
        self.assertEqual(txn.transaction_status, Transaction.SUCCESS)

    def test_the_chat_path_still_works_beside_the_form(self):
        self.open()
        sent, _ = self.say("1")
        self.assertIn("Which disco?", sent[-1])
        pa = PendingAction.objects.get(msisdn=MSISDN)
        self.assertEqual((pa.action_type, pa.state), ("electricity", "disco"))

    def test_a_named_disco_carries_on_in_the_chat(self):
        self.open()
        sent, _ = self.say("IKEDC")
        self.assertIn("Prepaid or postpaid", sent[-1])
        self.assertEqual(PendingAction.objects.get(msisdn=MSISDN).payload["disco"], "1")

    def test_cable_is_still_chat_only(self):
        self.open()
        sent, _ = self.say("2")
        self.assertIn("Which provider?", sent[-1])

    def test_a_failed_send_falls_back_to_the_chat_menu(self):
        from .router import handle_inbound

        sent = []
        with patch("whatsapp.router.reply_list", side_effect=lambda m, b, rows, **k: sent.append(b)), \
             patch("whatsapp.router.flows_live", return_value=True), \
             patch("whatsapp.router.send_flow", return_value={"success": False}):
            handle_inbound(MSISDN, "bill")
        self.assertEqual(sent, ["Which bill would you like to pay?"])
        self.assertEqual(PendingAction.objects.get(msisdn=MSISDN).action_type, "pick_service")


@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True})
class BillFormFlagOffTests(_MoneyBase):

    def test_pay_a_bill_is_the_chat_sub_menu_as_before(self):
        sent, flow = self.say("4")
        flow.assert_not_called()
        self.assertEqual(sent, ["Which bill would you like to pay?"])

    def test_a_bill_session_is_never_answered_with_a_bill_screen(self):
        """flow_bill rows are only ever created with the flag on; this pins the
        endpoint's dispatch to the state, not the flag, so a session opened
        before a flag flip-back still finishes."""
        pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="electricity", state="disco",
            payload={}, expires_at=timezone.now() + timedelta(minutes=5))
        self.assertIsNone(flows.resolve_flow_token(sign_flow_token(pa)))


# --------------------------------------------------------------------------- #
# 6. the menu as a form
# --------------------------------------------------------------------------- #
@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "MENU_FORM": True, "BILL_FORM": True})
class MenuFormTests(_MoneyBase):

    def menu_token(self):
        sent, flow = self.say("menu")
        kwargs = flow.call_args.kwargs
        self.assertEqual(kwargs["body"], "What would you like to do?")
        self.assertEqual(kwargs["cta"], "☰ Menu")
        self.assertEqual(kwargs["screen"], flows.MENU_SCREEN)
        self.assertScreen({"screen": kwargs["screen"], "data": kwargs["screen_data"]})
        self.assertIn("*1.*", sent[-1])                  # the numbered list follows
        return flow.call_args.args[1]

    def test_menu_lists_the_chat_menu_in_its_order(self):
        items = release2.menu_items()
        from .router import MENU_CORE

        self.assertEqual([i["id"] for i in items][:len(MENU_CORE)],
                         [str(n) for n in range(1, len(MENU_CORE) + 1)])
        self.assertEqual(items[0]["title"], "💰 Check balance")
        self.assertEqual(items[10]["title"], "⭐ My saved people")

    def test_a_form_less_item_closes_the_flow_and_answers_in_the_chat(self):
        token = self.menu_token()
        with patch("whatsapp.router.reply") as reply:
            resp = _exchange(token, {"item": "1"})
        self.assertEqual(resp["screen"], flows.SUCCESS_SCREEN)
        self.assertIn(flows._TERMINATION_KEY, resp["data"])
        self.assertIn("balance", " ".join(c.args[1] for c in reply.call_args_list).lower())

    @override_settings(WHATSAPP_PROCESS_INLINE=False)
    def test_in_production_the_item_rides_the_inbound_queue(self):
        token = self.menu_token()
        resp = _exchange(token, {"item": "9"})
        self.assertIn(flows._TERMINATION_KEY, resp["data"])
        job = WaMessageLog.objects.get(text="[flow menu] history")
        from .jobs import _decrypt

        self.assertEqual(_decrypt(job.processing_payload), {"is_text": True, "body": "history"})

    def test_send_money_opens_the_transfer_form_on_its_twin(self):
        token = self.menu_token()
        resp = self.assertScreen(_exchange(token, {"item": "2"}), standing_on=flows.MENU_SCREEN)
        self.assertEqual(resp["screen"], flows.TRANSFER_CHAIN)
        pa = PendingAction.objects.get(msisdn=MSISDN)
        self.assertEqual(pa.state, flows.FLOW_FORM_STATE)
        # The form's own error re-render stays on the twin.
        err = self.assertScreen(_exchange(token, {"amount": "x", "account_number": "1"}),
                                standing_on=flows.TRANSFER_CHAIN)
        self.assertEqual(err["screen"], flows.TRANSFER_CHAIN)
        with patch("utility.providers.payout_resolve_account",
                   return_value={"success": True, "name": "JOHN DOE"}):
            confirm = _exchange(token, {"amount": "5000", "account_number": "0123456789",
                                        "bank": "GTBank"})
        self.assertScreen(confirm, standing_on=flows.TRANSFER_CHAIN)
        self.assertEqual(confirm["screen"], flows.PIN_CHAIN)
        self.assertEqual(confirm["data"]["recipient"], "To: JOHN DOE")

    def test_airtime_opens_the_vtu_ladder_on_its_twin(self):
        token = self.menu_token()
        resp = self.assertScreen(_exchange(token, {"item": "3"}), standing_on=flows.MENU_SCREEN)
        self.assertEqual(resp["screen"], flows.VTU_CHAIN)
        err = self.assertScreen(_exchange(token, {"kind": "x"}), standing_on=flows.VTU_CHAIN)
        self.assertEqual(err["screen"], flows.VTU_CHAIN)
        nxt = self.assertScreen(_exchange(token, {"kind": "airtime"}), standing_on=flows.VTU_CHAIN)
        self.assertEqual(nxt["screen"], flows.VTU_NETWORK)

    def test_pay_a_bill_opens_the_bill_form_on_its_twin(self):
        token = self.menu_token()
        resp = self.assertScreen(_exchange(token, {"item": "4"}), standing_on=flows.MENU_SCREEN)
        self.assertEqual(resp["screen"], flows.BILL_CHAIN)
        err = self.assertScreen(_exchange(token, {"disco": "99", "meter_type": "prepaid",
                                                  "meter": "01234567890"}),
                                standing_on=flows.BILL_CHAIN)
        self.assertEqual(err["screen"], flows.BILL_CHAIN)
        amount = self.assertScreen(_exchange(token, {"disco": "1", "meter_type": "prepaid",
                                                     "meter": "01234567890"}),
                                   standing_on=flows.BILL_CHAIN)
        self.assertEqual(amount["screen"], flows.BILL_AMOUNT)

    @override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True, "MENU_FORM": True})
    def test_pay_a_bill_without_the_bill_form_is_answered_in_the_chat(self):
        token = self.menu_token()
        with patch("whatsapp.router.reply_list") as listed:
            resp = _exchange(token, {"item": "4"})
        self.assertIn(flows._TERMINATION_KEY, resp["data"])
        self.assertEqual(listed.call_args.args[1], "Which bill would you like to pay?")

    def test_an_unknown_item_re_renders_the_menu(self):
        token = self.menu_token()
        resp = self.assertScreen(_exchange(token, {"item": "99"}), standing_on=flows.MENU_SCREEN)
        self.assertEqual(resp["screen"], flows.MENU_SCREEN)
        self.assertIn("Pick one", resp["data"]["error"])

    def test_a_forged_or_unlinked_token_opens_nothing(self):
        token = self.menu_token()
        forged = token[:-1] + ("A" if token[-1] != "A" else "B")
        self.assertEqual(_exchange(forged, {"item": "2"})["screen"], flows.RESULT_SCREEN)
        WhatsAppLink.objects.update(status="revoked")
        self.assertIn("expired", _exchange(token, {"item": "2"})["data"]["message"])
        self.assertFalse(PendingAction.objects.exists())

    def test_a_blocked_account_is_told_in_the_chat_and_the_flow_closes(self):
        with patch("whatsapp.router._blocked_from_spending", return_value=True):
            token = self.menu_token()
            resp = _exchange(token, {"item": "2"})
        self.assertIn(flows._TERMINATION_KEY, resp["data"])
        self.assertFalse(PendingAction.objects.exists())

    def test_typing_a_number_after_the_menu_still_works(self):
        self.menu_token()
        self.assertFalse(PendingAction.objects.exists())   # the menu holds no action
        sent, _ = self.say("1")
        self.assertIn("balance", " ".join(sent).lower())


@override_settings(WHATSAPP_FLOW={"RESULT_SCREEN": True})
class MenuFormFlagOffTests(_MoneyBase):

    def test_the_menu_is_the_numbered_text_only(self):
        sent, flow = self.say("menu")
        flow.assert_not_called()
        self.assertEqual(len(sent), 1)
        self.assertIn("*1.*", sent[0])


# --------------------------------------------------------------------------- #
# 5. confirm screen layout
# --------------------------------------------------------------------------- #
class ConfirmLayoutTests(TestCase):

    def setUp(self):
        self.user, _ = make_user(balance="20000")

    def fields(self, action_type, **payload):
        from .router import _flow_fields

        pa = PendingAction(user=self.user, msisdn=MSISDN, action_type=action_type,
                           state=flows.FLOW_PIN_STATE, payload=payload)
        pa.id = 1
        return _flow_fields(pa)

    def test_balance_on_top_amount_heading_then_label_value_lines(self):
        f = self.fields("transfer", amount="5000", account="0123456789", bank_name="GTBank",
                        name="John Doe", fee="10.75", narration="Rent")
        self.assertEqual(f["balance"], "Available balance ₦20,000.00")
        self.assertEqual(f["amount"], "₦5,000.00")
        self.assertEqual(f["recipient"], "To: JOHN DOE")
        self.assertEqual(f["details"].splitlines(),
                         ["Account: GTBank · 0123456789", "Bank fee: ₦10.75",
                          "Total: ₦5,010.75"])
        self.assertEqual((f["cta"], f["title"]), ("Send", "Confirm payment"))
        self.assertEqual(f["narration"], "Note: Rent")

    def test_every_payment_kind_uses_label_value_lines(self):
        for action_type, payload, recipient in (
                ("airtime", {"amount": "500", "net": "1", "phone": "08031234567"}, "Airtime: MTN"),
                ("data", {"price": "500", "net": "1", "plan_name": "1GB", "phone": "0803"},
                 "Plan: 1GB"),
                ("electricity", {"amount": "3000", "disco": "1", "meter": "0123"},
                 "Biller: Ikeja Electric"),
                ("cable", {"price": "9000", "prov": "2", "plan_name": "Compact", "iuc": "1"},
                 "Package: DSTV Compact"),
                ("exam", {"amount": "3500", "exam_name": "WAEC", "description": "PIN",
                          "quantity": 1, "phone": "0803"}, "Exam: WAEC PIN ×1")):
            with self.subTest(action_type=action_type):
                f = self.fields(action_type, **payload)
                self.assertEqual(f["recipient"], recipient)
                for line in f["details"].splitlines():
                    self.assertRegex(line, r"^[A-Z][A-Za-z ()]+: ")


class ReceiptLineTests(SimpleTestCase):

    def test_success_outcomes_end_with_the_receipt_line(self):
        for given, expected in (
                ("₦5,000.00 sent to JOHN DOE - the receipt is in your chat.",
                 "₦5,000.00 sent to JOHN DOE. Receipt sent to your chat."),
                ("Electricity paid - the receipt is in your chat.",
                 "Electricity paid. Receipt sent to your chat."),
                ("Sent — the receipt is in your chat.", "Sent. Receipt sent to your chat."),
                ("Paid\n\nThe receipt is in your chat.", "Paid. Receipt sent to your chat."),
                ("Payment successful", "Payment successful. Receipt sent to your chat."),
                ("Done. Receipt sent to your chat.", "Done. Receipt sent to your chat.")):
            with self.subTest(given=given):
                self.assertEqual(flows.with_receipt_line(given), expected)


# --------------------------------------------------------------------------- #
# the Flow JSON: every Release 2 screen is declared, routed and renderable
# --------------------------------------------------------------------------- #
import json  # noqa: E402
from pathlib import Path  # noqa: E402

import whatsapp  # noqa: E402

_DOC = json.loads((Path(whatsapp.__file__).parent / "flow_assets" / "pin_flow.json").read_text())
_BY_ID = {s["id"]: s for s in _DOC["screens"]}
BY_ID_LOGIN = _BY_ID["LOGIN_SCREEN"]


class Release2FlowJsonTests(SimpleTestCase):

    NEW = ("SIGNUP_DETAILS", "DETAILS_CHAIN", "VERIFY_PASSWORD", "LOGIN_SCREEN", "LOGIN_RETRY",
           "MENU", "TRANSFER_CHAIN", "VTU_CHAIN", "BILL_SCREEN", "BILL_CHAIN", "BILL_AMOUNT")

    def test_version_unchanged_and_only_known_components_used(self):
        """No NavigationList: it needs a newer Flow JSON version than 6.0, and
        RadioButtonsGroup + data_exchange does the job without bumping it."""
        self.assertEqual(_DOC["version"], "6.0")
        allowed = {"SingleColumnLayout", "Image", "TextHeading", "TextBody", "TextCaption",
                   "TextInput", "Dropdown", "RadioButtonsGroup", "EmbeddedLink", "Form", "Footer"}
        found = set()

        def walk(node):
            if isinstance(node, dict):
                if "type" in node and isinstance(node["type"], str) and node["type"][0].isupper():
                    found.add(node["type"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        for sid in self.NEW:
            walk(_BY_ID[sid]["layout"])
        self.assertLessEqual(found, allowed)

    def test_every_new_screen_declares_routes_and_a_footer_last(self):
        for sid in self.NEW:
            with self.subTest(sid=sid):
                self.assertIn(sid, _DOC["routing_model"])
                self.assertIn("SUCCESS", _DOC["routing_model"][sid])
                form = _BY_ID[sid]["layout"]["children"][-1]
                self.assertEqual(form["type"], "Form")
                self.assertEqual(form["children"][-1]["type"], "Footer")
                self.assertEqual(sum(c.get("type") == "Footer" for c in form["children"]), 1)

    def test_the_footer_payload_names_only_fields_on_the_screen(self):
        for sid in self.NEW:
            with self.subTest(sid=sid):
                form = _BY_ID[sid]["layout"]["children"][-1]
                names = {c.get("name") for c in form["children"] if c.get("name")}
                payload = form["children"][-1]["on-click-action"]["payload"]
                for key, value in payload.items():
                    self.assertEqual(value, "${form.%s}" % key)
                    self.assertIn(key, names)

    def test_design_copy(self):
        details = _BY_ID["SIGNUP_DETAILS"]
        self.assertEqual(details["title"], "Create your account")
        texts = [c.get("text") for c in details["layout"]["children"]]
        self.assertIn("Create your Zitch account", texts)
        self.assertIn("Step 1 of 5", texts)
        phone = next(c for c in details["layout"]["children"][-1]["children"]
                     if c.get("name") == "phone")
        self.assertEqual(phone["helper-text"], "11 digits — we'll text you a code")
        verify = _BY_ID["VERIFY_PASSWORD"]
        self.assertIn("Step 2 of 5", [c.get("text") for c in verify["layout"]["children"]])
        labels = [c.get("label") or c.get("text") for c in verify["layout"]["children"][-1]["children"]]
        self.assertEqual(labels[:4], ["Enter the code", "Resend code",
                                      "Create a password", "Confirm password"])
        meter = next(c for c in _BY_ID["BILL_SCREEN"]["layout"]["children"][-1]["children"]
                     if c.get("name") == "meter")
        self.assertEqual(meter["helper-text"], "11–13 digits")
        amount = next(c for c in _BY_ID["BILL_AMOUNT"]["layout"]["children"][-1]["children"]
                      if c.get("name") == "amount")
        self.assertEqual(amount["helper-text"], "${data.balance}")

    def test_password_and_code_inputs_are_masked_or_numeric(self):
        form = _BY_ID["VERIFY_PASSWORD"]["layout"]["children"][-1]["children"]
        kinds = {c["name"]: c["input-type"] for c in form if c.get("type") == "TextInput"}
        self.assertEqual(kinds, {"phone_code": "number", "password": "password",
                                 "confirm_password": "password"})
