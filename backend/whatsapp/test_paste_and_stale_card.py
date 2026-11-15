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
        from transfers.models import Bank

        self.user, _ = make_user(phone="08010000041", email="paste@zitch.test")
        for code, name, bank_code in (("moniepoint", "Moniepoint MFB", "50515"),
                                      ("gtb", "GTBank", "058")):
            Bank.objects.create(code=code, name=name, bank_code=bank_code, active=True)

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
            router._start_transfer_from_paste(self.user, MSISDN, "01827364728 moniepoint 2500")
        self.assertEqual(begin.call_args.args[2], Decimal("2500"))

    def test_a_number_with_no_amount_and_no_bank_is_not_a_paste(self):
        """No bank this table knows, no amount, no "send": nothing says transfer."""
        with patch.object(router, "_begin_bank_transfer") as begin:
            for last_resort in (False, True):
                handled = router._start_transfer_from_paste(
                    self.user, MSISDN, "01827364728 opay", last_resort=last_resort)
                self.assertFalse(handled)
        begin.assert_not_called()

    def test_a_number_and_a_bank_with_no_amount_asks_for_the_amount(self):
        """A transfer missing only its amount. The model reads it first ("four
        thousand naira" is an amount this parser cannot see); asked for last."""
        from transfers.models import Bank

        Bank.objects.create(code="opay", name="OPay", bank_code="999992", active=True)
        self.assertFalse(router._start_transfer_from_paste(self.user, MSISDN, "01827364728 opay"))
        with patch.object(router, "reply") as reply, \
             patch.object(router, "_begin_bank_transfer") as begin:
            handled = router._start_transfer_from_paste(self.user, MSISDN, "01827364728 opay",
                                                        last_resort=True)
        self.assertTrue(handled)
        begin.assert_not_called()
        self.assertIn("How much would you like to send to OPay 01827364728", reply.call_args[0][1])
        pa = PendingAction.objects.get(msisdn=MSISDN)
        self.assertEqual((pa.action_type, pa.state, pa.payload["account"]),
                         ("transfer", "amount", "01827364728"))


class StaleConfirmCardTests(TestCase):
    """The confirm card stays in the thread with its button live.

    WhatsApp cannot retract or disable a button on a delivered message, so after a
    payment is approved by fingerprint the Flow's own "Use PIN" button is still
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
        from wallet.models import Transaction
        Transaction.objects.create(user=self.user, amount=Decimal("3100"),
            reference="stale-card-settled", idempotency_key=f"wa-{self.pa.pk}",
            transaction_status=Transaction.SUCCESS, direction=Transaction.OUT, service="Transfer")
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


class AmbiguousPasteTests(TestCase):
    """Two account numbers, or two different amounts, are not guessed between."""

    def test_two_different_amounts_leave_the_amount_open(self):
        self.assertEqual(router._paste_parts("send 2000 to 0123456789 gtb for 2 bags"),
                         ("0123456789", None))
        self.assertEqual(router._paste_parts("send 5k to 0123456789 gtb 5k"), ("0123456789", Decimal("5000")))

    def test_two_different_numbers_leave_the_account_open(self):
        self.assertEqual(router._paste_parts("call 08031234567, send 5000 to 0123456789 gtb"),
                         (None, Decimal("5000")))
        self.assertEqual(router._paste_parts("0123456789 0123456789 gtb 5000"),
                         ("0123456789", Decimal("5000")))
