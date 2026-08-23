"""The bill-payment lookup endpoints are third-party PII oracles.

validate_meter returns a stranger's NAME and HOME ADDRESS for a meter number,
validate_iuc their name for a decoder number. Unthrottled, a session token turns
those into an enumeration tool over arbitrary Nigerians — the same risk
wallet.resolve_recipient is throttled against — and each lookup also costs money
at the VTU provider.
"""
import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from wallet.tests import make_user


@override_settings(RATELIMIT_ENABLE=True)
class PiiLookupThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user, self.token = make_user("08070000001", "lookup@zitch.test")

    def tearDown(self):
        cache.clear()

    def _post(self, path, body):
        return self.client.post(path, data=json.dumps({"access_token": self.token, **body}),
                                content_type="application/json")

    @patch("utility.views.vtu_verify_customer",
           return_value={"success": True, "customer_name": "ADA EZE",
                         "customer_address": "12 Marina, Lagos"})
    def test_meter_lookup_is_throttled(self, _verify):
        body = {"disco": "1", "meter": "04123456789", "meter_type": "prepaid"}
        allowed = 0
        for _ in range(40):
            res = self._post("/api/utility/validate_meter/", body)
            if res.status_code == 429:
                break
            allowed += 1
        self.assertLess(allowed, 40, "unbounded meter lookups leak names and addresses")
        self.assertLessEqual(allowed, 20)

    @patch("utility.views.vtu_verify_customer",
           return_value={"success": True, "customer_name": "ADA EZE"})
    def test_iuc_lookup_is_throttled(self, _verify):
        body = {"cablenetwork": "1", "iuc": "1234567890"}
        allowed = 0
        for _ in range(40):
            res = self._post("/api/utility/validate_iuc/", body)
            if res.status_code == 429:
                break
            allowed += 1
        self.assertLessEqual(allowed, 20)
