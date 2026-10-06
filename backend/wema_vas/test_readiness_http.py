import os
from unittest.mock import patch

from django.db import DatabaseError
from django.test import SimpleTestCase


class VasReadinessHttpTests(SimpleTestCase):
    def setUp(self):
        environ = patch.dict(os.environ, {"DIAG_TOKEN": "operator-secret", "WEMA_DIAG_TOKEN": ""})
        environ.start()
        self.addCleanup(environ.stop)

    def get(self, suffix="", token="operator-secret"):
        return self.client.get("/vas-preflight" + suffix, HTTP_AUTHORIZATION="Bearer " + token)

    @patch("wema_vas.management.commands.vas_preflight.build_report")
    def test_authentication_precedes_database_inspection(self, build):
        self.assertEqual(self.get(token="bank-only-token").status_code, 403)
        self.assertEqual(self.client.get("/vas-preflight?token=operator-secret").status_code, 403)
        build.assert_not_called()

    @patch("wema_vas.management.commands.vas_preflight.build_report")
    def test_only_get_and_known_stages_are_accepted(self, build):
        self.assertEqual(self.client.post("/vas-preflight", HTTP_AUTHORIZATION="Bearer operator-secret").status_code, 405)
        self.assertEqual(self.get("?stage=general").status_code, 400)
        build.assert_not_called()

    @patch("wema_vas.management.commands.vas_preflight.build_report")
    def test_local_validation_readiness_is_not_full_go_live(self, build):
        build.return_value = {"stage": "validation", "local_ready": True, "full_go_live_ready": False}
        response = self.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["vas_preflight"]["full_go_live_ready"])
        self.assertIn("no-store", response["Cache-Control"])
        build.assert_called_once_with(stage="validation")

    @patch("wema_vas.management.commands.vas_preflight.build_report")
    def test_local_pilot_readiness_cannot_be_interpreted_as_release_approval(self, build):
        build.return_value = {"stage": "controlled-live-pilot", "local_ready": True, "full_go_live_ready": False}
        self.assertEqual(self.get("?stage=controlled-live-pilot").status_code, 503)

    @patch("wema_vas.management.commands.vas_preflight.build_report")
    def test_failed_inspection_never_exposes_database_details(self, build):
        build.side_effect = DatabaseError("postgres://user:private-password@db/secret")
        response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["vas_preflight"]["status"], "inspection_unavailable")
        self.assertNotIn("private-password", response.content.decode())

    @patch("wema_vas.management.commands.vas_preflight.build_report")
    def test_failed_local_checks_return_unavailable(self, build):
        build.return_value = {"local_ready": False, "full_go_live_ready": False}
        self.assertEqual(self.get().status_code, 503)
