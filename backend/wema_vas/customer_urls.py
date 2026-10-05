from django.urls import path

from . import customer_views

urlpatterns = [
    path("enroll/", customer_views.enroll, name="vas_enroll"),
    path("status/", customer_views.status, name="vas_status"),
]
