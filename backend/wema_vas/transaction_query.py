"""Bounded, read-only clients for Wema's NIP and Etranzact inbound queries.

These are not payout status APIs. A query never credits, refunds or releases a
hold. The bank's authenticated notification remains the credit authority.
"""
import json
import time

import requests
from django.db import DatabaseError
from django.views.decorators.debug import sensitive_variables

from .config import config
from .contracts import InvalidPayload, search_request
from .reconciliation import reconcile_snapshot


ENDPOINTS = {
    "nip": ("NIP_QUERY_URL", "https://apps3.wemabank.com/FintechTransQuery/api/v1/Trans/TransQuery"),
    "etranzact": ("ETRANZACT_QUERY_URL", "https://apps3.wemabank.com/eTzTransQuery/api/v1/Trans/EtzTransQuery"),
}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT = (4, 10)


class QueryUnavailable(Exception):
    """Fixed errors only; no bank payloads, account IDs or credentials in logs."""


class NoBankCredentials(requests.auth.AuthBase):
    """Suppress automatic .netrc credentials while respecting egress proxies."""
    def __call__(self, request):
        return request


def configured_endpoint(rail, values=None):
    if rail not in ENDPOINTS:
        raise InvalidPayload("Select nip or etranzact")
    values = config() if values is None else values
    key, expected = ENDPOINTS[rail]
    if values.get(key) != expected:
        raise QueryUnavailable("The bank query endpoint is missing or does not match the approved URL.")
    return expected


def query_configuration(values=None):
    result = {}
    for rail in ENDPOINTS:
        try:
            configured_endpoint(rail, values)
            result[rail + "_query_configured"] = True
        except QueryUnavailable:
            result[rail + "_query_configured"] = False
    return result


def unique_keys(pairs):
    body = {}
    for key, value in pairs:
        if key in body:
            raise InvalidPayload("Duplicate JSON key")
        body[key] = value
    return body


@sensitive_variables()
def fetch_query(rail, *, session_id="", account=""):
    payload = search_request(session_id=session_id, account=account)
    url = configured_endpoint(rail)
    started = time.monotonic()
    try:
        # No retries or redirects; never forward customer/diagnostic tokens,
        # Partnership API keys or our inbound bank callback token to this API.
        with requests.Session() as client:
            client.auth = NoBankCredentials()
            with client.post(url, json=payload, headers={"Accept": "application/json"},
                             timeout=REQUEST_TIMEOUT, allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise QueryUnavailable("The bank transaction query is unavailable; no financial changes made.")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    raise QueryUnavailable("The bank returned an unsupported response; no financial changes made.")
                raw = bytearray()
                for chunk in response.iter_content(chunk_size=16384):
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES or time.monotonic() - started > 15:
                        raise QueryUnavailable("The bank query exceeded its response limit; try a single session query.")
        body = json.loads(bytes(raw), object_pairs_hook=unique_keys)
        if not isinstance(body, dict):
            raise InvalidPayload("Invalid query envelope")
        # Observed on both supplied endpoints on 9 October 2026. This is
        # absence of rows, not transaction failure or proof of complete history.
        if body.get("status") == "02":
            description = body.get("status_desc")
            if (isinstance(description, str) and description.strip().rstrip(".").casefold() == "no data found"
                    and "transactions" in body and body["transactions"] in (None, [])):
                return {"status": "00", "transactions": []}
            raise InvalidPayload("Unsupported bank status")
        return body
    except (requests.RequestException, ValueError, UnicodeError, RecursionError):
        raise QueryUnavailable("The bank query could not be verified; no financial changes made.") from None


@sensitive_variables()
def query_and_reconcile(rail, *, session_id="", account=""):
    body = fetch_query(rail, session_id=session_id, account=account)
    try:
        report = reconcile_snapshot(body, session_id=session_id, account=account, rail=rail)
    except (InvalidPayload, DatabaseError):
        raise QueryUnavailable("Bank rows could not be matched safely to local evidence; contact bank support.") from None
    report.update({
        "source": "wema_transaction_query", "rail": rail,
        "source_authenticated": True, "transport": "verified_bank_https",
        "status": ("no_rows_returned" if not report["observed_count"] else
                   "review_required" if report["action_required"] else "returned_rows_match"),
        "limitation": "The query does not prove collection settlement or complete history. "
                      "Pagination and Etranzact success/reversal semantics still require bank confirmation. "
                      "These inbound queries cannot validate outgoing transfers or bill debits.",
    })
    return report
