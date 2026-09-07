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
    # The face result is keyed by its SESSION STATE alone — no shared token. This
    # URL is handed to the customer (it rides in the bank page's cb_uri, visible in
    # a WebView address bar and WhatsApp's browser), so it must not carry the secret
    # that guards the payout-authorisation callback above.
    *_both("face/<str:state>", cb.wema_face_callback, "wema_cb_face"),
    # The same view at a FIXED path, with the state in the query string instead.
    #
    # ALAT whitelist cb_uri values on their side, and a whitelist that matches the
    # exact URL cannot accept a path whose last segment is different on every
    # verification: it would admit one customer once and reject everybody after.
    # Registering a constant path gives them something stable to profile, and works
    # under prefix matching too, so we do not have to know which they do.
    #
    # The path form above is kept because it is already deployed. It costs one route
    # and means an in-flight session opened before this shipped still lands.
    *_both("face", cb.wema_face_callback, "wema_cb_face_fixed"),
]
