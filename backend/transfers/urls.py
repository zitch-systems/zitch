from django.urls import path

from . import views

urlpatterns = [
    path("banks/", views.list_banks),
    path("beneficiaries/", views.list_beneficiaries),
    path("resolve/", views.resolve_account),
    path("send/", views.bank_transfer),
    path("webhook/", views.disbursement_webhook),
    path("kora-webhook/", views.kora_disbursement_webhook),
]
