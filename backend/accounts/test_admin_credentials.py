"""Credential audit routes cannot manufacture or reopen authentication proof."""

from django.contrib import admin
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import AccessToken, OTP, OperatorTotp, User


@override_settings(OPS_REQUIRE_MFA=False, SECURE_SSL_REDIRECT=False)
class CredentialAdminBoundaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.operator = User.objects.create_superuser(
            username="credential-auditor", email="auditor@example.test", password="TestOnlyPass123!",
        )
        cls.customer = User.objects.create_user(
            username="credential-customer", phone="08012345678", email="customer@example.test",
        )
        issued = AccessToken.issue(cls.customer, device_id="test-device")
        cls.raw_key = issued.key
        cls.token = AccessToken.objects.get(pk=issued.pk)
        cls.challenge = OTP.issue(cls.customer.phone, "123456", email=cls.customer.email, purpose=OTP.RESET)
        cls.challenge.used = True
        cls.challenge.attempts = OTP.MAX_ATTEMPTS
        cls.challenge.save(update_fields=["used", "attempts"])

    def setUp(self):
        # Deliberately pass the site-level gate. A login/MFA redirect must not
        # masquerade as evidence that these model forms deny credential writes.
        self.assertFalse(OperatorTotp.objects.filter(user=self.operator, confirmed=True).exists())
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def route(self, model, action, obj=None):
        return reverse(f"admin:accounts_{model._meta.model_name}_{action}", args=[obj.pk] if obj else None)

    def cases(self):
        return (
            (AccessToken, self.token, {
                "key": AccessToken._hash("chosen-forged-bearer"), "user": self.operator.pk,
                "scope": AccessToken.ADMIN, "device_id": "",
            }),
            (OTP, self.challenge, {
                "phone": "08087654321", "email": "target@example.test", "code_hash": OTP.hash_code("654321"),
                "purpose": OTP.SIGNUP, "used": "", "attempts": "0",
            }),
        )

    def test_views_keep_forensic_metadata_without_credentials_or_editable_fields(self):
        for model, obj, _payload in self.cases():
            with self.subTest(model=model.__name__):
                response = self.client.get(self.route(model, "change", obj))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(dict(response.context["adminform"].form.fields), {})
                self.assertNotContains(response, self.raw_key)
                self.assertNotContains(response, self.token.key)
                self.assertNotContains(response, self.challenge.code_hash)
                self.assertContains(response, obj.pk)
                if model is AccessToken:
                    self.assertContains(response, "test-device")
                    self.assertContains(response, self.token.key[:6])
                else:
                    self.assertContains(response, self.customer.phone)
                    self.assertContains(response, self.customer.email)
                listing = self.client.get(self.route(model, "changelist"))
                self.assertEqual(listing.status_code, 200)
                self.assertNotContains(listing, self.raw_key)
                self.assertNotContains(listing, self.token.key)
                self.assertNotContains(listing, self.challenge.code_hash)

    def test_direct_add_posts_cannot_issue_tokens_or_challenges(self):
        for model, _obj, payload in self.cases():
            with self.subTest(model=model.__name__):
                before = list(model.objects.order_by("pk").values())
                response = self.client.post(self.route(model, "add"), {**payload, "_save": "Save"})
                self.assertEqual(response.status_code, 403)
                self.assertEqual(list(model.objects.order_by("pk").values()), before)

    def test_direct_change_posts_cannot_reassign_tokens_or_reopen_challenges(self):
        for model, obj, payload in self.cases():
            with self.subTest(model=model.__name__):
                before = model.objects.values().get(pk=obj.pk)
                response = self.client.post(self.route(model, "change", obj), {**payload, "_save": "Save"})
                self.assertEqual(response.status_code, 403)
                self.assertEqual(model.objects.values().get(pk=obj.pk), before)

    def test_direct_delete_posts_cannot_remove_credential_evidence(self):
        for model, obj, _payload in self.cases():
            with self.subTest(model=model.__name__):
                before = model.objects.values().get(pk=obj.pk)
                response = self.client.post(self.route(model, "delete", obj), {"post": "yes"})
                self.assertEqual(response.status_code, 403)
                self.assertEqual(model.objects.values().get(pk=obj.pk), before)

    def test_forged_bulk_delete_is_not_an_available_action(self):
        for model, obj, _payload in self.cases():
            with self.subTest(model=model.__name__):
                before = model.objects.values().get(pk=obj.pk)
                response = self.client.post(self.route(model, "changelist"), {
                    "action": "delete_selected", "_selected_action": [obj.pk], "index": "0", "post": "yes",
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(admin.site._registry[model].get_actions(response.wsgi_request), {})
                self.assertEqual(model.objects.values().get(pk=obj.pk), before)

    def test_issuer_and_verifier_services_still_work_outside_generic_admin_forms(self):
        issued = AccessToken.issue(self.customer, device_id="new-test-device")
        self.assertEqual(AccessToken.resolve(issued.key, required_scope=AccessToken.APP, device_id="new-test-device"), self.customer)
        challenge = OTP.issue(self.customer.phone, "654321", email=self.customer.email, purpose=OTP.RESET)
        self.assertTrue(challenge.verify_code("654321"))
        AccessToken.objects.filter(pk=issued.pk).delete()
        self.assertIsNone(AccessToken.resolve(issued.key, required_scope=AccessToken.APP, device_id="new-test-device"))
