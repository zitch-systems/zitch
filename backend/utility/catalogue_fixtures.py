"""Catalogue rows that make VAS purchasable, for tests and local setup.

Not a convenience. The partner bank is the only VAS rail and it fulfils against its
OWN codes — a data/cable bundle's ``wema_code`` and an electricity/betting service's
``WemaBiller.package_id``. A service with no code cannot be routed, so
``providers._wema_vas_route`` returns None and the purchase is refused before any
debit (see ``providers.vtu_purchase``).

That is a change of KIND, not degree. While the retired rail still existed an
unmapped service simply fell through to a provider that needed no catalogue, so a
test (or a fresh deploy) could buy airtime, data, cable and electricity with an empty
catalogue and nothing looked wrong. Now the catalogue IS the product: nothing is on
sale until ``manage.py seed_wema_plans`` has run. These helpers are the test-sized
equivalent of that command, so a fixture reads as the mapped deploy it stands in for
rather than silently exercising a refusal path.
"""
from .models import CablePlan, DataPlan, WemaBiller

#: Placeholder partner-bank codes. Their VALUES never matter — the rail is always
#: mocked or patched in tests — only that they are non-blank, which is what makes
#: the service routable.
_CODE = "9001"


def map_billers(*service_ids: str) -> None:
    """Map electricity/betting services (no plan catalogue, so codes live on a row)."""
    for service_id in (service_ids or ("ikeja-electric", "eko-electric",
                                       "abuja-electric", "bet9ja-betting")):
        WemaBiller.objects.update_or_create(
            service_id=service_id,
            defaults={"package_id": _CODE, "name": service_id, "active": True})


def map_cable() -> None:
    """Give every cable provider at least one mapped bouquet.

    One per provider is the minimum that works, because a smartcard is validated
    BEFORE a bouquet is chosen and validation borrows any mapped bouquet of the same
    provider to name the biller (providers._cable_validation_code).
    """
    for code, provider, name, price in (("gotv-max", "1", "GOtv Max", "5700"),
                                        ("dstv-compact", "2", "DStv Compact", "19000"),
                                        ("startimes-basic", "3", "StarTimes Basic", "4000")):
        CablePlan.objects.update_or_create(
            cable_plan_code=code,
            defaults={"provider": provider, "name": name, "price": price,
                      "wema_code": _CODE, "active": True})


def map_existing_plans() -> None:
    """Stamp a code onto every DataPlan/CablePlan a fixture already created.

    For tests that build their own plan rows with specific prices or codes: they keep
    their row and just become buyable, instead of having to thread ``wema_code``
    through every ``objects.create`` call.
    """
    DataPlan.objects.filter(wema_code="").update(wema_code=_CODE)
    CablePlan.objects.filter(wema_code="").update(wema_code=_CODE)
