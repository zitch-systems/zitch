from django.urls import path

from . import views

urlpatterns = [
    path("list/", views.list_exams),
    path("buy/", views.buy_exam),
]
