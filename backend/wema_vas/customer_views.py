"""Authenticated enrollment: the bank bearer token cannot call this surface."""
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import DatabaseError
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.csrf import csrf_exempt

from common.http import api, fail, ok, require_user
from common.ratelimit import ratelimit
from wallet.services import customer_funding_account

from .config import config
from .enrollment import enroll_verified
from .views import _secure


def _private(response):
    response["Cache-Control"] = "no-store"
    return response


@sensitive_post_parameters("bvn", "nin")
@api
@require_user
@ratelimit("vas_enroll", limit=5, window=600)
def enroll(request):
    if config().get("REQUIRE_HTTPS", True) and not _secure(request, config()):
        return _private(fail("HTTPS required", status=403))
    data = request.data
    try:
        enroll_verified(request.user_obj, bvn=data.get("bvn", ""), nin=data.get("nin", ""), consent=data.get("consent", False))
    except ValidationError as exc:
        return _private(fail(" ".join(exc.messages), status=409, code="vas_enrollment_pending"))
    except (DatabaseError, ImproperlyConfigured):
        return _private(fail("New funding accounts are temporarily unavailable. Please try again later.", status=503))
    return _private(ok(success=True, **customer_funding_account(request.user_obj)))


@csrf_exempt
@require_user
def status(request):
    if request.method not in ("GET", "POST"):
        return _private(fail("Method not allowed", status=405))
    return _private(ok(success=True, **customer_funding_account(request.user_obj)))
