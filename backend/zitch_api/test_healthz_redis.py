"""Whether the shared cache is actually Redis and actually reachable.

Not settled by "the app answered /healthz": DJANGO_REQUIRE_SHARED_CACHE can be
set false per-service, which lets a Redis-less boot pass silently instead of
refusing to start — exactly what the production audit found. This is the only
reading that proves the round-trip, not just the config string.
"""
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings


class RedisHealthzTests(SimpleTestCase):

    @override_settings(REDIS_URL="", CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
    def test_unconfigured_reports_reachable_as_unknown_not_false(self):
        body = self.client.get("/healthz").json()["integrations"]["redis"]
        self.assertEqual(body, {"configured": False, "backend_is_redis": False, "reachable": None})

    @override_settings(REDIS_URL="redis://cache:6379",
                       CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
    def test_configured_but_not_the_active_backend_is_flagged(self):
        """REDIS_URL set with LocMem still active — the override the audit
        described: configured, but not actually wired as the cache."""
        body = self.client.get("/healthz").json()["integrations"]["redis"]
        self.assertEqual(body["configured"], True)
        self.assertEqual(body["backend_is_redis"], False)

    @override_settings(REDIS_URL="redis://cache:6379",
                       CACHES={"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache",
                                          "LOCATION": "redis://cache:6379"}})
    def test_a_working_redis_round_trips(self):
        with patch("django.core.cache.cache.set") as set_, \
             patch("django.core.cache.cache.get", return_value="1"):
            body = self.client.get("/healthz").json()["integrations"]["redis"]
        set_.assert_called_once()
        self.assertEqual(body, {"configured": True, "backend_is_redis": True, "reachable": True})

    @override_settings(REDIS_URL="redis://cache:6379",
                       CACHES={"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache",
                                          "LOCATION": "redis://unreachable:6379"}})
    def test_configured_as_redis_but_unreachable_is_caught_not_raised(self):
        with patch("django.core.cache.cache.set", side_effect=ConnectionError("refused")):
            resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["integrations"]["redis"]["reachable"], False)
