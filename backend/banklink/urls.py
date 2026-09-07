from django.urls import path

from . import views

urlpatterns = [
    path("connect-init/", views.connect_init),
    path("connect/", views.connect),
    path("list/", views.list_accounts),
    path("refresh/", views.refresh),
    path("unlink/", views.unlink),
    path("fund/", views.fund),
    path("webhook/", views.webhook),
]
