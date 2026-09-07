"""Reading a payment QR.

The parser is the part that can hurt somebody: it turns a photographed poster into
an account number, and a wrong digit there sends a customer's money to a stranger
who is under no obligation to give it back. So the tests lean hardest on the cases
where it must REFUSE — a damaged payload, a code that is not a payment, a merchant
scheme we cannot settle — rather than on the happy path.
"""
from decimal import Decimal

from django.test import SimpleTestCase

from utility.qr import decode_image, parse_payment


def tlv(tag, value):
    return f"{tag}{len(value):02d}{value}"


def crc(body):
    c = 0xFFFF
    for byte in body.encode():
        c ^= byte << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return f"{c:04X}"


def emv(*, account="0123456789", amount="1500.00", name="ADA STORE",
        scheme="ng.com.nqr", dynamic=True, reference="REF9"):
    merchant = tlv("00", scheme) + (tlv("01", account) if account else "")
    body = (tlv("00", "01") + tlv("01", "12" if dynamic else "11") + tlv("26", merchant)
            + tlv("52", "4814") + tlv("53", "566")
            + (tlv("54", amount) if amount else "")
            + tlv("58", "NG") + tlv("59", name) + tlv("60", "Lagos")
            + (tlv("62", tlv("05", reference)) if reference else "") + "6304")
    return body + crc(body)


class EmvParsingTests(SimpleTestCase):
    def test_a_valid_merchant_code_resolves_to_a_payable_account(self):
        p = parse_payment(emv())
        self.assertEqual(p["kind"], "emv")
        self.assertTrue(p["payable"])
        self.assertEqual(p["account"], "0123456789")
        self.assertEqual(p["merchant_name"], "ADA STORE")
        self.assertEqual(p["amount"], Decimal("1500.00"))
        self.assertEqual(p["currency"], "NGN")
        self.assertEqual(p["reference"], "REF9")
        self.assertTrue(p["dynamic"])

    def test_a_static_poster_carries_no_amount(self):
        p = parse_payment(emv(amount="", dynamic=False))
        self.assertTrue(p["payable"])
        self.assertIsNone(p["amount"])
        self.assertFalse(p["dynamic"])

    def test_a_zero_amount_is_treated_as_no_amount(self):
        # "Pay ₦0" is a malformed dynamic code; asking what to send beats sending
        # nothing and calling it done.
        self.assertIsNone(parse_payment(emv(amount="0.00"))["amount"])

    def test_a_merchant_id_that_is_not_a_nuban_is_not_payable(self):
        # It parses perfectly — we simply cannot settle it, and the caller has to
        # be able to tell that apart from a broken scan.
        p = parse_payment(emv(account="MERCHANT00099"))
        self.assertEqual(p["kind"], "emv")
        self.assertFalse(p["payable"])
        self.assertEqual(p["merchant_name"], "ADA STORE")

    def test_one_flipped_digit_is_caught_by_the_checksum(self):
        good = emv()
        bad = good.replace("0123456789", "0123456780", 1)
        p = parse_payment(bad)
        self.assertTrue(p["corrupt"])
        self.assertFalse(p["payable"])
        self.assertNotIn("account", p)

    def test_a_truncated_payload_is_refused_rather_than_half_read(self):
        p = parse_payment(emv()[:40])
        self.assertFalse(p.get("payable"))

    def test_a_length_that_overruns_the_buffer_yields_nothing(self):
        # "9999" bytes of value in a 20-byte string: guessing past the damage is
        # how a parser invents an account number.
        body = tlv("00", "01") + "2699SHORT" + "6304"
        self.assertEqual(parse_payment(body + crc(body))["kind"], "other")


class NonPaymentCodeTests(SimpleTestCase):
    def test_a_wifi_code_is_not_a_payment(self):
        self.assertEqual(parse_payment("WIFI:S:Net;T:WPA;P:pw;;")["kind"], "other")

    def test_a_url_containing_ten_digits_is_not_an_account(self):
        # The digits are a tracking id. Offering to send money to them would be
        # confidently wrong.
        p = parse_payment("https://example.com/track/0123456789/x")
        self.assertEqual(p["kind"], "other")
        self.assertFalse(p["payable"])

    def test_a_zitch_pay_link_resolves(self):
        p = parse_payment("https://zitch.ng/pay?account=0123456789")
        self.assertEqual(p["kind"], "zitch")
        self.assertEqual(p["account"], "0123456789")

    def test_a_bare_account_number_resolves(self):
        p = parse_payment("0123456789")
        self.assertEqual(p["kind"], "account")
        self.assertTrue(p["payable"])

    def test_an_oversized_payload_is_refused(self):
        self.assertEqual(parse_payment("0" * 9000)["kind"], "other")

    def test_empty_input_is_safe(self):
        self.assertEqual(parse_payment("")["kind"], "other")
        self.assertIsNone(decode_image(b""))

    def test_a_non_image_does_not_raise(self):
        self.assertIsNone(decode_image(b"not an image at all"))


class ImageDecodingTests(SimpleTestCase):
    def test_a_photographed_code_round_trips(self):
        try:
            import io

            import qrcode
        except ImportError:  # pragma: no cover
            self.skipTest("qrcode not installed")
        payload = emv()
        buf = io.BytesIO()
        qrcode.make(payload).convert("L").save(buf, format="PNG")
        self.assertEqual(parse_payment(decode_image(buf.getvalue()))["account"],
                         "0123456789")
