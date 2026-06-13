from django.urls import path

from . import views

# Mounted under /api/utility/ — paths match the Expo app exactly.
urlpatterns = [
    path("buyairtime/", views.buyairtime),
    path("get_data_plans/", views.get_data_plans),
    path("get_data_plans_price/", views.get_data_plans_price),
    path("buydata/", views.buydata),
    path("get_cable_plans/", views.get_cable_plans),
    path("get_cable_plans_price/", views.get_cable_plans_price),
    path("validate_iuc/", views.validate_iuc),
    path("buycable/", views.buycable),
    path("validate_meter/", views.validate_meter),
    path("buyelectricity/", views.buyelectricity),
]
