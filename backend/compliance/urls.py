from django.urls import path

from . import views

urlpatterns = [
    path("", views.my_disputes),
    path("open/", views.open_dispute),
    path("withdraw/", views.withdraw_dispute),
]
