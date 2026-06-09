from django.urls import path

from . import views

urlpatterns = [
    path("link/start/", views.link_start),
    path("link/status/", views.link_status),
    path("link/unlink/", views.link_unlink),
    # Operator (staff) endpoints
    path("ops/handover/", views.ops_handover),
    path("ops/return-to-bot/", views.ops_return_to_bot),
    path("ops/reply/", views.ops_reply),
    path("ops/broadcast/", views.ops_broadcast),
]
