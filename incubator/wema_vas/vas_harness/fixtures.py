"""Deliberately synthetic identifiers. These are NOT verified bank customers."""
CUSTOMERS = {
    "7110000001": {"name": "Zitch/SYNTHETIC TEST ONE", "bvn": "00000000001", "nin": ""},
    "7110000002": {"name": "Zitch/SYNTHETIC TEST TWO", "bvn": "", "nin": "00000000002"},
    "7110000003": {"name": "Zitch/SYNTHETIC TEST THREE", "bvn": "00000000003", "nin": "00000000003"},
}


def seed_customers():
    from .models import VirtualAccount

    # Idempotent: never reset a balance, unblock an account, or overwrite records.
    for number in CUSTOMERS:
        VirtualAccount.objects.get_or_create(number=number)
