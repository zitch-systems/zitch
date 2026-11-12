"""Partnership debits the customer's own account; no rail may fall back to a pool.

Zitch does not use Wema's pool (PartnerPayment) product. A WEMA_SOURCE_ACCOUNT
left in an environment must never become the debit source for a customer who
has no Partnership account: that would spend Zitch's corporate account on
their behalf.
"""
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings

from utility import providers, wema

POOL = "0124634738"


def _live_wema():
    keys = {**(settings.WEMA.get("KEYS") or {}), "wallet": "k", "airtime": "k", "bills": "k"}
    return {**settings.WEMA, "CHANNEL_ID": "c", "KEYS": keys, "SIMULATION": False,
            "SOURCE_ACCOUNT": POOL}


@override_settings(WEMA=_live_wema())
class NoPoolFallbackTests(TestCase):

    def test_vas_source_is_never_the_pool(self):
        self.assertEqual(wema._vas_source(), "")

    def test_payout_without_a_customer_account_never_calls_the_bank(self):
        with patch("utility.providers._partnership_reference_blocked", return_value=False), \
                patch("utility.wema.transfer") as transfer:
            result = providers.payout_send(
                "1000", "ref-no-source", "n", account_number="0123456789",
                bank_code="058", bank_name="GTBank", account_name="X", source_account="")
        self.assertFalse(result["success"])
        transfer.assert_not_called()

    def test_airtime_without_a_customer_account_never_calls_the_bank(self):
        with patch("utility.providers._biller_source", return_value=("", None)), \
                patch("utility.wema._post") as post:
            result = wema.purchase_airtime("100", "ref-air", "08000000000", "1", source_account="")
        self.assertFalse(result["success"])
        post.assert_not_called()
