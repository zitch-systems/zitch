"""Customer-facing dispute endpoints.

Only disputes are exposed over HTTP to customers, and only for their own transactions.
AML cases are deliberately invisible to the subject — telling someone they are under
investigation is tipping off, which is an offence — and data-subject exports go through
a management command rather than a token (see that command's docstring).
"""
from common.http import api, fail, ok, require_user
from common.ratelimit import ratelimit


@api
@require_user
@ratelimit("dispute_open", limit=10, window=3600)
def open_dispute(request):
    """POST /api/disputes/open/ {reference, reason, detail?} -> the dispute.

    Rate-limited per the shared limiter: a dispute costs a human's time to work, so an
    unbounded endpoint is a way to bury the queue.
    """
    from compliance.services import open_dispute as service_open

    try:
        dispute = service_open(
            request.user_obj,
            reference=request.data.get("reference"),
            reason=(request.data.get("reason") or "").strip(),
            detail=request.data.get("detail") or "")
    except ValueError as exc:
        return fail(str(exc), status=400)
    return ok(success=True, dispute_id=dispute.pk, status=dispute.status,
              reference=dispute.reference,
              due=dispute.due.date().isoformat(),
              message=("We've logged your dispute and will come back to you by "
                       f"{dispute.due:%d %b}."))


@api
@require_user
def my_disputes(request):
    """POST /api/disputes/ -> this customer's disputes, newest first."""
    from compliance.models import Dispute

    rows = Dispute.objects.filter(user=request.user_obj)[:50]
    return ok(rows=[{
        "id": d.pk, "reference": d.reference, "reason": d.reason, "detail": d.detail,
        "status": d.status, "resolution": d.resolution,
        "created": d.created.isoformat(), "due": d.due.date().isoformat(),
    } for d in rows])


@api
@require_user
def withdraw_dispute(request):
    """POST /api/disputes/withdraw/ {dispute_id} — the customer drops their claim."""
    from compliance.models import Dispute
    from compliance.services import resolve_dispute

    dispute = Dispute.objects.filter(
        pk=request.data.get("dispute_id"), user=request.user_obj,
        status__in=(Dispute.OPEN, Dispute.INVESTIGATING)).first()
    if dispute is None:
        return fail("No open dispute with that id", status=404)
    resolve_dispute(dispute, status=Dispute.WITHDRAWN, actor=request.user_obj,
                    resolution="Withdrawn by the customer")
    return ok(success=True, status=dispute.status)
