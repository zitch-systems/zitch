"""Operator-only bank queries. No customer credentials or raw bank rows leave here."""
import json

from django import forms
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from common.ratelimit import ratelimit
from .contracts import InvalidPayload
from .transaction_query import QueryUnavailable, query_and_reconcile, unique_keys


@csrf_exempt
@never_cache
@require_http_methods(["POST"])
@ratelimit("vas_operator_query", 10, 60)
def query_diagnose(request):
    from zitch_api.urls import _diag_denied
    denied = _diag_denied(request, "DIAG_TOKEN", "WEMA_DIAG_TOKEN")
    if denied:
        return denied
    if request.content_type != "application/json":
        return JsonResponse({"detail": "application/json required"}, status=415)
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > 2048 or len(request.body) > 2048:
            return JsonResponse({"detail": "request too large"}, status=413)
        body = json.loads(request.body, object_pairs_hook=unique_keys)
        if not isinstance(body, dict) or set(body) not in ({"rail", "sessionid"}, {"rail", "craccount"}):
            raise InvalidPayload("Invalid query fields")
        if not isinstance(body.get("rail"), str):
            raise InvalidPayload("Select nip or etranzact")
        if not isinstance(body.get("sessionid", body.get("craccount")), str):
            raise InvalidPayload("Query value must be text")
        report = query_and_reconcile(body["rail"], session_id=body.get("sessionid", ""), account=body.get("craccount", ""))
    except (InvalidPayload, ValueError, UnicodeError, RecursionError):
        return JsonResponse({"detail": "Select a rail and supply exactly one valid sessionid or craccount."}, status=400)
    except QueryUnavailable as exc:
        return JsonResponse({"detail": str(exc), "read_only": True}, status=503)
    return JsonResponse({"transaction_query": report})


class TransactionQueryForm(forms.Form):
    rail = forms.ChoiceField(choices=(("nip", "NIP"), ("etranzact", "Etranzact")))
    query_type = forms.ChoiceField(choices=(("session", "Session ID"), ("account", "Virtual account number")))
    value = forms.CharField(label="Session ID or virtual account number", max_length=128)


@never_cache
@require_http_methods(["GET", "POST"])
@ratelimit("vas_operator_query_page", 10, 60)
def query_page(request):
    # admin.site.admin_view supplies staff-session auth and CSRF protection.
    # Require financial-evidence permission as well as staff membership.
    if not request.user.has_perm("wema_vas.view_receipt"):
        return HttpResponseForbidden("Transaction query permission required.")
    form = TransactionQueryForm(request.POST or None)
    report, error = "", ""
    if request.method == "POST" and form.is_valid():
        values = form.cleaned_data
        try:
            result = query_and_reconcile(values["rail"],
                session_id=values["value"] if values["query_type"] == "session" else "",
                account=values["value"] if values["query_type"] == "account" else "")
            report = json.dumps(result, indent=2, sort_keys=True)
        except (InvalidPayload, QueryUnavailable) as exc:
            error = str(exc)
    return render(request, "wema_vas/transaction_query.html", {"form": form, "report": report, "error": error})
