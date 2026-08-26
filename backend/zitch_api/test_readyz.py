"""Readiness is stricter than liveness and covers required shared state."""

from unittest.mock import patch

from django.test import TestCase, override_settings


class ReadinessTests(TestCase):
    @override_settings(REQUIRE_SHARED_CACHE=True, PUBLIC_HEALTH_DETAILS=False)
    def test_production_readiness_discloses_no_component_topology(self):
        response = self.client.get("/readyz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": True})
        self.assertEqual(response["Cache-Control"], "no-store")

    @override_settings(REQUIRE_SHARED_CACHE=False)
    def test_database_only_deploy_is_ready(self):
        body = self.client.get("/readyz").json()
        self.assertEqual(body, {"status": True, "db": True, "cache": "not_required"})

    @override_settings(REQUIRE_SHARED_CACHE=True)
    def test_required_shared_cache_round_trips(self):
        response = self.client.get("/readyz")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["cache"])

    @override_settings(REQUIRE_SHARED_CACHE=True)
    @patch("zitch_api.urls._shared_cache_ready", return_value=False)
    def test_required_shared_cache_failure_is_not_ready(self, _cache):
        response = self.client.get("/readyz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": False, "db": True, "cache": False})

    @override_settings(REQUIRE_SHARED_CACHE=True)
    @patch("zitch_api.urls._database_ready", return_value=False)
    def test_database_failure_short_circuits(self, _database):
        response = self.client.get("/readyz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": False, "db": False, "cache": None})
