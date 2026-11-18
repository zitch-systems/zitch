"""A Tier 2 upgrade the bank received but did not answer in time is pending,
not refused: the bank may still upgrade the account."""
from unittest.mock import patch

import requests
from django.test import SimpleTestCase

from utility import wema


class Tier2TimeoutTests(SimpleTestCase):
    def call(self, exc):
        with patch.object(wema, "_product_live", return_value=True), \
                patch.object(wema, "_post", side_effect=exc):
            return wema.upgrade_tier2("0123456789", bvn="1" * 11, nin="2" * 11, live_image="x")

    def test_read_timeout_is_pending(self):
        res = self.call(requests.ReadTimeout())
        self.assertFalse(res["success"])
        self.assertTrue(res["pending"])

    def test_request_that_never_left_is_not_pending(self):
        for exc in (requests.ConnectTimeout(), requests.ConnectionError()):
            with self.subTest(exc=type(exc).__name__):
                self.assertFalse(self.call(exc)["pending"])
