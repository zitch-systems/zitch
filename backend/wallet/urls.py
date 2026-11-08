from django.urls import path

from . import views

urlpatterns = [
    path("wallet_balance/", views.wallet_balance),
    path("wallet/account/", views.wallet_account),
    path("user-transaction-history/", views.transaction_history),
    path("transaction/status/", views.transaction_status),
    path("fund/initialize/", views.fund_initialize),
    path("fund/verify/", views.fund_verify),
    path("wallet/statement/request/", views.statement_request),
    path("transfer/resolve/", views.resolve_recipient),
    path("transfer/send/", views.transfer_send),
]
