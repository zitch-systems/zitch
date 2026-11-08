"""Dashboard-pasted values keep their meaning despite surrounding whitespace."""
import os
from unittest.mock import patch

from django.test import SimpleTestCase

from zitch_api.settings import env_bool, env_text


class EnvParsingTests(SimpleTestCase):
    def test_flags_with_a_pasted_newline_still_enable(self):
        for value, expected in (("true\n", True), (" TRUE ", True), ("1\r\n", True),
                                ("false\n", False), ("", False), ("truthy", False)):
            with self.subTest(value=value), patch.dict(os.environ, {"NINEPSB_ENABLED": value}):
                self.assertIs(env_bool("NINEPSB_ENABLED", False), expected)

    def test_bank_credentials_and_urls_drop_surrounding_whitespace_only(self):
        with patch.dict(os.environ, {"NINEPSB_VAS_USERNAME": " PK_TEST_example\n",
                                     "NINEPSB_VAS_AUTH_URL": "http://102.216.128.75:9090/identity/api/v1/authenticate\n"}):
            self.assertEqual(env_text("NINEPSB_VAS_USERNAME"), "PK_TEST_example")
            self.assertEqual(env_text("NINEPSB_VAS_AUTH_URL"),
                             "http://102.216.128.75:9090/identity/api/v1/authenticate")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(env_text("NINEPSB_ENVIRONMENT", "sandbox"), "sandbox")
