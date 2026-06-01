from django.urls import path

from . import views

urlpatterns = [
    path("rates/", views.savings_rates),
    path("quote/", views.savings_quote),
    path("create/", views.savings_create),
    path("list/", views.savings_list),
]
