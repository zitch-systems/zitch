"""Contacts read off a BVN/NIN record.

The ownership challenge is only worth running because its code goes to the
IDENTITY's contacts, not the Zitch account's: a name match proves someone knows
a name, a code delivered to the registered line or inbox proves they hold it.
These tests pin what counts as a usable contact on the record.
"""
from django.test import SimpleTestCase

from utility.providers import _record_email, _record_phone


class RecordEmailTests(SimpleTestCase):
    def test_every_spelling_the_provider_uses_is_read(self):
        # Prembly's naming is not stable across the BVN and NIN products, so a
        # record that HAS an email must not read as one that has none.
        for field in ("email", "emailAddress", "email_address", "emailaddress"):
            self.assertEqual(_record_email({field: "Ada@Example.com"}), "ada@example.com")

    def test_a_record_without_an_email_is_normal_not_an_error(self):
        # Most records carry no email at all. "" is the answer the caller expects,
        # and it falls back to SMS alone rather than to the account's address.
        self.assertEqual(_record_email({}), "")
        self.assertEqual(_record_email({"email": ""}), "")
        self.assertEqual(_record_email({"email": None}), "")

    def test_something_that_is_not_an_address_is_not_postable(self):
        for junk in ("not-an-email", "no@domain", "a b@c.com", "@example.com", "x@"):
            self.assertEqual(_record_email({"email": junk}), "")

    def test_an_absurdly_long_value_is_refused(self):
        self.assertEqual(_record_email({"email": "a" * 250 + "@example.com"}), "")

    def test_the_first_populated_field_wins(self):
        self.assertEqual(
            _record_email({"email": "first@example.com", "emailAddress": "second@example.com"}),
            "first@example.com")


class RecordPhoneStillHoldsTests(SimpleTestCase):
    """The email is an addition, not a replacement — the line is still the
    primary channel and must keep behaving exactly as it did."""

    def test_the_registered_line_is_still_normalised(self):
        self.assertEqual(_record_phone({"phoneNumber": "08031234567"}), "2348031234567")

    def test_a_record_with_no_line_still_reads_empty(self):
        self.assertEqual(_record_phone({}), "")
