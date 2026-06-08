"""Tests for profile avatar upload."""
import base64
import json

from django.test import Client, TestCase, override_settings

from wallet.tests import make_user

# A 1x1 transparent PNG.
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()


@override_settings(MEDIA_ROOT="/tmp/zitch-test-media")
class AvatarUploadTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08030000009", "pic@zitch.test")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def test_upload_sets_avatar_and_returns_url(self):
        res, body = self.post("/api/profile/avatar/", {"access_token": self.token, "image": PNG_DATA_URL})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertIn("/media/avatars/", body["avatar"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar.startswith("avatars/"))

    def test_avatar_appears_in_wallet_balance(self):
        self.post("/api/profile/avatar/", {"access_token": self.token, "image": PNG_DATA_URL})
        res, body = self.post("/api/wallet_balance/", {"access_token": self.token})
        self.assertEqual(res.status_code, 200)
        self.assertIn("/media/avatars/", body["user_avatar"])

    def test_requires_auth(self):
        res, _ = self.post("/api/profile/avatar/", {"image": PNG_DATA_URL})
        self.assertEqual(res.status_code, 401)

    def test_rejects_empty(self):
        res, _ = self.post("/api/profile/avatar/", {"access_token": self.token, "image": ""})
        self.assertEqual(res.status_code, 400)

    def test_rejects_invalid_base64(self):
        res, _ = self.post("/api/profile/avatar/", {"access_token": self.token, "image": "not!base64!!"})
        self.assertEqual(res.status_code, 400)
