from django.urls import path

from . import views

urlpatterns = [
    path("wallet_balance/", views.wallet_balance),
    path("user-transaction-history/", views.transaction_history),
]
