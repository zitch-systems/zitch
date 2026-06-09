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
    path("kyc/review", views.kyc_review),
    path("txn/flag", views.txn_flag),
    path("cards/freeze", views.card_freeze),
    path("wa/handover", views.wa_handover),
]
