"""A provider's empty float must never be reported as the customer's balance.

From production, 2026-09-08. The confirm card said "Available balance ₦1,000.00"
and the balance command agreed. Seconds later the same chat said:

    ❌ Airtime - MTN (₦100.00 - MTN airtime · To 0816…) failed: Your wallet
    balance (NGN12.25) is insufficient to make this airtime purchase of NGN100.
    You were not charged.

Both figures were real. ₦1,000.00 was the customer's Zitch wallet; NGN12.25 was
OUR VTU.ng float, in a sentence VTU.ng writes in the second person. The customer
was told by their bank that their money had gone.

It cannot be their balance, and that is structural rather than a judgement call:
run_provider_purchase debits the customer BEFORE calling the provider, and
debit() raises InsufficientFunds when the wallet cannot cover the amount. A
provider only ever gets to answer after the customer's money has already moved.
"""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase

from wallet.services import (PROVIDER_FLOAT_MESSAGE, credit,
                             customer_safe_failure, get_or_create_wallet,
                             run_provider_purchase)

User = get_user_model()

# VTU.ng's exact wording, from the screenshots.
VTUNG_FLOAT_EMPTY = ("Your wallet balance (NGN12.25) is insufficient to make "
                     "this airtime purchase of NGN100")


class CustomerSafeFailureTests(TestCase):
    def test_the_reported_message_is_never_shown_to_the_customer(self):
        said = customer_safe_failure({"message": VTUNG_FLOAT_EMPTY}, service="airtime")
        self.assertEqual(said, PROVIDER_FLOAT_MESSAGE)
        self.assertNotIn("12.25", said)
        self.assertNotIn("balance", said.lower())

    def test_no_provider_balance_figure_survives_in_any_phrasing(self):
        """Providers word an empty float differently; none of them may through."""
        for message in (
            VTUNG_FLOAT_EMPTY,
            "Insufficient balance",
            "insufficient funds in wallet",
            "Your balance is too low to complete this transaction",
            "Low balance. Please top up your account.",
            "Wallet is empty",
        ):
            with self.subTest(message=message):
                self.assertEqual(
                    customer_safe_failure({"message": message}), PROVIDER_FLOAT_MESSAGE)

    def test_a_real_customer_error_is_still_relayed(self):
        """Replacing these would trade one bad failure for another.

        "Invalid phone number" is the customer's to act on and only they can.
        """
        for message in ("Invalid phone number for MTN",
                        "Meter number not found",
                        "Smartcard number is invalid",
                        "Network operator could not be determined"):
            with self.subTest(message=message):
                self.assertEqual(customer_safe_failure({"message": message}), message)

    def test_an_empty_provider_message_falls_back(self):
        self.assertEqual(customer_safe_failure({}), "please try again")
        self.assertEqual(customer_safe_failure({"message": "  "}, fallback="Transaction failed"),
                         "Transaction failed")

    def test_an_exhausted_float_pages_an_operator(self):
        """It is an outage: every purchase fails, and only a top-up clears it.

        It also fails quietly — each customer is refunded and shown one line, so
        nothing accumulates into a signal and the first real report is a
        complaint. The provider's own words go to the operator, who is the one
        person they are actually about.
        """
        with self.assertLogs("zitch", level="ERROR") as logs:
            customer_safe_failure({"message": VTUNG_FLOAT_EMPTY}, service="airtime")
        joined = "\n".join(logs.output)
        self.assertIn("12.25", joined)
        self.assertIn("topped up", joined)
        self.assertIn("airtime", joined)


class TheCustomersMoneyIsUntouchedTests(TestCase):
    """The other half of the promise the message makes."""

    def setUp(self):
        # Verified, like any customer who can actually reach a purchase: the
        # spend gate refuses an unverified account long before the provider.
        self.user = User.objects.create(
            username="c1", phone="08011112222", email="c1@zitch.test",
            first_name="Ada", last_name="Eze", tier=1, email_verified=True,
            phone_verified=True, bvn_verified=True, nin_verified=True)
        get_or_create_wallet(self.user)
        credit(self.user, Decimal("1000.00"), "test-topup")

    def test_a_failed_purchase_leaves_the_balance_where_it_was(self):
        wallet = get_or_create_wallet(self.user)
        self.assertEqual(wallet.balance, Decimal("1000.00"))

        status, txn, result = run_provider_purchase(
            self.user, Decimal("100.00"), "Airtime - MTN", {},
            lambda ref: {"success": False, "message": VTUNG_FLOAT_EMPTY},
        )
        self.assertEqual(status, "failed")
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal("1000.00"),
                         "the customer was charged for our empty float")

    def test_the_provider_text_is_kept_on_the_row_for_the_console(self):
        _status, txn, _result = run_provider_purchase(
            self.user, Decimal("100.00"), "Airtime - MTN", {},
            lambda ref: {"success": False, "message": VTUNG_FLOAT_EMPTY},
        )
        txn.refresh_from_db()
        self.assertIn("12.25", txn.meta.get("failure", ""),
                      "operators lost the real reason")
