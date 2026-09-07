"""Authentication backend for the Django admin login form.

The app authenticates by username, email OR phone at ``/api/ops/login/`` and
``/api/auth/login/`` — those views resolve the identifier themselves and call
``check_password`` directly, so they never touch this file. The Django admin at
``/admin/`` is the one login surface that goes through ``authenticate()``, and
with the stock ``ModelBackend`` it matches on ``username`` alone, exact-case.

That asymmetry is a trap: an operator seeded with DJANGO_SUPERUSER_EMAIL signs
into the operator portal with their email, then gets "please enter the correct
username and password" from /admin/ for the same account and the same password.
This backend closes the gap so one credential works on every surface.

Email is NOT unique on User (it is AbstractUser's plain EmailField), so an
identifier that resolves to more than one account is refused rather than
guessed. Picking "the first match" would let a second account claiming an
existing operator's email decide who logs in.
"""
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.hashers import make_password
from django.db.models import Q


class UsernameOrEmailOrPhoneBackend(ModelBackend):
    """ModelBackend that also accepts a unique email or phone as the identifier.

    Password checking, the ``is_active`` gate and permission resolution are all
    inherited — this only widens how the typed identifier maps to a User.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = self._user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if not username or password is None:
            return None

        user = self._resolve(UserModel, username)
        if user is None:
            # Same dummy hash the stock ModelBackend runs on a miss, so the
            # response time of "no such operator" matches "wrong password" and
            # the form can't be used to enumerate valid usernames.
            make_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    def _resolve(self, UserModel, identifier):
        identifier = identifier.strip()
        if not identifier:
            return None
        # Exact username first: this is the stock lookup, so an account whose
        # username is an exact match always wins and existing logins are
        # unaffected by the widened matching below.
        exact = UserModel.objects.filter(**{UserModel.USERNAME_FIELD: identifier}).first()
        if exact is not None:
            return exact
        # Widened match. Sliced to two rows: we only need to know whether the
        # identifier is unambiguous, not how many accounts share it.
        matches = list(
            UserModel.objects.filter(
                Q(**{f"{UserModel.USERNAME_FIELD}__iexact": identifier})
                | Q(email__iexact=identifier)
                | Q(phone=identifier)
            )[:2]
        )
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _user_model():
        from django.contrib.auth import get_user_model

        return get_user_model()
