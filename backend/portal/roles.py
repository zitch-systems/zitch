"""Operator RBAC: map staff users to the four portal roles and their caps.

Roles ride on Django groups (`super_admin` / `finance` / `support`); a staff
user in none of them is `read_only`, and a superuser is always `super_admin`.
The caps mirror the admin design's permission matrix and are returned to the
SPA at login *and* enforced server-side on every mutating endpoint — the UI
disabling a button is never the actual gate.
"""
import functools

from common.http import fail, require_user

ROLES = ("super_admin", "finance", "support", "read_only")

CAPS = {
    "super_admin": {"wa": True, "broadcast": True, "money": True, "users": True, "ai": True, "settings": True},
    "finance":     {"wa": False, "broadcast": False, "money": True, "users": True, "ai": False, "settings": False},
    "support":     {"wa": True, "broadcast": True, "money": False, "users": False, "ai": False, "settings": False},
    "read_only":   {"wa": False, "broadcast": False, "money": False, "users": False, "ai": False, "settings": False},
}


def role_of(user) -> str:
    if user.is_superuser:
        return "super_admin"
    groups = set(user.groups.values_list("name", flat=True))
    for role in ("super_admin", "finance", "support"):
        if role in groups:
            return role
    return "read_only"


def caps_of(user) -> dict:
    return CAPS[role_of(user)]


def require_cap(cap=None):
    """Decorator (under @api): staff-gate the view; when `cap` is given, the
    caller's role must also grant that capability."""

    def deco(view):
        @functools.wraps(view)
        @require_user
        def wrapped(request, *args, **kwargs):
            user = request.user_obj
            if not user.is_staff:
                return fail("Staff access required", status=403)
            if cap and not caps_of(user).get(cap):
                return fail(f"Your role ({role_of(user)}) can't perform this action", status=403)
            request.role = role_of(user)
            return view(request, *args, **kwargs)

        return wrapped

    return deco
