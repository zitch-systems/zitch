from django.urls import path

from . import views

# Paths mirror the exact endpoints the Expo app calls.
urlpatterns = [
    path("sigin/", views.signin),
    path("logout/", views.logout),
    path("phone_verification/", views.phone_verification),
    path("verify_otp/", views.verify_otp),
    path("resend_verify_otp/", views.resend_verify_otp),
    path("password/forgot/", views.password_forgot),
    path("password/reset/", views.password_reset),
    path("set-password/", views.set_password),
    path("set-transaction-pin/", views.set_transaction_pin),
    path("update_info/", views.update_info),
    path("profile/avatar/", views.avatar_upload),
    path("kyc/status/", views.kyc_status),
    path("kyc/bvn/", views.kyc_bvn),
    path("kyc/nin/", views.kyc_nin),
    path("kyc/face/", views.kyc_face),
]
