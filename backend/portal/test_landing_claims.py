"""The served landing pages must not make claims the business cannot support.

Two separate problems were live on `/`:

1. **Currency/remittance claims.** `compliance/WHATSAPP_PAYMENTS_COMPLIANCE_QA_v1.0.md`
   §3 answers a regulator's request for an IMTO licence by stating that Zitch "does
   not currently offer international remittance services or multi-currency transfers"
   and "currently serves domestic VTU, bill payment, and money transfer only" — and
   commits to removing the website's currency references. The page was simultaneously
   advertising a multi-currency wallet and quoting four exchange rates.

2. **Fabricated metrics.** "5,000+ people" and "₦1bn+ in transactions processed", on a
   product that has not launched and has moved no real money.

This test exists because prose regressions are invisible: a designer restoring an old
hero section, or a copy revert, puts a regulatory contradiction back on a public page
with nothing to catch it. Both served copies are checked, because they are separate
files that drifted from the same source.
"""
from pathlib import Path

from django.test import TestCase

BACKEND = Path(__file__).resolve().parent.parent
PAGES = {
    "portal (served at /)": BACKEND / "portal" / "templates" / "portal" / "landing.html",
    "console (served at /console/)": BACKEND / "console" / "pages" / "landing.html",
}

# Substrings that must not appear. Each one is a specific claim, not a keyword sweep —
# so the failure message says which claim came back.
BANNED = {
    "5,000+": "fabricated user count",
    "₦1bn": "fabricated transaction volume",
    "multi-currency": "multi-currency claim (compliance QA §3 denies it)",
    "Multi-currency": "multi-currency claim (compliance QA §3 denies it)",
    "NGN → USD": "quoted FX rate",
    "NGN → GBP": "quoted FX rate",
    "NGN → CAD": "quoted FX rate",
    "NGN → CNY": "quoted FX rate",
    "IMTO": "international remittance claim",
    "international money transfer": "international remittance claim",
}


class LandingClaimsTests(TestCase):
    def test_no_unsupportable_claims_on_the_served_pages(self):
        for label, path in PAGES.items():
            body = path.read_text(encoding="utf-8")
            for needle, why in BANNED.items():
                self.assertNotIn(
                    needle, body,
                    f"{label}: {path.name} contains {needle!r} — {why}. See "
                    "compliance/WHATSAPP_PAYMENTS_COMPLIANCE_QA_v1.0.md §3.")

    def test_the_currencies_section_and_its_links_are_gone_together(self):
        # A dangling #fx anchor is how half a revert survives review: the section is
        # removed but the nav still advertises "Currencies".
        for label, path in PAGES.items():
            body = path.read_text(encoding="utf-8")
            self.assertNotIn('id="fx"', body, f"{label}: currencies section is back")
            self.assertNotIn('href="#fx"', body, f"{label}: link to a removed section")

    def test_the_pages_still_render(self):
        # The edits removed a whole <section>; make sure neither surface 500s and both
        # still describe the product.
        for url in ("/", "/console/"):
            res = self.client.get(url)
            self.assertEqual(res.status_code, 200, url)
            self.assertIn("Zitch", res.content.decode("utf-8"))

    def test_the_store_listing_matches(self):
        # The Play/App Store description is a public claim on the same footing as the
        # website, and it made the same one.
        import json
        app_json = json.loads((BACKEND.parent / "app.json").read_text(encoding="utf-8"))
        description = app_json["expo"]["description"]
        for needle in ("multi-currency", "dollar card"):
            self.assertNotIn(needle, description.lower(),
                             f"app.json description still claims {needle!r}")
