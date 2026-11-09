"""Retired 9PSB must not reappear through the WhatsApp bank picker.

A restored old Bank row can remain for immutable beneficiary/history references,
but is not an eligible new transfer destination even if reactivated by a stale
operator edit. Wema remains available.
"""
from django.test import TestCase

from transfers.models import Bank
from whatsapp.router_tail import _bank_items, _match_banks


class RetiredBankWhatsAppPickerTests(TestCase):
    def setUp(self):
        Bank.objects.create(
            code="9psb", name="9PSB", bank_code="120001", active=True, popular=True)
        Bank.objects.create(
            code="wema", name="Wema Bank", bank_code="035", active=True, popular=True)

    def test_retired_partner_is_not_offered_by_whatsapp(self):
        items = _bank_items()
        self.assertIn("wema", [b["id"] for b in items])
        self.assertNotIn("9psb", [b["id"] for b in items])
        self.assertEqual(_match_banks("9PSB"), [])
        self.assertEqual(_match_banks("9 Payment Service Bank"), [])

    def test_old_provider_is_not_added_when_picker_is_searched(self):
        ids = [b["id"] for b in _bank_items(query="9PSB")]
        self.assertNotIn("9psb", ids)
