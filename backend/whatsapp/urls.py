from django.urls import path

from . import views

urlpatterns = [
    path("link/start/", views.link_start),
    path("link/status/", views.link_status),
    path("link/unlink/", views.link_unlink),
]
