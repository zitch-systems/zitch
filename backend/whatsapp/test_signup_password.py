"""One account, two front doors, and a password that never lands in a chat.

A WhatsApp signup used to create an account with NO app password
(`set_unusable_password`), so the customer who had just finished signing up
could not open the app without first running "Forgot password" on an account
they made two minutes ago. They now choose the password during signup, and the
same email and password sign in to the app.

Where it is typed matters as much as that it is typed. A password sent as a
WhatsApp message is stored forever in the customer's own chat history, in ours,
and on Meta's servers — readable by anyone who picks up the unlocked phone. It
is therefore collected only inside the encrypted Flow, hashed before it is
written anywhere, and never echoed back to the chat or a screen.
"""
from datetime import timedelta

from django.contrib.auth.hashers import check_password
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User, password_rejection
from whatsapp import flows
from whatsapp.models import WaOnboarding

MSISDN = "2348030000001"
GOOD = "Zitch!2026pay"


def _ob(step=flows.FLOW_PASSWORD_STATE, **payload):
    base = {"first_name": "Ada", "last_name": "Obi",
            "email": "ada@example.com", "phone": "08030000001"}
    base.update(payload)
    return WaOnboarding.objects.create(
        msisdn=MSISDN, step=step, payload=base,
        expires_at=timezone.now() + timedelta(minutes=10))


class PasswordRuleTests(TestCase):
    """The rule the sign-up screen has always drawn tick-boxes for — which lived
    only in the mobile client, so it was advice rather than policy."""

    def test_it_names_the_missing_ingredient(self):
        for password, wanted in (
            ("Ab1!", "at least 8 characters"),
            ("12345678!", "must include a letter"),
            ("abcdefgh!", "must include a number"),
            ("abcdefg1", "special character"),
        ):
            self.assertIn(wanted, password_rejection(password), password)

    def test_a_password_meeting_every_rule_is_accepted(self):
        self.assertEqual(password_rejection(GOOD), "")

    def test_djangos_validators_still_have_the_last_word(self):
        """The tick-boxes are necessary, not sufficient: "Password1!" passes
        every one of them and is still among the first guesses ever made."""
        self.assertNotEqual(password_rejection("Password1!"), "")

    def test_it_refuses_the_customers_own_details(self):
        user = User(username="08030000001", email="ada@example.com",
                    first_name="Ada", last_name="Obi")
        self.assertNotEqual(password_rejection("ada@example.com1!", user), "")

    def test_the_app_endpoint_and_the_flow_share_one_rule(self):
        """Two independent rules would mean the weaker door is the one used."""
        from accounts.views import _weak_password

        self.assertEqual(_weak_password("abcdefgh"), password_rejection("abcdefgh"))
        self.assertIsNone(_weak_password(GOOD))


@override_settings(WHATSAPP_FLOW={"PASSWORD_SCREEN": True})
class SignupPasswordScreenTests(TestCase):

    def test_the_phone_step_hands_off_to_the_password_page(self):
        ob = _ob(step=flows.FLOW_PHONE_STATE)
        screen = flows._signup_to_pin(ob)
        self.assertEqual(screen["screen"], flows.SIGNUP_PASSWORD)
        ob.refresh_from_db()
        self.assertEqual(ob.step, flows.FLOW_PASSWORD_STATE)

    def test_a_good_password_is_stored_only_as_a_hash(self):
        """An abandoned signup, or a database read by anyone at all, must yield
        a hash and not a credential."""
        ob = _ob()
        screen = flows._submit_signup_password(
            ob, {"password": GOOD, "confirm_password": GOOD})
        ob.refresh_from_db()
        held = ob.payload["flow_pw_hash"]
        self.assertNotEqual(held, GOOD)
        self.assertNotIn(GOOD, str(ob.payload))
        self.assertTrue(check_password(GOOD, held))
        # ...and it moves on to the PIN, which is the next thing it asks for.
        self.assertEqual(screen["screen"], flows.PIN_CHAIN)
        self.assertEqual(ob.step, flows.FLOW_PIN_STATE)

    def test_a_mismatch_is_refused_before_the_composition_is_criticised(self):
        """Telling someone their two entries differ is more useful than
        critiquing one they may simply have mistyped."""
        ob = _ob()
        screen = flows._submit_signup_password(
            ob, {"password": "abc", "confirm_password": "abd"})
        self.assertEqual(screen["screen"], flows.SIGNUP_PASSWORD)
        self.assertIn("don't match", screen["data"]["error"])
        ob.refresh_from_db()
        self.assertNotIn("flow_pw_hash", ob.payload)

    def test_a_weak_password_is_refused_with_the_reason(self):
        ob = _ob()
        screen = flows._submit_signup_password(
            ob, {"password": "abcdefgh", "confirm_password": "abcdefgh"})
        self.assertIn("number", screen["data"]["error"])
        ob.refresh_from_db()
        self.assertEqual(ob.step, flows.FLOW_PASSWORD_STATE)

    def test_a_refusal_never_echoes_what_was_typed(self):
        ob = _ob()
        screen = flows._submit_signup_password(
            ob, {"password": "secretpw", "confirm_password": "different"})
        self.assertNotIn("secretpw", str(screen))

    def test_an_empty_submission_asks_again_rather_than_accepting_a_blank(self):
        ob = _ob()
        screen = flows._submit_signup_password(ob, {})
        self.assertEqual(screen["screen"], flows.SIGNUP_PASSWORD)
        ob.refresh_from_db()
        self.assertNotIn("flow_pw_hash", ob.payload)

    def test_the_screen_matches_what_the_published_flow_declares(self):
        """The contract the device holds us to — a mismatch here is the
        "Couldn't load content" failure, mid-signup."""
        screen = flows._check_contract(flows._signup_password_screen("Nope"))
        self.assertEqual(set(screen["data"]),
                         set(flows._screen_contract()[flows.SIGNUP_PASSWORD]))

    def test_the_page_re_renders_on_a_reopen_rather_than_losing_the_signup(self):
        ob = _ob()
        answer = flows._handle_flow_request(
            {"action": "INIT", "flow_token": flows.sign_onboarding_token(ob)})
        self.assertEqual(answer["screen"], flows.SIGNUP_PASSWORD)


class GateTests(TestCase):
    """The Flow JSON is published by hand, so the code can reach production
    before the screen does. Answering with a screen Meta has never heard of is
    the failure this gate exists to prevent — here, mid-signup."""

    @override_settings(WHATSAPP_FLOW={"PASSWORD_SCREEN": False})
    def test_off_by_default_signup_goes_straight_to_the_pin_as_before(self):
        ob = _ob(step=flows.FLOW_PHONE_STATE)
        screen = flows._signup_to_pin(ob)
        self.assertEqual(screen["screen"], flows.PIN_CHAIN)
        ob.refresh_from_db()
        self.assertEqual(ob.step, flows.FLOW_PIN_STATE)

    @override_settings(WHATSAPP_FLOW={})
    def test_an_absent_setting_reads_as_off(self):
        self.assertFalse(flows.signup_password_live())

    @override_settings(WHATSAPP_FLOW={"PASSWORD_SCREEN": True})
    def test_a_password_already_chosen_is_not_asked_for_twice(self):
        """The phone step can be reached again on a re-render; that must not
        throw away a password the customer already set."""
        ob = _ob(step=flows.FLOW_PHONE_STATE, flow_pw_hash="pbkdf2$fake")
        self.assertEqual(flows._signup_to_pin(ob)["screen"], flows.PIN_CHAIN)


@override_settings(WHATSAPP_FLOW={"PASSWORD_SCREEN": True})
class TheAccountItCreatesTests(TestCase):

    def _finish(self, **payload):
        from whatsapp.router import _finish_onboarding

        ob = _ob(step=flows.FLOW_PIN_STATE, **payload)
        with self.settings(WHATSAPP={"TOKEN": "", "PHONE_ID": ""}):
            _finish_onboarding(ob, MSISDN, "739184")
        return User.objects.get(phone="08030000001")

    def test_the_password_chosen_on_whatsapp_signs_in_to_the_app(self):
        from django.contrib.auth.hashers import make_password

        user = self._finish(flow_pw_hash=make_password(GOOD))
        self.assertTrue(user.check_password(GOOD))
        self.assertEqual(user.email, "ada@example.com")

    def test_the_signup_row_is_deleted_so_the_hash_does_not_linger(self):
        from django.contrib.auth.hashers import make_password

        self._finish(flow_pw_hash=make_password(GOOD))
        self.assertFalse(WaOnboarding.objects.filter(msisdn=MSISDN).exists())

    def test_a_signup_without_one_still_creates_a_usable_account(self):
        """The gate can be off, and older in-flight signups have no hash. Neither
        may leave a half-made account behind."""
        user = self._finish()
        self.assertFalse(user.has_usable_password())
        self.assertTrue(user.check_transaction_pin("739184"))

    def test_the_pin_still_authorises_payments_independently(self):
        """Two different secrets for two different things: the password opens the
        app, the PIN authorises money. Sharing one would mean signing in and
        spending were the same permission."""
        from django.contrib.auth.hashers import make_password

        user = self._finish(flow_pw_hash=make_password(GOOD))
        self.assertTrue(user.check_transaction_pin("739184"))
        self.assertFalse(user.check_transaction_pin(GOOD[:6]))
