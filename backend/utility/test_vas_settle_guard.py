"""A VAS purchase must never be accepted when this deploy could not settle it.

The hazard, end to end. ALAT's airtime/data and bills purchase endpoints may answer
``PROCESSING``. ``settle_or_refund`` correctly HOLDS the money on that — refunding a
maybe-delivered top-up would leak it — and leaves the row for the reconcile cron.
The cron's only tool is ``wema.vas_status``, which answers with the confirmed
200-success / 400-failure / 401-auth-failure integer ``transactionStatus``; with no
``WEMA_VAS_STATUS_LEGEND`` / ``WEMA_BILLS_STATUS_LEGEND`` configured, ``_parse_vas``
reports ``pending`` for every code it sees, forever. Nothing else can settle the row
either: the bank's own transaction callback routes back through ``vtu_requery``.

So on a legend-less deploy, a PROCESSING purchase is a customer debited with nothing
delivered, nothing refunded, and no job anywhere that can ever clear it. The safe answer is to refuse before debit when status access is unavailable. The
provider boundary checks again, so any later refusal refunds the reservation.
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from utility import providers as P

# CHANNEL_ID + an airtime subscription key => wema._vas_live("airtime") is True, i.e.
# real calls WILL be made. Note "bills" is wallet-key-covered (wema._WALLET_COVERED),
# so the wallet key alone already makes bill payments live.
_KEYED = {"BASE_URL": "https://apiplayground.alat.ng", "CHANNEL_ID": "chan-1",
          "KEYS": {"wallet": "subkey", "airtime": "airkey"},
          "SOURCE_ACCOUNT": "0100000001", "SECURITY_INFO": "sec", "SIMULATION": False}
_AIRTIME_LEGEND = {**_KEYED, "VAS_STATUS_LEGEND": "200=success 400=failed 401=unauthorized_authentication_failed_or_invalid_api"}
_BILLS_LEGEND = {**_KEYED, "BILLS_STATUS_LEGEND": "200=success 400=failed 401=unauthorized_authentication_failed_or_invalid_api"}
_REMITA_KEYED = {
    **_KEYED,
    "KEYS": {**_KEYED["KEYS"], "remita": "remita-key"},
    "REMITA_STATUS_LEGEND": "200=success 400=failed 401=unauthorized_authentication_failed_or_invalid_api",
}

_AIRTIME = ("mtn-airtime", {"amount": "500", "phone": "08012345678",
                            "source_account": "0100000001"})
