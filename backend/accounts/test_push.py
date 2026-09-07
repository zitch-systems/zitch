import json

from django.test import Client, TestCase

from .models import AccessToken, PushDevice, User


class PushRegistrationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            # Build a valid non-secret fixture at runtime so secret scanners do
            # not mistake a realistic-looking test password for a credential.
            username="08010000001", phone="08010000001", password="x" * 16)
        issued = AccessToken.issue(self.user, device_id="install-a")
        self.raw_token = issued.key

    def post(self, path, body, *, device="install-a", token=None):
        headers = {"HTTP_X_ZITCH_DEVICE": device}
        if token is not False:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token or self.raw_token}"
        return self.client.post(path, data=json.dumps(body), content_type="application/json", **headers)

    def test_an_authenticated_install_can_register(self):
        response = self.post("/api/push/register/", {
            "token": "ExpoPushToken[abc_DEF-123]", "platform": "android",
        })
        self.assertEqual(response.status_code, 200)
        device = PushDevice.objects.get()
        self.assertEqual(device.user, self.user)
        self.assertEqual(device.device_id, "install-a")

    def test_a_malformed_or_unauthenticated_token_is_refused(self):
        malformed = self.post("/api/push/register/", {
            "token": "https://attacker.test/push", "platform": "android",
        })
        unauthenticated = self.post("/api/push/register/", {
            "token": "ExpoPushToken[abc_DEF-123]", "platform": "android",
        }, token=False)
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertFalse(PushDevice.objects.exists())

    def test_logout_removes_only_this_install(self):
        PushDevice.objects.create(user=self.user, token="ExpoPushToken[first]",
                                  device_id="install-a", platform="android")
        PushDevice.objects.create(user=self.user, token="ExpoPushToken[second]",
                                  device_id="install-b", platform="ios")
        response = self.post("/api/logout/", {})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PushDevice.objects.filter(device_id="install-a").exists())
        self.assertTrue(PushDevice.objects.filter(device_id="install-b").exists())
