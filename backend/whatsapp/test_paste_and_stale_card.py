"""Two things a customer did in production that the channel got wrong."""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from whatsapp import flows, router
from whatsapp.models import PendingAction
from whatsapp.tests import make_user

MSISDN = "2348011116666"


class AmountParsingTests(TestCase):
    """Amounts arrive inside sentences, not as bare tokens."""

    def test_a_trailing_full_stop_no_longer_refuses_the_amount(self):
        # The actual cause of "12300. Moniepoint 01827364728 cravings" falling
        # through to a blank form: the parser demanded a bare number.
        self.assertEqual(router.parse_amount("12300."), Decimal("12300"))

    def test_shorthand_still_works(self):
        self.assertEqual(router.parse_amount("5k"), Decimal("5000"))
        self.assertEqual(router.parse_amount("2m"), Decimal("2000000"))
        self.assertEqual(router.parse_amount("1,500"), Decimal("1500"))

    def test_currency_and_punctuation_together(self):
        self.assertEqual(router.parse_amount("₦2,000."), Decimal("2000"))

    def test_words_and_bare_punctuation_are_still_refused(self):
        for t in ("cravings", ".", "", "Moniepoint"):
            self.assertIsNone(router.parse_amount(t), t)


class PastedTransferTests(TestCase):
    """"12300. Moniepoint 01827364728 cravings" opened the guided form instead.

    The parser only recognised a 10-digit NUBAN, but the app-first banks —
    Moniepoint, OPay, PalmPay, Kuda — address an account by the customer's 11-digit
    phone number. So a complete instruction was answered with a blank form, which
    reads as the assistant ignoring what it was told.
    """

    def setUp(self):
        self.user, _ = make_user(phone="08010000041", email="paste@zitch.test")

    def test_an_eleven_digit_account_is_recognised(self):
        with patch.object(router, "_begin_bank_transfer", return_value=True) as begin:
            handled = router._start_transfer_from_paste(
                self.user, MSISDN, "12300. Moniepoint 01827364728 cravings")
        self.assertTrue(handled)
        begin.assert_called_once()
        self.assertEqual(begin.call_args.args[2], Decimal("12300"))
        self.assertEqual(begin.call_args.args[3], "01827364728")

    def test_a_ten_digit_nuban_still_works(self):
        with patch.object(router, "_begin_bank_transfer", return_value=True) as begin:
            router._start_transfer_from_paste(self.user, MSISDN, "0123456789 GTBank 5000")
        self.assertEqual(begin.call_args.args[3], "0123456789")

    def test_the_amount_is_not_taken_from_the_account_number(self):
        with patch.object(router, "_begin_bank_transfer", return_value=True) as begin:
            router._start_transfer_from_paste(self.user, MSISDN, "01827364728 opay 2500")
        self.assertEqual(begin.call_args.args[2], Decimal("2500"))

    def test_a_message_with_no_amount_is_not_a_paste(self):
        with patch.object(router, "_begin_bank_transfer") as begin:
            handled = router._start_transfer_from_paste(self.user, MSISDN, "01827364728 opay")
        self.assertFalse(handled)
        begin.assert_not_called()


class StaleConfirmCardTests(TestCase):
    """The confirm card stays in the thread with its button live.

    WhatsApp cannot retract or disable a button on a delivered message, so after a
    payment is approved by fingerprint the Flow's own "Use PIN instead" is still
    tappable. It used to answer "This request expired or was already completed",
    which reads as a failure on a payment that in fact succeeded.
    """

    def setUp(self):
        # The settled marker lives in the process cache, which outlives a test's
        # database rollback — and rollback recycles primary keys, so without this a
        # neighbouring test's outcome is read back under an identical token.
        from django.core.cache import cache
        cache.clear()
        self.user, _ = make_user(phone="08010000042", email="stale@zitch.test")
        self.pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer",
            state=flows.FLOW_PIN_STATE, payload={},
            expires_at=timezone.now() + timezone.timedelta(minutes=10))
        self.token = flows.sign_flow_token(self.pa)

    def test_a_settled_payment_answers_with_what_happened(self):
        flows.remember_settled(self.pa, "₦3,100.00 sent to ADEYEMI WILLIAM.")
        self.pa.delete()
        res = flows.handle_flow_request({"action": "data_exchange",
                                         "flow_token": self.token,
                                         "data": {"pin": "1234"}})
        self.assertEqual(res["screen"], flows.RESULT_SCREEN)
        self.assertEqual(res["data"]["status"], "✅ Successful")
        self.assertIn("ADEYEMI WILLIAM", res["data"]["message"])
        self.assertIn("receipt", res["data"]["message"].lower())

    def test_a_genuinely_expired_action_still_says_so(self):
        # Nothing was recorded, so we do not know it succeeded — and claiming it
        # did would be worse than the vague message.
        self.pa.delete()
        res = flows.handle_flow_request({"action": "data_exchange",
                                         "flow_token": self.token,
                                         "data": {"pin": "1234"}})
        self.assertIn("expired", res["data"]["message"].lower())

    def test_recording_never_raises_on_a_missing_action(self):
        flows.remember_settled(None, "x")  # must not raise
        self.assertEqual(flows.settled_outcome("notatoken"), "")

    def test_a_forged_token_cannot_read_somebody_elses_outcome(self):
        # Action ids are sequential, so keying this on the id alone let anyone who
        # guessed one read back the amount and the recipient of a real payment.
        flows.remember_settled(self.pa, "₦3,100.00 sent to ADEYEMI WILLIAM.")
        forged = f"{self.pa.pk}.deadbeefdeadbeefdeadbe"
        self.assertEqual(flows.settled_outcome(forged), "")
