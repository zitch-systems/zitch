"""Identity Flow deadlines reach Prembly without changing legacy bank requests."""
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings
from urllib3.util import Timeout

from utility import providers


@override_settings(
    PREMBLY={"BASE_URL": "https://identity.example.test", "API_KEY": "test", "APP_ID": "test"},
    WEMA={"SIMULATION": False},
    WEMA_PARTNERSHIP_MODE="active",
    BANK_ACCOUNT_PROVIDER="partnership",
)
class IdentityTimeoutTests(SimpleTestCase):
    def response(self):
        return Mock(status_code=200, json=Mock(return_value={
            "status": True, "response_code": "00", "data": {"firstName": "ADA", "lastName": "EZE"},
        }))

    def test_existing_callers_keep_thirty_second_timeout(self):
        for verify in (providers.verify_bvn, providers.verify_nin,
                       providers.prembly_verify_bvn, providers.prembly_verify_nin):
            with self.subTest(verify=verify.__name__), patch(
                "utility.providers.requests.post", return_value=self.response(),
            ) as post:
                self.assertTrue(verify("12345678901", name="Ada Eze")["success"])
                self.assertEqual(post.call_args.kwargs["timeout"], 30)

    def test_custom_deadline_reaches_prembly_unchanged(self):
        for verify in (providers.verify_bvn, providers.verify_nin,
                       providers.prembly_verify_bvn, providers.prembly_verify_nin):
            for deadline in (3, (1, 2), Timeout(total=3, connect=1, read=2)):
                with self.subTest(verify=verify.__name__, deadline=deadline), patch(
                    "utility.providers.requests.post", return_value=self.response(),
                ) as post:
                    self.assertTrue(verify("12345678901", name="Ada Eze", timeout=deadline)["success"])
                    self.assertIs(post.call_args.kwargs["timeout"], deadline)

    def test_expired_deadline_is_not_a_verified_or_invalid_identity(self):
        for verify in (providers.verify_bvn, providers.verify_nin):
            with self.subTest(verify=verify.__name__), patch(
                "utility.providers.requests.post", side_effect=requests.Timeout("deadline exceeded"),
            ):
                result = verify("12345678901", name="Ada Eze", timeout=3)
                self.assertFalse(result["success"])
                self.assertFalse(result.get("invalid", False))

    @override_settings(PREMBLY={"BASE_URL": "", "API_KEY": "", "APP_ID": ""})
    def test_prembly_timeout_does_not_change_legacy_bank_requests(self):
        with patch("utility.wema.verify_bvn", return_value={"success": True}) as bank:
            providers.verify_bvn("12345678901", "Ada Eze", "1990-01-01", "08012345678", timeout=3)
            bank.assert_called_once_with(
                "12345678901", name="Ada Eze", date_of_birth="1990-01-01", mobile="08012345678",
            )
        with patch("utility.wema.verify_nin", return_value={"success": True}) as bank:
            providers.verify_nin("12345678901", "Ada Eze", timeout=3)
            bank.assert_called_once_with("12345678901", name="Ada Eze")
