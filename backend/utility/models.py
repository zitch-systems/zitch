from django.db import models

# Network / provider ids match the values the Expo app sends.
NETWORKS = [("1", "MTN"), ("2", "GLO"), ("3", "Airtel"), ("4", "9mobile")]
PLAN_TYPES = [("1", "SME"), ("2", "SME2"), ("3", "Gifting"), ("4", "Corporate")]
CABLE_PROVIDERS = [("1", "GoTV"), ("2", "DSTV"), ("3", "StarTimes")]


class DataPlan(models.Model):
    network = models.CharField(max_length=2, choices=NETWORKS)
    plan_type = models.CharField(max_length=2, choices=PLAN_TYPES)
    name = models.CharField(max_length=60)          # e.g. "1.5GB"
    validity = models.CharField(max_length=40)       # e.g. "30 days"
    plan_code = models.CharField(max_length=40, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.get_network_display()} {self.name} ({self.validity})"


class CablePlan(models.Model):
    provider = models.CharField(max_length=2, choices=CABLE_PROVIDERS)
    name = models.CharField(max_length=80)
    validity = models.CharField(max_length=40, blank=True, default="30 days")
    cable_plan_code = models.CharField(max_length=40, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.get_provider_display()} {self.name}"
