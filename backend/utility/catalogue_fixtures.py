"""Catalogue rows that make VAS purchasable, for tests and local setup.

The local plan tables keep the retired partner bank's codes (a data/cable bundle's
``wema_code`` and an electricity/betting service's ``WemaBiller.package_id``) as
historical evidence. Tests that exercise older purchase paths still need mapped
rows, so these helpers fill them in; 9PSB VAS reads its catalogue live from the bank.
"""
from .models import WemaBiller

#: Placeholder partner-bank codes. Their VALUES never matter — the rail is always
#: mocked or patched in tests — only that they are non-blank, which is what makes
#: the service routable.
_CODE = "9001"


def map_billers(*service_ids: str) -> None:
    """Map electricity/betting services (no plan catalogue, so codes live on a row)."""
    for service_id in (service_ids or ("ikeja-electric", "eko-electric",
                                       "abuja-electric", "bet9ja-betting")):
        variants = ("prepaid", "postpaid") if service_id.endswith("-electric") else ("",)
        for meter_type in variants:
            WemaBiller.objects.update_or_create(
                service_id=service_id, meter_type=meter_type,
                defaults={"package_id": _CODE, "name": service_id, "active": True})
