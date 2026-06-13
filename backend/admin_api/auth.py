"""Staff authentication, server-side RBAC, and the audit helper for the
operator portal (`/portal/`, backed by `/api/admin/`).

The portal reuses the app's opaque `AccessToken`, but every staff endpoint
additionally requires ``user.is_staff`` and gates writes behind a role matrix
that is enforced HERE, on the server — the topbar "view as" switcher in the
prototype is presentation only and must never be trusted for authorization.

Role resolution:
  super_admin — Django superuser.
  finance / support / read_only — membership of the like-named Django Group.
  default — read_only (least privilege) for any other staff user.
"""
import functools
import json

from django.views.decorators.csrf import csrf_exempt

from common.http import fail, resolve_token

ROLE_SUPER = "super_admin"
ROLE_FINANCE = "finance"
ROLE_SUPPORT = "support"
ROLE_READONLY = "read_only"
ROLES = [ROLE_SUPER, ROLE_FINANCE, ROLE_SUPPORT, ROLE_READONLY]

# Server-authoritative capability matrix (mirrors the portal's CAN map). Each
# write endpoint declares the capability it needs; this is the real gate.
CAN = {
    ROLE_SUPER: {"wa", "broadcast", "money", "users", "ai", "settings"},
    ROLE_FINANCE: {"money", "users"},
    ROLE_SUPPORT: {"wa", "broadcast"},
    ROLE_READONLY: set(),
}


def staff_role(user) -> str:
    if getattr(user, "is_superuser", False):
        return ROLE_SUPER
    names = set(user.groups.values_list("name", flat=True))
    for role in (ROLE_FINANCE, ROLE_SUPPORT, ROLE_READONLY):
        if role in names:
            return role
    return ROLE_READONLY


def can(role: str, capability: str) -> bool:
    return capability in CAN.get(role, set())


def staff_endpoint(*, methods=("GET", "POST"), perm=None):
    """Decorator for portal endpoints.

    - restricts HTTP methods (reads use GET, writes use POST);
    - parses a JSON body into ``request.data`` for POST;
    - resolves the bearer token to a staff user (401 otherwise) and injects
      ``request.staff`` + ``request.role``;
    - enforces ``perm`` against the role matrix (403 otherwise).
    """

    def decorator(view):
        @csrf_exempt
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            from accounts.models import AccessToken

            if request.method not in methods:
                return fail("Method not allowed", status=405)
            if request.method == "POST":
                try:
                    request.data = json.loads(request.body or b"{}")
                except (ValueError, TypeError):
                    return fail("Invalid JSON body", status=400)
                if not isinstance(request.data, dict):
                    return fail("Invalid request body", status=400)
            else:
                request.data = {}

            user = AccessToken.resolve(resolve_token(request))
            if user is None or not user.is_staff:
                return fail("Staff authentication required", status=401)
            request.staff = user
            request.role = staff_role(user)
            if perm and not can(request.role, perm):
                return fail("Insufficient privileges for this action", status=403, code="forbidden")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def audit(request, action: str, target="", before=None, after=None) -> None:
    """Append an admin action to the immutable AuditLog (hard-rule #10)."""
    from whatsapp.models import AuditLog

    AuditLog.objects.create(
        actor_type="admin",
        actor_id=(request.staff.email or request.staff.username or str(request.staff.id)),
        action=action,
        target=str(target),
        before=before or {},
        after=after or {},
    )
