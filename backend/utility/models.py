from django.db import models

# Network / provider ids match the values the Expo app sends.
NETWORKS = [("1", "MTN"), ("2", "GLO"), ("3", "Airtel"), ("4", "9mobile")]
class WemaBiller(models.Model):
    """A Wema/ALAT bills-catalogue code for a service that has no plan catalogue.

    Retained read-only as historical evidence. Nothing routes through it now: the
    services it mapped -- electricity, betting, cable, data -- were bought through
    the 9PSB VAS rail, which has been removed, and the DataPlan and CablePlan
    tables that carried the same code for bundle-based products went with it.
    It is kept because it is Wema-era evidence, like the balance archives, not
    because anything reads it.

    Keyed by the same `service_id` the app and the WhatsApp router already send
    ("ikeja-electric", "bet9ja-betting"), so routing is a lookup rather than another
    naming scheme to keep in sync.

    A missing row takes that ONE service off sale and leaves every mapped service
    working: providers._wema_vas_route returns None for it and the purchase is refused
    before any debit. That per-service granularity is the point, because the codes are
    synced from a live catalogue (`manage.py seed_wema_plans --only billers`) and a
    partial sync must degrade service-by-service rather than break what it did map.
    """
    service_id = models.CharField(max_length=60)
    # A historical blank electricity variant is retained for audit but cannot
    # route either meter type. Bookmakers and other unvaried billers use blank.
    meter_type = models.CharField(max_length=8, blank=True, default="",
                                  choices=[("", "Not specified"), ("prepaid", "Prepaid"), ("postpaid", "Postpaid")])
    # ALAT's integer packageId — what ValidateCustomer and PayBill actually take.
    package_id = models.CharField(max_length=60)
    # The biller this package belongs to, kept for operator review of a synced
    # catalogue; not sent in any request.
    biller_id = models.CharField(max_length=60, blank=True, default="")
    name = models.CharField(max_length=120, blank=True, default="")
    active = models.BooleanField(default=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["service_id", "meter_type"], name="uniq_wema_biller_variant"),
            models.CheckConstraint(condition=models.Q(meter_type__in=["", "prepaid", "postpaid"]),
                                   name="wema_biller_variant_valid"),
        ]

    def __str__(self):
        return f"{self.service_id} {self.meter_type or '(unspecified)'} -> {self.package_id}"
