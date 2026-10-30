"""The retained biller product never inherits a legacy settlement source.

Funding approval itself is tested by the wallet layer. These boundary tests give
the resolver a canonical source and prove that every transport uses it, while a
refusal cannot reach either a real purchase or the simulation success path.
"""
from decimal import Decimal
from unittest import mock

import requests
from django.test import SimpleTestCase, override_settings

from utility import providers, wema
from wallet.services import LimitExceeded


LIVE = {
    "BASE_URL": "https://bank.example", "CHANNEL_ID": "test-channel",
    "KEYS": {"wallet": "test-wallet", "airtime": "test-airtime",
             "bills": "test-bills", "remita": "test-remita"},
    "SECURITY_INFO": "test-security", "SIMULATION": False,
    "SOURCE_ACCOUNT": "0100000001",
    "VAS_STATUS_LEGEND": "200=success 400=failed",
    "BILLS_STATUS_LEGEND": "200=success 400=failed",
    "REMITA_STATUS_LEGEND": "200=success 400=failed",
}
APPROVED_SOURCE = "0200000002"
SUPPLIED_SOURCE = "0300000003"
AMOUNT = Decimal("500.00")
REFERENCE = "biller-bound-reference"
RESOLVER = "wallet.services.biller_source_for_transaction"


def response(body):
    result = mock.Mock(status_code=200, content=b"{}")
    result.json.return_value = body
    return result


def purchases():
    return (
        (wema.purchase_airtime, (AMOUNT, REFERENCE, "08000000000", "MTN"), {},
         "airtime", "/api/Airtime/Client/PurchaseAirtime", "accountNumber"),
        (wema.purchase_data, (AMOUNT, REFERENCE, "08000000000", "MTN", "7"), {},
         "airtime", "/api/Data/Client/PurchaseData", "accountNumber"),
        (wema.pay_bill, (AMOUNT, REFERENCE), {"package_id": "7", "identifier": "meter"},
         "bills", "/api/Shared/PayBill", "customerAccount"),
        (wema.pay_remita, (AMOUNT, REFERENCE), {"rrr": "123456789"},
         "remita", "/api/RemitaPayment/ProcessRemitaPayment", "customerAccount"),
    )
