"""Partner-bank electricity and betting routing.

Unmapped services now fail closed instead of falling back to a retired biller.
"""
from unittest import mock

from django.test import TestCase, override_settings

from utility.models import WemaBiller
from utility.providers import _wema_vas_route, vtu_purchase, vtu_verify_customer

# A deploy that can actually SELL bills: keyed, not simulating, AND carrying the
# status legends. The legends are not decoration here — providers.vas_can_settle
# refuses a purchase on a keyed-but-legend-less deploy, because a PROCESSING result
# could then never be settled or refunded. Without them the tests below would assert
# routing against a purchase that is (correctly) never submitted.
WEMA_ON = {"KEYS": {"wallet": "k", "airtime": "k", "bills": "k"}, "CHANNEL_ID": "c",
           "SIMULATION": False, "SOURCE_ACCOUNT": "0123456789",
           "VAS_STATUS_LEGEND": "200=success 400=failed 401=unauthorized_authentication_failed_or_invalid_api",
           "BILLS_STATUS_LEGEND": "200=success 400=failed 401=unauthorized_authentication_failed_or_invalid_api"}
