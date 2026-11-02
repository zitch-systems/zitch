from django.urls import path
from . import views

urlpatterns = [
    path("status/", views.status),
    path("identity/start/", views.identity_start),
    path("identity/confirm/", views.identity_confirm),
    path("identity/resend/", views.identity_resend),
    path("identity/status/", views.identity_status),
    path("upgrade/", views.upgrade),
    path("upgrade/document/", views.upgrade_document),
    path("upgrade/documents/", views.upgrade_documents),
    path("liveness/start/", views.liveness_start),
    path("address/start/", views.address_start),
    path("address/status/", views.address_status),
    path("catalog/", views.catalog),
    path("bills/validate/", views.bill_validate),
    path("purchase/", views.purchase),
]
