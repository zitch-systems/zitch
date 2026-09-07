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
    # Wema's own packageCode for this plan (differs from the VTU.ng plan_code).
    # Blank until synced by `manage.py seed_wema_plans`; a blank code keeps the
    # plan on VTU.ng, so data only moves to Wema once the catalogue is mapped.
    wema_code = models.CharField(max_length=60, blank=True, default="")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.get_network_display()} {self.name} ({self.validity})"


class CablePlan(models.Model):
    provider = models.CharField(max_length=2, choices=CABLE_PROVIDERS)
    name = models.CharField(max_length=80)
    validity = models.CharField(max_length=40, blank=True, default="30 days")
    cable_plan_code = models.CharField(max_length=40, unique=True)
    # Wema's own packageId/packageCode for this bouquet (differs from the VTU.ng
    # cable_plan_code). Blank until synced by `manage.py seed_wema_plans`; a blank
    # code keeps the bouquet on VTU.ng.
    wema_code = models.CharField(max_length=60, blank=True, default="")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.get_provider_display()} {self.name}"


class WemaBiller(models.Model):
    """A Wema/ALAT bills-catalogue code for a service that has no plan catalogue.

    Data and cable carry their Wema code on the plan row itself (DataPlan.wema_code /
    CablePlan.wema_code) because the customer picks a bundle. Electricity and betting
    have no bundle to pick — the customer types a meter number or a betting ID and an
    amount — so their Wema `packageId` has nowhere to live. Without it those two
    services could not route to Wema at all, which is why they stayed on VTU.ng.

    Keyed by the same `service_id` the app and the WhatsApp router already send
    ("ikeja-electric", "bet9ja-betting"), so routing is a lookup rather than another
    naming scheme to keep in sync.

    A missing row is not an error: it keeps that one service on VTU.ng, exactly as
    before. That matters because the codes are synced from a live catalogue
    (`manage.py seed_wema_plans --only billers`) and a partial sync must degrade
    service-by-service rather than break the ones it did map.
    """
    service_id = models.CharField(max_length=60, unique=True)
    # ALAT's integer packageId — what ValidateCustomer and PayBill actually take.
    package_id = models.CharField(max_length=60)
    # The biller this package belongs to, kept for operator review of a synced
    # catalogue; not sent in any request.
    biller_id = models.CharField(max_length=60, blank=True, default="")
    name = models.CharField(max_length=120, blank=True, default="")
    active = models.BooleanField(default=True)
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.service_id} -> {self.package_id}"
