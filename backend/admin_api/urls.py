from django.urls import path

from . import views

urlpatterns = [
    path("login", views.login),
    path("logout", views.logout),
    path("me", views.me),
    path("bootstrap", views.bootstrap),
    # write actions (server-side RBAC enforced per endpoint)
    path("settings/update", views.setting_update),
    path("users/status", views.user_status),
    path("users/pin_unlock", views.user_pin_unlock),
    path("kyc/review", views.kyc_review),
    path("txn/flag", views.txn_flag),
    path("txn/requery", views.txn_requery),
    path("fx/margin", views.fx_margin),
    path("fx/corridor", views.fx_corridor),
    path("loans/remind", views.loan_remind),
    path("ops/maturities", views.run_maturities),
    path("ops/recon", views.run_recon),
    path("cards/freeze", views.card_freeze),
    path("wa/handover", views.wa_handover),
    path("wa/conv_ai", views.wa_conv_ai),
    path("wa/reply", views.wa_reply),
    path("wa/broadcast", views.wa_broadcast),
]
