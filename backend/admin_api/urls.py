from django.urls import path

from . import views

urlpatterns = [
    path("login", views.login),
    path("logout", views.logout),
    path("me", views.me),
    path("bootstrap", views.bootstrap),
    # deeper reads (any staff role, like bootstrap)
    path("users/detail", views.user_detail),
    path("users/search", views.user_search),
    path("txn/search", views.txn_search),
    path("audit/search", views.audit_search),
    path("wa/broadcast_detail", views.wa_broadcast_detail),
    # write actions (server-side RBAC enforced per endpoint)
    path("settings/update", views.setting_update),
    path("users/status", views.user_status),
    path("users/pin_unlock", views.user_pin_unlock),
    path("kyc/review", views.kyc_review),
    path("txn/flag", views.txn_flag),
    path("txn/requery", views.txn_requery),
    path("txn/reversal-cases", views.reversal_cases),
    path("txn/reversal-resolution", views.reversal_resolution),
    path("txn/card-funding-cases", views.card_funding_cases),
    path("txn/card-funding-resolution", views.card_funding_resolution),
    path("txn/funding-review-cases", views.funding_review_cases),
    path("txn/funding-resolution", views.funding_resolution),
    path("fx/margin", views.fx_margin),
    path("fx/corridor", views.fx_corridor),
    path("loans/remind", views.loan_remind),
    path("ops/maturities", views.run_maturities),
    path("ops/recon", views.run_recon),
    path("wallet/credit", views.wallet_credit),
    path("cards/freeze", views.card_freeze),
    path("wa/handover", views.wa_handover),
    path("wa/conv_ai", views.wa_conv_ai),
    path("wa/reply", views.wa_reply),
    # Maker/checker queue (see common.approvals), shared by the API and live
    # operator portal. Django admin remains a guarded fallback surface.
    path("approvals/list", views.approvals_list),
    path("approvals/decide", views.approvals_decide),
    # Operator second factor.
    path("mfa/status", views.mfa_status),
    path("mfa/enroll", views.mfa_enroll),
    path("mfa/confirm", views.mfa_confirm),
    path("mfa/disable", views.mfa_disable),
    path("wa/broadcast", views.wa_broadcast),
]
