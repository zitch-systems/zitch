"""Delete session rows that can no longer authenticate anything.

Nothing in the system has ever removed these. An AccessToken is deleted only if
someone happens to PRESENT it after it expired (`AccessToken.resolve`), which
the overwhelming majority never are — the app simply refreshes and abandons the
old one. A RefreshToken row is kept on purpose when spent, because its presence
is what detects reuse, but that argument stops applying once the family is past
REFRESH_ABSOLUTE_DAYS: the family is refused on age alone by then, so the row
detects nothing.

So both tables grow forever, at roughly one row per token per user per day. Ten
thousand daily-active customers is about seven million dead rows a year across
the two, which costs index and backup weight and, in time, query latency on the
one lookup that sits in front of every authenticated request.

It is also a data-minimisation problem, not only a performance one: these are
authentication artefacts for sessions that ended months ago, and keeping them
has no purpose that survives the retention question.

Schedule daily (see render.yaml). Safe to run concurrently with traffic: it only
ever removes rows that `resolve()`/`rotate()` would already refuse, and neither
table has dependent rows.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import AccessToken, RefreshToken

#: Deleted only this far PAST expiry, never at it. The margin keeps the purge
#: away from the boundary a live request may be racing, and leaves an expired
#: token readable for a short while if someone is debugging a sign-out.
GRACE = timedelta(hours=6)

#: Rows per DELETE. Large tables should not be removed in one statement: it
#: takes a long lock and writes one enormous transaction.
BATCH = 5000


def _delete_in_batches(queryset, batch: int = BATCH) -> int:
    """Delete `queryset` in chunks, returning the number of rows removed."""
    removed = 0
    while True:
        pks = list(queryset.values_list("pk", flat=True)[:batch])
        if not pks:
            return removed
        removed += queryset.model.objects.filter(pk__in=pks).delete()[0]


class Command(BaseCommand):
    help = "Delete access/refresh tokens that are past their expiry and cannot authenticate."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be deleted without deleting it.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        dry = options["dry_run"]

        # Each scope has its own TTL, so each needs its own cutoff — sharing the
        # longer one would leave admin tokens (2h) sitting for a day.
        app_cutoff = now - timedelta(hours=settings.TOKEN_TTL_HOURS) - GRACE
        admin_cutoff = (now - timedelta(hours=getattr(settings, "ADMIN_TOKEN_TTL_HOURS", 2))
                        - GRACE)
        access = (AccessToken.objects.filter(scope=AccessToken.APP, created__lt=app_cutoff)
                  | AccessToken.objects.filter(scope=AccessToken.ADMIN,
                                               created__lt=admin_cutoff))

        # Keyed on family_started, not `created`: the absolute ceiling is what
        # ends a family, and every rotation in it carries the original start. A
        # row younger than the ceiling is still live evidence for reuse detection
        # even when it was spent months ago, so age of the ROW is the wrong test.
        family_cutoff = (now - timedelta(days=getattr(settings, "REFRESH_ABSOLUTE_DAYS", 90))
                         - GRACE)
        refresh = RefreshToken.objects.filter(family_started__lt=family_cutoff)

        if dry:
            self.stdout.write(f"Would delete {access.count()} access token(s) "
                              f"and {refresh.count()} refresh token(s)")
            return

        access_removed = _delete_in_batches(access)
        refresh_removed = _delete_in_batches(refresh)
        self.stdout.write(f"Purged {access_removed} access token(s) "
                          f"and {refresh_removed} refresh token(s)")
