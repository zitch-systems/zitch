from django.urls import path

from . import views

urlpatterns = [
    path("wallet_balance/", views.wallet_balance),
    path("wallet/account/", views.wallet_account),
    path("wallet/account/create/", views.wallet_account_create),
    path("user-transaction-history/", views.transaction_history),
    path("fund/initialize/", views.fund_initialize),
    path("fund/verify/", views.fund_verify),
    path("fund/webhook/", views.fund_webhook),
    path("transfer/resolve/", views.resolve_recipient),
    path("transfer/send/", views.transfer_send),
]
