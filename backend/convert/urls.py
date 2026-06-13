from django.urls import path

from . import views

urlpatterns = [
    path("rates/", views.rates),
    path("fx/", views.fx_rates),
    path("airtime/", views.convert_airtime),
]
