"""Electricity: the meter address whatever the bank calls it, the bank's minimum, logos.

16 Nov 2026: a ₦500 Ikeja prepaid payment passed every Zitch check and was refused
by the bank after the PIN ("You cannot pay less than ₦1000.00 for this bill"), and
the confirm card read "Address: Not provided by the disco".
"""
import base64
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase

from common.http import MIN_ELECTRICITY
from utility.wema import _bill_customer_address


class BillCustomerAddressTests(SimpleTestCase):
    def test_known_spellings(self):
        self.assertEqual(_bill_customer_address({"customerAddress": "12 Marina"}), "12 Marina")
        self.assertEqual(_bill_customer_address({"address": "3 Allen Ave"}), "3 Allen Ave")

    def test_any_field_named_like_an_address(self):
        self.assertEqual(_bill_customer_address({"customerName": "H OKAFOR",
                                                 "CustomerAddress1": " 5 Ikeja Rd "}), "5 Ikeja Rd")

    def test_one_level_down(self):
        self.assertEqual(_bill_customer_address({"otherInfo": {"address": "7 Opebi"}}), "7 Opebi")

    def test_nothing_usable(self):
        self.assertEqual(_bill_customer_address({"customerName": "X", "address": "  "}), "")
        self.assertEqual(_bill_customer_address({}), "")


class ElectricityMinimumTests(SimpleTestCase):
    def test_minimum_is_the_banks(self):
        self.assertEqual(MIN_ELECTRICITY, Decimal("1000"))


class DiscoLogoTests(SimpleTestCase):
    def setUp(self):
        from whatsapp import release2

        release2._disco_logo.cache_clear()
        self.addCleanup(release2._disco_logo.cache_clear)

    def test_a_supplied_logo_rides_beside_the_disco_and_a_missing_one_is_skipped(self):
        import tempfile
        from pathlib import Path

        from whatsapp import release2

        png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "IKEDC.png").write_bytes(png)
            with mock.patch.object(release2, "_DISCO_LOGO_DIR", Path(tmp)):
                options = {o["id"]: o for o in release2._disco_options()}
        self.assertEqual(options["1"]["image"], base64.b64encode(png).decode())
        self.assertEqual(options["1"]["alt-text"], "Ikeja Electric")
        self.assertNotIn("image", options["2"])
        self.assertEqual(options["1"]["title"], "Ikeja Electric (IKEDC)")
