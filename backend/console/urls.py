from django.urls import path

from . import views

urlpatterns = [
    path("", views.landing),
    path("app/", views.app_prototype),
    path("portal/", views.portal),
]
