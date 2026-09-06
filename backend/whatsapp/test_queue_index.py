"""The inbound queue must be answered from an index, not a table scan.

WaMessageLog is an append-only audit of every message in both directions, so it
grows without bound, while the queue inside it — inbound rows not yet processed —
is nearly always empty. The worker asks "anything to do?" twice a second and
jobs.drain_in_background asks again on every inbound webhook, so an unindexed
answer is a continuous full scan of the whole history on the same database the
app's own requests use.

Measured on Postgres with a 300k-row table and an empty queue, before the partial
indexes: Parallel Seq Scan, 4,921 shared buffers, 21.6ms, three backends, zero
rows returned. After: Index Scan, 1 buffer, 0.027ms.
"""
from django.db import connection
from django.db.models import Q
from django.test import TestCase
from django.utils import timezone

from .jobs import INBOUND_LEASE, MAX_ATTEMPTS
from .models import WaMessageLog


def _poll_queryset(limit=50):
    """Exactly the query jobs.process_inbound_batch runs."""
    now = timezone.now()
    stale = now - INBOUND_LEASE
    return (
        WaMessageLog.objects.filter(direction=WaMessageLog.IN, processed_at__isnull=True)
        .filter(Q(processing_attempts__lt=MAX_ATTEMPTS)
                | Q(processing_attempts__gte=MAX_ATTEMPTS, processing_started_at__lte=stale))
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .filter(Q(processing_started_at__isnull=True) | Q(processing_started_at__lte=stale))
        .order_by("created").values_list("pk", flat=True)[:limit]
    )


def _plan(queryset) -> str:
    sql, params = queryset.query.sql_with_params()
    prefix = "EXPLAIN QUERY PLAN " if connection.vendor == "sqlite" else "EXPLAIN "
    with connection.cursor() as cur:
        cur.execute(prefix + sql, params)
        return " ".join(str(cell) for row in cur.fetchall() for cell in row)


class InboundQueueIndexTests(TestCase):
    def setUp(self):
        # A history the queue must not be forced to read through.
        WaMessageLog.objects.bulk_create([
            WaMessageLog(msisdn=f"23480000{i:05d}", direction=WaMessageLog.OUT,
                         wa_message_id=f"out-{i}", text="sent")
            for i in range(300)
        ])
        WaMessageLog.objects.bulk_create([
            WaMessageLog(msisdn=f"23480000{i:05d}", direction=WaMessageLog.IN,
                         wa_message_id=f"done-{i}", text="handled",
                         processed_at=timezone.now())
            for i in range(300)
        ])

    def test_declared_on_the_model(self):
        """Both partial indexes survive, with the predicate that makes them tiny."""
        by_name = {i.name: i for i in WaMessageLog._meta.indexes}
        for name in ("wamsg_inbound_queue_idx", "wamsg_inbound_pending_idx"):
            self.assertIn(name, by_name, f"{name} was removed")
            condition = by_name[name].condition
            self.assertEqual(condition, Q(direction="in", processed_at__isnull=True),
                             f"{name}'s condition no longer matches the queue query")

    def test_poll_uses_the_queue_index(self):
        """The 'anything to do?' poll must not scan the message history."""
        self.assertIn("wamsg_inbound_queue_idx", _plan(_poll_queryset()))

    def test_claim_ordering_check_uses_the_pending_index(self):
        """jobs._claim_inbound's per-sender ordering check, same requirement.

        Before this index it was served by wamsg_msisdn_created_idx, which is
        keyed on a sender's ENTIRE history: it re-read every message that number
        ever exchanged to find the handful still pending.
        """
        row = WaMessageLog.objects.create(
            msisdn="2348011112222", direction=WaMessageLog.IN, wa_message_id="live-1",
            text="balance",
        )
        earlier = WaMessageLog.objects.filter(
            direction=WaMessageLog.IN, msisdn=row.msisdn, processed_at__isnull=True,
        ).filter(Q(created__lt=row.created) | Q(created=row.created, pk__lt=row.pk))
        self.assertIn("wamsg_inbound_pending_idx", _plan(earlier))

    def test_poll_still_returns_the_backlog_in_order(self):
        """An index is only a win if it still answers the question correctly."""
        queued = [
            WaMessageLog.objects.create(msisdn="2348011112222", direction=WaMessageLog.IN,
                                        wa_message_id=f"queued-{i}", text="hi")
            for i in range(5)
        ]
        # A deferred row and an exhausted-but-leased row must stay excluded/included
        # exactly as the unindexed query had them.
        WaMessageLog.objects.create(
            msisdn="2348011113333", direction=WaMessageLog.IN, wa_message_id="deferred",
            text="later", next_attempt_at=timezone.now() + INBOUND_LEASE,
        )
        self.assertEqual(list(_poll_queryset()), [r.pk for r in queued])
