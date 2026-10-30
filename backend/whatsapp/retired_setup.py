"""Reject retired setup sessions without interpreting their private input.

These markers exist only to drain historical jobs safely. No new action or
provider request uses this contract. `add_account` is the retired partner-bank
in-chat account opening; new accounts open through the 9PSB verification page.
"""
from django.db.models import Q

from .models import PendingAction


RETIRED_SETUP_MESSAGE = (
    "That account setup is no longer available. "
    "Reply 6 to add money or 8 to verify your identity."
)


def is_retired_setup(action):
    payload = action.payload if isinstance(action.payload, dict) else {}
    return action.action_type in ("vas_enroll", "add_account") or any(
        payload.get(key) is True for key in ("vas_identity", "vas_contacts", "vas_reregister")
    )


def retired_setup_pending(msisdn):
    return PendingAction.objects.filter(msisdn=msisdn).filter(
        Q(action_type__in=("vas_enroll", "add_account")) | Q(payload__vas_identity=True)
        | Q(payload__vas_contacts=True) | Q(payload__vas_reregister=True)
    ).exists()
