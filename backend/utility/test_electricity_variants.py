"""Electricity must validate and pay the package for the selected meter type."""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from utility.models import WemaBiller
from utility.providers import _wema_vas_route, vtu_purchase, vtu_verify_customer
from utility.test_wema_billers import WEMA_ON


def catalogue(*, category="Electricity", biller="Ikeja Electricity", packages=None):
    """Use actual nested packages, retaining biller ID as a separate identity."""
    if packages is None:
        packages = [
            {"id": 1043, "name": "Ikeja Electric Prepaid"},
            {"id": 1411, "name": "Ikeja Electric Postpaid"},
        ]
    return {
        "success": True,
        "raw": {"result": [{"name": category, "billers": [{
            "id": 49, "name": biller, "packages": packages,
        }]}]},
        # These flattened rows intentionally disagree. Their `code` could be a
        # biller ID, so a catalogue sync must obtain IDs from nested packages.
        "bills": [{"code": "49", "biller": biller,
                   "category": category, "name": "Ikeja Electric Prepaid"}],
    }
