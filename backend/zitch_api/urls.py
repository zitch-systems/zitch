from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(_request):
    """Liveness probe + which integrations are live (True) vs MOCK (False).

    Reports booleans only — never secrets — so ops can confirm prod keys are
    wired without exposing them.
    """
    from utility.providers import _baxi_live, _prembly_live, payments_live

    integrations = {
        "payments_monnify": payments_live(),
        "vtu_baxi": _baxi_live(),
        "sms_sendchamp": bool(settings.SENDCHAMP["API_KEY"]),
        "kyc_prembly": _prembly_live(),
        "cards_issuer": bool(settings.CARD_ISSUER["API_KEY"]),
    }
    return JsonResponse({"status": True, "service": "zitch-api", "integrations": integrations})


urlpatterns = [
    path("", health),
    path("admin/", admin.site.urls),
    path("api/", include("accounts.urls")),
    path("api/", include("wallet.urls")),
    path("api/utility/", include("utility.urls")),
    path("api/exams/", include("exams.urls")),
    path("api/loans/", include("loans.urls")),
    path("api/savings/", include("savings.urls")),
    path("api/betting/", include("betting.urls")),
    path("api/transfers/", include("transfers.urls")),
    path("api/cards/", include("cards.urls")),
]
