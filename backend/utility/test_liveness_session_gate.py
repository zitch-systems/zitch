"""No deployment switch or client claim can replace verified session proof."""
from django.test import SimpleTestCase, override_settings

from utility.liveness import tier2_face_available, verify_tier2_liveness


class TierTwoSessionGateTests(SimpleTestCase):
    def test_unconfigured_session_adapter_stays_unavailable_in_all_modes(self):
        for simulation in (False, True):
            with self.subTest(simulation=simulation), override_settings(
                    DEBUG=True, TESTING=True, WEMA={"SIMULATION": simulation},
                    PREMBLY={"API_KEY": "test", "APP_ID": "test", "WIDGET_KEY": "test"},
                    TIER2_FACE_AVAILABLE=True):
                self.assertFalse(tier2_face_available())
                result = verify_tier2_liveness(None, {
                    "success": True, "session_id": "client-selected", "live_image": "ZmFrZQ==",
                    "verification": {"status": "VERIFIED", "reference": "client-reference"},
                })
                self.assertFalse(result["success"])
                self.assertTrue(result["unavailable"])
                self.assertEqual(result["code"], "tier2_liveness_unavailable")
