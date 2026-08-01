"""Maker/checker executors owned by the WhatsApp channel."""

from common import approvals

from .ops import create_queued_broadcast


@approvals.register("whatsapp.broadcast", capability="broadcast", always=True)
def execute_broadcast(payload, approver, approval_request=None):
    """Create the durable outbox only after a different operator approves it."""
    broadcast = create_queued_broadcast(
        payload, actor=approver, approval_request=approval_request,
    )
    return {
        "broadcast_id": broadcast.pk,
        "queued": broadcast.count_queued,
        "status": broadcast.status,
    }
