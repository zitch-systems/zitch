"""Dead session rows must actually be removed — and live ones must not be.

Nothing purged these before, so both tables grew forever at roughly one row per
token per user per day. The risk in fixing that is deleting a session someone is
still using, so every test here is really about the boundary.
"""
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken, RefreshToken
from wallet.tests import make_user


def _age(model, pk, delta):
    """Back-date a row: `created`/`family_started` are set on insert."""
    field = "created" if model is AccessToken else "family_started"
    model.objects.filter(pk=pk).update(**{field: timezone.now() - delta})


class PurgeExpiredSessionsTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08077770001", "purge@zitch.test")

    # ---------------- access tokens ----------------

    def test_an_expired_app_token_is_deleted(self):
        tok = AccessToken.issue(self.user)
        _age(AccessToken, tok.pk, timedelta(days=3))
        call_command("purge_expired_sessions")
        self.assertFalse(AccessToken.objects.filter(pk=tok.pk).exists())

    def test_a_live_app_token_survives(self):
        tok = AccessToken.issue(self.user)
        call_command("purge_expired_sessions")
        self.assertTrue(AccessToken.objects.filter(pk=tok.pk).exists())

    def test_a_token_just_past_expiry_survives_the_grace_window(self):
        """Deleting at the boundary would race a request already in flight."""
        tok = AccessToken.issue(self.user)
        _age(AccessToken, tok.pk, timedelta(hours=25))   # TTL 24h, grace 6h
        call_command("purge_expired_sessions")
        self.assertTrue(AccessToken.objects.filter(pk=tok.pk).exists())

    def test_admin_tokens_use_their_own_shorter_ttl(self):
        """Sharing the app cutoff would keep 2h admin tokens for a whole day."""
        admin_tok = AccessToken.issue(self.user, scope=AccessToken.ADMIN)
        app_tok = AccessToken.issue(self.user)
        _age(AccessToken, admin_tok.pk, timedelta(hours=12))
        _age(AccessToken, app_tok.pk, timedelta(hours=12))
        call_command("purge_expired_sessions")
        self.assertFalse(AccessToken.objects.filter(pk=admin_tok.pk).exists())
        self.assertTrue(AccessToken.objects.filter(pk=app_tok.pk).exists())

    # ---------------- refresh tokens ----------------

    def test_a_family_past_the_absolute_ceiling_is_deleted(self):
        tok = RefreshToken.issue(self.user)
        _age(RefreshToken, tok.pk, timedelta(days=120))  # ceiling 90d
        call_command("purge_expired_sessions")
        self.assertFalse(RefreshToken.objects.filter(pk=tok.pk).exists())

    def test_a_spent_row_inside_the_ceiling_survives(self):
        """A spent row is what detects reuse — it is only dead once the whole
        family is refused on age, which is why the cutoff is family_started."""
        tok = RefreshToken.issue(self.user)
        RefreshToken.objects.filter(pk=tok.pk).update(used_at=timezone.now())
        _age(RefreshToken, tok.pk, timedelta(days=30))
        call_command("purge_expired_sessions")
        self.assertTrue(RefreshToken.objects.filter(pk=tok.pk).exists())

    # ---------------- operability ----------------

    def test_dry_run_deletes_nothing(self):
        tok = AccessToken.issue(self.user)
        _age(AccessToken, tok.pk, timedelta(days=3))
        call_command("purge_expired_sessions", "--dry-run")
        self.assertTrue(AccessToken.objects.filter(pk=tok.pk).exists())

    @override_settings(TOKEN_TTL_HOURS=1)
    def test_the_cutoff_follows_the_configured_ttl(self):
        tok = AccessToken.issue(self.user)
        _age(AccessToken, tok.pk, timedelta(hours=10))   # dead at TTL 1h + 6h grace
        call_command("purge_expired_sessions")
        self.assertFalse(AccessToken.objects.filter(pk=tok.pk).exists())

    def test_batching_removes_everything(self):
        """The batch loop must drain, not stop after one chunk."""
        import accounts.management.commands.purge_expired_sessions as cmd

        toks = [AccessToken.issue(self.user).pk for _ in range(7)]
        for pk in toks:
            _age(AccessToken, pk, timedelta(days=3))
        original = cmd.BATCH
        cmd.BATCH = 2
        self.addCleanup(setattr, cmd, "BATCH", original)
        call_command("purge_expired_sessions")
        self.assertEqual(AccessToken.objects.filter(pk__in=toks).count(), 0)
