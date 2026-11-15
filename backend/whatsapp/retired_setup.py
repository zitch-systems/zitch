"""Reject retired setup sessions without interpreting their private input.

These markers exist only to drain historical jobs safely. No new action or
provider request uses this contract.
"""
from django.db.models import Q

from .models import PendingAction


RETIRED_SETUP_MESSAGE = (
    "That account setup is no longer available. "
    "Reply 5 to add money or 7 to verify your identity."
)


def is_retired_setup(action):
    payload = action.payload if isinstance(action.payload, dict) else {}
    return action.action_type == "vas_enroll" or any(
        payload.get(key) is True for key in ("vas_identity", "vas_contacts", "vas_reregister")
    )


def retired_setup_pending(msisdn):
    return PendingAction.objects.filter(msisdn=msisdn).filter(
        Q(action_type="vas_enroll") | Q(payload__vas_identity=True)
        | Q(payload__vas_contacts=True) | Q(payload__vas_reregister=True)
    ).exists()
