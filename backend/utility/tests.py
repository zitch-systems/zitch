"""FX must fail closed in production, like every other money provider.

This file covered the VTU purchase invariant -- debit, call the provider, then
settle or refund -- for airtime, data, cable, electricity and Remita. Those
endpoints were bought through the 9PSB VAS rail and went with it, so the
invariant they protected no longer has a code path. What remains is FX, which
does not go through a bank.
"""
from django.test import TestCase


class FxFailClosedTests(TestCase):
    """FX (Fincra) must fail closed in production like every other money provider
    — a mock rate settling the real ledger would book phantom liability."""

    def test_fx_mock_fails_closed_in_prod(self):
        from django.test import override_settings

        from .providers import fx_execute, fx_quote
        with override_settings(DEBUG=False, TESTING=False,
                               FINCRA={"SECRET_KEY": "", "BASE_URL": "https://x"}):
            self.assertFalse(fx_quote("NGN", "USD", "16000").get("success"))
            self.assertFalse(fx_execute("FXQ-TEST").get("success"))

    def test_fx_mock_still_works_in_dev(self):
        from django.test import override_settings

        from .providers import fx_quote
        with override_settings(DEBUG=True, FINCRA={"SECRET_KEY": "", "BASE_URL": "https://x"}):
            self.assertTrue(fx_quote("NGN", "USD", "16000").get("success"))
