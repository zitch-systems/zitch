from django.urls import path

from . import views

urlpatterns = []
for fragment, view in {
    "account-lookup": views.lookup,
    "transaction-notification": views.notify,
    "mini-statement": views.statement,
    "kyc-details": views.kyc,
    "block-account": views.block,
}.items():
    # Both spellings accept the original POST, without a redirect losing it.
    urlpatterns.extend([path("vas/" + fragment, view), path("vas/" + fragment + "/", view)])
