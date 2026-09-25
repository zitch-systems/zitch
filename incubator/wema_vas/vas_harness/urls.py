from django.urls import path

from . import views

urlpatterns = [path("healthz", views.health)]
for fragment, view in {
    "account-lookup": views.lookup,
    "transaction-notification": views.notify,
    "mini-statement": views.statement,
    "kyc-details": views.kyc,
    "block-account": views.block,
}.items():
    # Both spellings accept POST directly, without a body-losing redirect.
    urlpatterns.extend([path("vas/" + fragment, view), path("vas/" + fragment + "/", view)])
