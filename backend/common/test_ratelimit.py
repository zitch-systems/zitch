import json

from django.core.cache import cache
from django.http import JsonResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from common.ratelimit import ratelimit


@override_settings(RATELIMIT_ENABLE=True)
class RateLimitResponseContractTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def tearDown(self):
        cache.clear()

    def test_rejection_has_stable_pre_execution_code(self):
        @ratelimit("response-contract-test", limit=1, window=60)
        def view(_request):
            return JsonResponse({"success": True})

        first = view(self.factory.post("/", REMOTE_ADDR="203.0.113.1"))
        rejected = view(self.factory.post("/", REMOTE_ADDR="203.0.113.1"))

        self.assertEqual(first.status_code, 200)
        self.assertEqual(rejected.status_code, 429)
        body = json.loads(rejected.content)
        self.assertEqual(body["code"], "rate_limited")
        self.assertNotIn("success", body)
