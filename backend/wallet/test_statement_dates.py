"""Statement dates fail as user input, without rolling dates or mailing a wrong period."""
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import AccessToken


class StatementDateValidationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="statement-date-test", phone="08019998001", email="statement-date@zitch.test",
        )
        self.token = AccessToken.issue(self.user).key

    def post(self, **dates):
        return self.client.post(
            "/api/wallet/statement/request/",
            data=json.dumps({"file_type": "excel", **dates}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

    @patch("utility.providers.send_email")
    def test_invalid_dates_never_send_a_statement(self, send_email):
        for field in ("from", "to"):
            for value in ("2026-02-31", "2025-02-29", "2026-13-01", "2026-00-01",
                          "2026-01-00", "0000-01-01", "2026-2-1", "not-a-date", "2026-01-01\nignored"):
                with self.subTest(field=field, value=value):
                    payload = {"from": "2020-01-01", "to": "2030-01-01", field: value}
                    response = self.post(**payload)
                    self.assertEqual(response.status_code, 400)
                    self.assertFalse(response.json().get("success"))
                    self.assertIn("YYYY-MM-DD", response.json()["message"])
        response = self.post(**{"from": "2026-01-01", "to": "9999-12-31"})
        self.assertEqual(response.status_code, 400)
        send_email.assert_not_called()

    @patch("utility.providers.send_email", return_value={"success": True})
    def test_a_real_leap_day_is_kept_as_the_requested_period(self, send_email):
        response = self.post(**{"from": "2024-02-29", "to": "2024-02-29"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["from_date"], "2024-02-29")
        self.assertEqual(response.json()["to_date"], "2024-02-29")
        self.assertEqual(send_email.call_args.kwargs["attachments"][0]["filename"],
                         "Zitch-Statement-2024-02-29_2024-02-29.xlsx")
