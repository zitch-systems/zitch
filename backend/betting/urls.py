from django.urls import path

from . import views

urlpatterns = [
    path("list/", views.list_platforms),
    path("fund/", views.fund_betting),
]
