"""Bank-called Wema/ALAT callback routes.

Mounted outside /api/ because everything there is app-facing and authenticated by a
user access token; these are authenticated by a path secret + source IP instead.

Both the slashless and slashed spelling of each route is registered. Wema is given
the slashless form, but Django's CommonMiddleware has APPEND_SLASH on, and a slashed
POST would be answered with a 301 that many clients re-issue as a bodyless GET — the
callback would vanish with no error anywhere. Registering both makes either land.
"""
from django.urls import path

from . import wema_callbacks as cb


def _both(fragment, view, name):
    return [path(fragment, view, name=name), path(fragment + "/", view)]


urlpatterns = [
    *_both("account/<str:token>", cb.wema_account_callback, "wema_cb_account"),
    *_both("authorize/<str:token>", cb.wema_authenticate_callback, "wema_cb_auth"),
    *_both("transaction/<str:token>", cb.wema_transaction_callback, "wema_cb_txn"),
    *_both("notification/<str:token>", cb.wema_notification_callback, "wema_cb_notify"),
    # The face-biometric result carries a per-session state as a second path
    # segment, so one customer's completed check can never stand in for another's.
    *_both("face/<str:token>/<str:state>", cb.wema_face_callback, "wema_cb_face"),
]
