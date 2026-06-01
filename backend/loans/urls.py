from django.urls import path

from . import views

urlpatterns = [
    path("status/", views.loan_status),
    path("quote/", views.loan_quote),
    path("request/", views.loan_request),
    path("repay/", views.loan_repay),
]
