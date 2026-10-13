import json
from datetime import timedelta
from inspect import unwrap

from django.test import Client, RequestFactory, TestCase
from django.utils import timezone

from . import views
from .models import AccessToken, PushDevice, RefreshToken, User


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
        self.assertFalse(PushDevice.objects.get(device_id="install-a").enabled)
        self.assertTrue(PushDevice.objects.filter(device_id="install-b").exists())

    def test_rotated_push_token_retires_only_own_bound_installation(self):
        other = User.objects.create_user(username="push-other", phone="08010000002")
        old = PushDevice.objects.create(user=self.user, token="ExpoPushToken[old]",
                                       device_id="install-a", platform="android")
        other_customer = PushDevice.objects.create(user=other, token="ExpoPushToken[other]",
                                                  device_id="install-a", platform="android")
        other_device = PushDevice.objects.create(user=self.user, token="ExpoPushToken[tablet]",
                                                device_id="install-b", platform="ios")
        response = self.post("/api/push/register/", {
            "token": "ExpoPushToken[new]", "platform": "android"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PushDevice.objects.get(pk=old.pk).enabled)
        self.assertTrue(PushDevice.objects.filter(pk=other_customer.pk).exists())
        self.assertTrue(PushDevice.objects.filter(pk=other_device.pk).exists())

    def test_unbound_session_cannot_claim_installation_from_a_header(self):
        old = PushDevice.objects.create(user=self.user, token="ExpoPushToken[old]",
                                       device_id="install-a", platform="android")
        legacy = AccessToken.issue(self.user)
        response = self.post("/api/push/register/", {
            "token": "ExpoPushToken[legacy]", "platform": "android"}, token=legacy.key)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "device_session_required")
        self.assertTrue(PushDevice.objects.filter(pk=old.pk).exists())
        self.assertFalse(PushDevice.objects.filter(token="ExpoPushToken[legacy]").exists())

    def test_registration_authenticated_before_logout_cannot_restore_alerts(self):
        # Model a request that passed require_user, then waited while another
        # request signed out. The view must recheck the revoked session.
        request = RequestFactory().post("/api/push/register/", content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_token}", HTTP_X_ZITCH_DEVICE="install-a")
        request.user_obj = self.user
        request.data = {"token": "ExpoPushToken[late]", "platform": "android"}
        self.assertEqual(self.post("/api/logout/", {}).status_code, 200)
        response = unwrap(views.push_register)(request)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(PushDevice.objects.exists())

    def issue_at(self, user, epoch):
        return AccessToken.issue(user, device_id="install-a", session_started_at=epoch).key

    def register(self, bearer, push="ExpoPushToken[shared]"):
        return self.post("/api/push/register/", {"token": push, "platform": "android"}, token=bearer)

    def test_late_old_account_cannot_reclaim_token_in_either_arrival_order(self):
        other = User.objects.create_user(username="new-owner", phone="08010000003")
        epoch = timezone.now() - timedelta(minutes=2)
        old = self.issue_at(self.user, epoch)
        new = self.issue_at(other, epoch + timedelta(minutes=1))
        for old_arrives_first in (False, True):
            with self.subTest(old_arrives_first=old_arrives_first):
                PushDevice.objects.all().delete()
                if old_arrives_first:
                    self.assertEqual(self.register(old).status_code, 200)
                self.assertEqual(self.register(new).status_code, 200)
                response = self.register(old)
                self.assertEqual(response.status_code, 409)
                device = PushDevice.objects.get()
                self.assertEqual(device.user_id, other.pk)
                self.assertTrue(device.enabled)

    def test_new_owner_logout_keeps_watermark_against_older_active_account(self):
        other = User.objects.create_user(username="logged-out-owner", phone="08010000004")
        epoch = timezone.now() - timedelta(minutes=2)
        old = self.issue_at(self.user, epoch)
        new = self.issue_at(other, epoch + timedelta(minutes=1))
        self.assertEqual(self.register(new).status_code, 200)
        self.assertEqual(self.post("/api/logout/", {}, token=new).status_code, 200)
        self.assertEqual(self.register(old).status_code, 409)
        device = PushDevice.objects.get()
        self.assertEqual(device.user_id, other.pk)
        self.assertFalse(device.enabled)

    def test_refresh_of_old_account_does_not_supersede_newer_login(self):
        other = User.objects.create_user(username="new-login", phone="08010000005")
        epoch = timezone.now() - timedelta(minutes=2)
        refresh = RefreshToken.issue(self.user, device_id="install-a", family_started=epoch)
        new = self.issue_at(other, epoch + timedelta(minutes=1))
        self.assertEqual(self.register(new).status_code, 200)
        result = self.post("/api/token/refresh/", {"refresh_token": refresh.key}, token=False)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.register(result.json()["access_token"]).status_code, 409)
        self.assertEqual(PushDevice.objects.get().user_id, other.pk)

    def test_old_token_cannot_return_after_rotation_within_same_session(self):
        self.assertEqual(self.register(self.raw_token, "ExpoPushToken[original]").status_code, 200)
        self.assertEqual(self.register(self.raw_token, "ExpoPushToken[rotated]").status_code, 200)
        self.assertEqual(self.register(self.raw_token, "ExpoPushToken[original]").status_code, 409)
        self.assertFalse(PushDevice.objects.get(token="ExpoPushToken[original]").enabled)
        self.assertTrue(PushDevice.objects.get(token="ExpoPushToken[rotated]").enabled)

    def test_legacy_enabled_owner_can_adopt_but_old_other_account_cannot(self):
        device = PushDevice.objects.create(user=self.user, token="ExpoPushToken[shared]",
                                          device_id="install-a", platform="android")
        other = User.objects.create_user(username="legacy-other", phone="08010000006")
        old = self.issue_at(other, timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.register(old).status_code, 409)
        self.assertEqual(self.register(self.raw_token).status_code, 200)
        device.refresh_from_db()
        self.assertIsNotNone(device.session_started_at)
        self.assertEqual(device.user_id, self.user.pk)

    def test_invalid_push_payload_types_and_overlong_token_return_clean_error(self):
        for token, platform in ((123, "android"), (["token"], "ios"),
                                ("ExpoPushToken[valid]", {"platform": "ios"}),
                                ("ExpoPushToken[" + "x" * 255 + "]", "android")):
            with self.subTest(token_type=type(token).__name__, platform=platform):
                response = self.post("/api/push/register/", {"token": token, "platform": platform})
                self.assertEqual(response.status_code, 400)
        self.assertFalse(PushDevice.objects.exists())
