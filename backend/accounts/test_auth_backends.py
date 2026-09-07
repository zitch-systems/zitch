"""The Django admin login accepts username, email or phone — and refuses an
identifier that maps to more than one account."""
from django.contrib.auth import authenticate, get_user_model
from django.test import Client, TestCase

User = get_user_model()

PASSWORD = "Adm1n#Pass99"


def make_operator(username, email="", phone=None, password=PASSWORD, **kw):
    user = User.objects.create(username=username, email=email, phone=phone,
                               is_staff=True, is_superuser=True, **kw)
    user.set_password(password)
    user.save()
    return user


class AdminLoginIdentifierTests(TestCase):
    def test_username_authenticates(self):
        make_operator("admin", "admin@zitch.ng")
        self.assertIsNotNone(authenticate(username="admin", password=PASSWORD))

    def test_email_authenticates(self):
        """The seeded admin's email is the credential an operator is most likely
        to try first, because DJANGO_SUPERUSER_EMAIL is what they set."""
        make_operator("admin", "owner@zitch.ng")
        user = authenticate(username="owner@zitch.ng", password=PASSWORD)
        self.assertIsNotNone(user)
        self.assertEqual(user.username, "admin")

    def test_email_match_is_case_insensitive(self):
        make_operator("admin", "Owner@Zitch.NG")
        self.assertIsNotNone(authenticate(username="owner@zitch.ng", password=PASSWORD))

    def test_phone_authenticates(self):
        make_operator("admin", "admin@zitch.ng", phone="08010000009")
        self.assertIsNotNone(authenticate(username="08010000009", password=PASSWORD))

    def test_surrounding_whitespace_is_tolerated(self):
        make_operator("admin", "owner@zitch.ng")
        self.assertIsNotNone(authenticate(username="  owner@zitch.ng  ", password=PASSWORD))

    def test_wrong_password_is_refused(self):
        make_operator("admin", "owner@zitch.ng")
        self.assertIsNone(authenticate(username="owner@zitch.ng", password="wrong-one"))

    def test_unknown_identifier_is_refused(self):
        make_operator("admin", "owner@zitch.ng")
        self.assertIsNone(authenticate(username="nobody@zitch.ng", password=PASSWORD))

    def test_inactive_account_is_refused(self):
        make_operator("admin", "owner@zitch.ng", is_active=False)
        self.assertIsNone(authenticate(username="owner@zitch.ng", password=PASSWORD))

    def test_ambiguous_email_is_refused_not_guessed(self):
        """email is not unique on User. Two accounts sharing one address must not
        let whichever row sorts first decide who is logged in — a customer who
        signs up with an operator's address would otherwise change who /admin/
        authenticates."""
        make_operator("admin", "shared@zitch.ng")
        second = User.objects.create(username="impostor", email="shared@zitch.ng",
                                     phone="08010000008")
        second.set_password(PASSWORD)
        second.save()
        self.assertIsNone(authenticate(username="shared@zitch.ng", password=PASSWORD))
        # The unambiguous identifier still works.
        self.assertIsNotNone(authenticate(username="admin", password=PASSWORD))

    def test_exact_username_wins_over_another_accounts_email(self):
        """A username is the canonical identifier: if one account is named "ada"
        and another carries the email "ada", the named account authenticates."""
        make_operator("ada", "ada@zitch.ng")
        other = User.objects.create(username="second", email="ada", phone="08010000007")
        other.set_password(PASSWORD)
        other.save()
        user = authenticate(username="ada", password=PASSWORD)
        self.assertIsNotNone(user)
        self.assertEqual(user.username, "ada")

    def test_blank_identifier_or_password_is_refused(self):
        make_operator("admin", "owner@zitch.ng")
        self.assertIsNone(authenticate(username="", password=PASSWORD))
        self.assertIsNone(authenticate(username="   ", password=PASSWORD))
        self.assertIsNone(authenticate(username="admin", password=None))


class AdminLoginFormTests(TestCase):
    """End-to-end through the real /admin/ login form, not just authenticate()."""

    def _login(self, identifier):
        return Client().post("/admin/login/",
                             {"username": identifier, "password": PASSWORD,
                              "next": "/admin/"})

    def test_admin_form_accepts_username_and_email(self):
        make_operator("admin", "owner@zitch.ng")
        for identifier in ("admin", "owner@zitch.ng"):
            res = self._login(identifier)
            self.assertEqual(res.status_code, 302, f"{identifier} should sign in")
            self.assertEqual(res["Location"], "/admin/")

    def test_admin_form_rejects_a_non_staff_account(self):
        user = User.objects.create(username="customer", email="c@zitch.ng",
                                   phone="08010000006")
        user.set_password(PASSWORD)
        user.save()
        # Correct password, but /admin/ is staff-only: the form re-renders (200)
        # rather than redirecting into the admin.
        self.assertEqual(self._login("customer").status_code, 200)
