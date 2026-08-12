import json

from django.test import Client, TestCase, override_settings

from accounts.models import AccessToken
from wallet.tests import make_user


class JsonApiBoundaryTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_rejects_non_json_and_non_object_bodies(self):
        wrong_type = self.client.post("/api/sigin/", data="{}", content_type="text/plain")
        self.assertEqual(wrong_type.status_code, 415)
        array = self.client.post("/api/sigin/", data="[]", content_type="application/json")
        self.assertEqual(array.status_code, 400)

    @override_settings(API_MAX_BODY_BYTES=32, DATA_UPLOAD_MAX_MEMORY_SIZE=64)
    def test_rejects_oversized_json_before_parsing(self):
        response = self.client.post(
            "/api/sigin/",
            data=json.dumps({"email_or_phone": "x" * 100, "password": "x"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 413)

    @override_settings(ALLOW_BODY_ACCESS_TOKEN=False)
    def test_production_auth_accepts_bearer_but_not_body_tokens(self):
        user, _ = make_user("08091910001", "bearer-only@zitch.test")
        token = AccessToken.issue(user).key
        body_only = self.client.post(
            "/api/wallet_balance/",
            data=json.dumps({"access_token": token}),
            content_type="application/json",
        )
        self.assertEqual(body_only.status_code, 401)
        bearer = self.client.post(
            "/api/wallet_balance/",
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(bearer.status_code, 200)
