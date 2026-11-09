"""Production activation must never follow simply from stale Wema credentials."""
from django.test import SimpleTestCase, override_settings

from utility.providers import (
    biller_enabled, partnership_archived, partnership_new_business_allowed,
)


class WemaCutoverApprovalTests(SimpleTestCase):
    @override_settings(DEBUG=False, TESTING=False,
                       WEMA_PARTNERSHIP_MODE="active", WEMA_BILLER_MODE="active",
                       WEMA_CUTOVER_APPROVED=False)
    def test_production_without_cutover_approval_is_inert(self):
        self.assertTrue(partnership_archived())
        self.assertFalse(partnership_new_business_allowed())
        self.assertFalse(biller_enabled())

    @override_settings(DEBUG=False, TESTING=False,
                       WEMA_PARTNERSHIP_MODE="active", WEMA_BILLER_MODE="active",
                       WEMA_CUTOVER_APPROVED=True)
    def test_explicit_approval_enables_only_active_products(self):
        self.assertFalse(partnership_archived())
        self.assertTrue(partnership_new_business_allowed())
        self.assertTrue(biller_enabled())

    @override_settings(DEBUG=False, TESTING=False,
                       WEMA_PARTNERSHIP_MODE="archived", WEMA_BILLER_MODE="disabled",
                       WEMA_CUTOVER_APPROVED=True)
    def test_approval_does_not_override_disabled_products(self):
        self.assertTrue(partnership_archived())
        self.assertFalse(partnership_new_business_allowed())
        self.assertFalse(biller_enabled())
