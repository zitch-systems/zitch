from django.urls import path

from . import views

urlpatterns = [
    path("banks/", views.list_banks),
    path("beneficiaries/", views.list_beneficiaries),
    path("beneficiaries/save/", views.save_beneficiary),
    path("beneficiaries/rename/", views.rename_beneficiary),
    path("beneficiaries/delete/", views.delete_beneficiary),
    path("resolve/", views.resolve_account),
    path("charge/", views.transfer_charge),
    path("send/", views.bank_transfer),
]
