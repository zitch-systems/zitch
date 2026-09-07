"""Maker/checker: hold a high-risk operator action for a second operator.

Registered actions declare an executor. The requesting endpoint calls `submit()`
instead of doing the work; a *different* operator calls `decide()`, and only then does
the original executor run — through exactly the path it would have taken, which is
what stops this becoming a second, less-tested way to move money.

Actions can opt into unconditional approval (broadcasts do); the global
`OPS_REQUIRE_DUAL_APPROVAL` flag enables it for the remaining registered actions.
"""
import logging

from django.conf import settings
from django.utils import timezone

log = logging.getLogger("zitch.security")

# action name -> callable(payload, approver) -> dict result
_EXECUTORS = {}
_CAPABILITIES = {}
_ALWAYS_REQUIRED = set()


class ApprovalError(Exception):
    """A decision that cannot be honoured (already decided, self-approval, …)."""


def register(action, *, capability=None, always=False):
    """Decorator registering the executor for `action`."""
    def deco(fn):
        _EXECUTORS[action] = fn
        _CAPABILITIES[action] = capability
        if always:
            _ALWAYS_REQUIRED.add(action)
        return fn
    return deco


def required_for(action: str) -> bool:
    """Whether `action` must be held for a second operator."""
    return action in _EXECUTORS and (
        action in _ALWAYS_REQUIRED
        or getattr(settings, "OPS_REQUIRE_DUAL_APPROVAL", False)
    )


def capability_for(action: str) -> str | None:
    """Server-side RBAC capability required to view or decide an action."""
    return _CAPABILITIES.get(action)


def submit(action: str, *, payload: dict, requested_by, reason: str = ""):
    """Record a pending request. Returns the ApprovalRequest."""
    from whatsapp.models import ApprovalRequest
    from whatsapp.ops import record_audit

    if action not in _EXECUTORS:
        raise ApprovalError(f"{action} has no registered executor")
    req = ApprovalRequest.objects.create(
        action=action, payload=payload or {}, reason=reason[:200],
        requested_by=requested_by,
    )
    record_audit("approval.requested", actor=requested_by, target=f"req_{req.pk}",
                 after={"action": action, "payload": _safe(payload), "reason": req.reason})
    log.info("approval_requested id=%s action=%s by=%s", req.pk, action, requested_by.pk)
    return req


def decide(req, *, approver, approve: bool, note: str = ""):
    """Approve or reject `req`, executing it on approval. Returns the request.

    Two refusals are load-bearing:

    * **Self-approval.** A maker/checker control that lets the maker check their own
      work is not a control at all — it is an extra click. This is the entire point of
      the mechanism, so it is enforced here rather than in a view where one endpoint
      could forget.
    * **A non-pending request.** Deciding twice would run the executor twice, which for
      a wallet credit means crediting twice.

    Both raise rather than returning falsy, so a caller cannot ignore them by accident.
    """
    from django.db import transaction as db_transaction

    from whatsapp.models import ApprovalRequest
    from whatsapp.ops import record_audit

    with db_transaction.atomic():
        # Re-read under a row lock: two approvers clicking at once would otherwise both
        # see `pending` and both execute.
        locked = ApprovalRequest.objects.select_for_update().get(pk=req.pk)
        if not locked.is_pending:
            raise ApprovalError(f"This request was already {locked.status}")
        if locked.requested_by_id == getattr(approver, "id", None):
            raise ApprovalError("You cannot approve your own request — a second operator must.")

        locked.decided_by = approver
        locked.decided = timezone.now()
        locked.decision_note = (note or "")[:200]
        if not approve:
            locked.status = ApprovalRequest.REJECTED
            locked.save(update_fields=["status", "decided_by", "decided", "decision_note"])
            record_audit("approval.rejected", actor=approver, target=f"req_{locked.pk}",
                         after={"action": locked.action, "note": locked.decision_note})
            return locked
        locked.status = ApprovalRequest.APPROVED
        locked.save(update_fields=["status", "decided_by", "decided", "decision_note"])

        # Execute under the same database transaction as the decision. A process
        # crash therefore rolls back BOTH the decision and executor writes instead
        # of leaving an approved-but-never-executed request. The nested savepoint
        # lets us persist an ordinary executor refusal as FAILED.
        try:
            with db_transaction.atomic():
                result = _EXECUTORS[locked.action](locked.payload, approver, locked) or {}
            locked.status = ApprovalRequest.EXECUTED
            locked.result = _safe(result)
        except Exception as exc:  # noqa: BLE001 — record a terminal failure
            log.exception("approval_execute_failed id=%s action=%s", locked.pk, locked.action)
            locked.status = ApprovalRequest.FAILED
            locked.result = {"error": str(exc)[:300]}
        locked.save(update_fields=["status", "result"])
        record_audit(f"approval.{locked.status}", actor=approver, target=f"req_{locked.pk}",
                     after={"action": locked.action, "result": locked.result})
        return locked


def _safe(value):
    """JSON-safe copy for audit/result columns, with the same key redaction the webhook
    log uses — an approval payload can carry an operator-typed identifier."""
    from whatsapp.models import WebhookEvent

    if not isinstance(value, (dict, list)):
        return {}
    return WebhookEvent.redact(_jsonable(value))


def _jsonable(value):
    from decimal import Decimal

    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    return value
