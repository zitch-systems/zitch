from django.urls import path

from . import views

urlpatterns = [
    path("list/", views.list_cards),
    path("create/", views.create_card),
    path("freeze/", views.toggle_freeze),
]
