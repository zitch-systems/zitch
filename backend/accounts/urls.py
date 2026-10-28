from django.urls import path

from . import views

urlpatterns = [
    path("sigin/", views.signin),
    path("logout/", views.logout),
    path("token/refresh/", views.token_refresh),
    path("push/register/", views.push_register),
    path("phone_verification/", views.phone_verification),
    path("verify_otp/", views.verify_otp),
    path("resend_verify_otp/", views.resend_verify_otp),
    path("password/forgot/", views.password_forgot),
    path("password/reset/", views.password_reset),
    path("set-password/", views.set_password),
    path("set-transaction-pin/", views.set_transaction_pin),
    path("verify-transaction-pin/", views.verify_pin),
    path("update_info/", views.update_info),
    path("profile/avatar/", views.avatar_upload),
    path("email/verify/start/", views.email_verify_start),
    path("email/verify/confirm/", views.email_verify_confirm),
    path("kyc/status/", views.kyc_status),
    path("limits/", views.transaction_limits),
    path("limits/transaction/", views.set_transaction_limit),
]
