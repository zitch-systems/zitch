"""An account opened by any Wema product is found by the account lookup.

The hosted face check opens the account through create-account-face, whose
lookup is the only one that sees it. Asking only account-creation reported
"not found" while account opening said "customer already exists".
"""
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from utility import wema


def _live():
    keys = {**(settings.WEMA.get("KEYS") or {}), "wallet": "k", "wallet_bvn": "k",
            "wallet_nin": "k", "face_account": "k"}
    return {**settings.WEMA, "CHANNEL_ID": "c", "KEYS": keys, "SIMULATION": False}


def _found(n="0123456789"):
    return {"success": True, "account_number": n, "account_name": "EZE ADA",
            "account_lookup_state": "found"}


_MISS = {"success": False, "account_number": "", "account_lookup_state": "not_found"}


@override_settings(WEMA=_live())
class AccountLookupAcrossProductsTests(SimpleTestCase):
    def test_face_opened_account_is_found(self):
        calls = []

        def read(product, phone):
            calls.append(product)
            return _found() if product == "face_account" else dict(_MISS)

        with patch("utility.wema._get_account_details_from", side_effect=read):
            result = wema.get_account_details("08000000000", bvn=True)
        self.assertEqual(result["account_number"], "0123456789")
        self.assertEqual(calls, ["wallet_bvn", "face_account"])

    def test_primary_answer_returned_when_no_product_has_an_account(self):
        with patch("utility.wema._get_account_details_from", return_value=dict(_MISS)) as read:
            result = wema.get_account_details("08000000000", bvn=False)
        self.assertEqual(result["account_lookup_state"], "not_found")
        self.assertEqual([c.args[0] for c in read.call_args_list],
                         ["wallet_nin", "face_account", "wallet_bvn"])

    def test_primary_hit_stops_the_search(self):
        with patch("utility.wema._get_account_details_from", return_value=_found()) as read:
            wema.get_account_details("08000000000", bvn=True)
        self.assertEqual(read.call_count, 1)
