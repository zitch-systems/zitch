from django.urls import path

from . import app_identity, customer_views

urlpatterns = [
    path("enroll/", customer_views.enroll, name="vas_enroll"),
    path("status/", customer_views.status, name="vas_status"),
    path("identity/start/", app_identity.start, name="vas_identity_start"),
    path("identity/confirm/", app_identity.confirm, name="vas_identity_confirm"),
    path("identity/resend/", app_identity.resend, name="vas_identity_resend"),
]
