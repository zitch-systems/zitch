from django.db import models


class ExamProduct(models.Model):
    """An exam PIN/token product (WAEC, NECO, JAMB, NABTEB...)."""

    code = models.CharField(max_length=20, unique=True)   # e.g. "waec"
    name = models.CharField(max_length=40)                # e.g. "WAEC"
    description = models.CharField(max_length=80)         # e.g. "Result Checker PIN"
    price = models.DecimalField(max_digits=10, decimal_places=2)
    # VTU provider service code for this exam, used when live.
    service_id = models.CharField(max_length=40, blank=True, default="")
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.description})"
