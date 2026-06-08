from django.urls import path

from . import views

urlpatterns = [
    path("rates/", views.rates),
    path("airtime/", views.convert_airtime),
]
