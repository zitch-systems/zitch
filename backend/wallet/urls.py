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
    # TEST-ONLY: credit a mock deposit (WEMA_SIMULATION + SIMULATE_DEPOSIT_TOKEN only).
    path("dev/simulate-deposit/", views.simulate_deposit),
    # Fund from the user's own ALAT account (Pay with Bank Account — direct debit).
    path("wallet/alat/fund/", views.alat_fund_initiate),
    path("wallet/alat/fund/verify/", views.alat_fund_verify),
    # Wema NUBAN bank statement (transhistoryV2).
    path("wallet/statement/", views.wema_statement),
    path("transfer/resolve/", views.resolve_recipient),
    path("transfer/send/", views.transfer_send),
]
