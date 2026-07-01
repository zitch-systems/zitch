from django.urls import path

from . import views

urlpatterns = [
    path("wallet_balance/", views.wallet_balance),
    path("wallet/account/", views.wallet_account),
    path("wallet/account/create/", views.wallet_account_create),
    # Wema/ALAT wallet provisioning — OTP round-trip (create -> verify -> resend).
    path("wallet/wema/create/", views.wema_wallet_create),
    path("wallet/wema/verify-otp/", views.wema_wallet_verify_otp),
    path("wallet/wema/resend-otp/", views.wema_wallet_resend_otp),
    path("user-transaction-history/", views.transaction_history),
    path("fund/initialize/", views.fund_initialize),
    path("fund/verify/", views.fund_verify),
    path("fund/webhook/", views.fund_webhook),
    path("fund/monnify/webhook/", views.monnify_fund_webhook),
    path("transfer/resolve/", views.resolve_recipient),
    path("transfer/send/", views.transfer_send),
]
