from django.urls import path

from admin_api import views as admin_views

from . import views

urlpatterns = [
    path("login/", views.login),
    path("logout/", views.logout),
    path("summary/", views.summary),
    path("users/", views.users),
    path("user-action/", views.user_action),
    path("kyc-queue/", views.kyc_queue),
    path("kyc-review/", views.kyc_review),
    path("transactions/", views.transactions),
    path("txn-requery/", views.txn_requery),
    path("fx/", views.fx),
    path("fx-margin/", views.fx_margin),
    path("fx-corridor/", views.fx_corridor),
    path("products/", views.products),
    path("card-action/", views.card_action),
    path("loan-remind/", views.loan_remind),
    path("run-maturities/", views.maturities_run),
    path("run-recon/", views.recon_run),
    path("inbox/", views.inbox),
    path("thread/", views.thread),
    path("conv-ai/", views.conv_ai),
    path("broadcasts/", views.broadcasts),
    path("ai/", views.ai_state),
    path("ai-global/", views.ai_global),
    path("audit/", views.audit),
    path("recon/", views.recon),
    path("settings/", views.settings_view),
    # Same operator features as /api/admin/, so an operator using the live portal is
    # not sent to a second surface to manage their own second factor or drain the
    # approval queue.
    path("approvals/", admin_views.approvals_list),
    path("approvals-decide/", admin_views.approvals_decide),
    path("mfa/", admin_views.mfa_status),
    path("mfa-enroll/", admin_views.mfa_enroll),
    path("mfa-confirm/", admin_views.mfa_confirm),
    path("mfa-disable/", admin_views.mfa_disable),
]
