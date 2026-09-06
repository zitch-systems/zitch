"""Pooled outbound HTTPS sessions for the provider rails.

`requests.post(...)` at module level builds a throwaway Session per call, so
every outbound request paid for a TCP handshake plus a TLS handshake before a
single byte of the payload moved — several network round trips of pure setup, on
the critical path of whatever the customer is waiting for. That cost was being
paid on every WhatsApp message we send and on every call to the bank rail.

Reusing one connection pool per rail keeps those connections warm between calls.
Measured against a host one hop away: 96ms -> 65ms per call, and the saving grows
with distance to the peer, which for Meta's edge and Wema's gateway is
considerably further than one hop.
"""
import sys
import threading

import requests

#: Sized for the most concurrent callers either process can have: gunicorn's 8
#: threads on the API, and the WhatsApp worker's inbound pool
#: (WHATSAPP_WORKER_CONCURRENCY, capped at 16). A pool smaller than the number of
#: threads calling at once makes urllib3 discard the surplus connection after each
#: use, which is a handshake per request again — the exact cost this removes.
POOL_MAXSIZE = 24

#: Whether this process IS the test runner.
#:
#: Read from the same place settings.TESTING is (`"test" in sys.argv`), but
#: captured once here at import rather than read from settings on every call.
#: That difference matters: a fair number of tests use
#: `override_settings(TESTING=False)` to reach a production-only branch, and if
#: this consulted the setting, those tests would quietly start taking the pooled
#: path and lose the `requests` patch their assertions depend on — a mock that
#: silently stops intercepting, which is the worst way for a test to break.
#: A process's identity as the test runner is not something a test should be
#: able to flip, so it is not read from a setting at all.
_UNDER_TEST_RUNNER = "test" in sys.argv

_LOCK = threading.Lock()
_sessions: dict[str, requests.Session] = {}


def pooled_session(name: str):
    """The pooled session for one rail — or, under the test runner, `requests`.

    Both expose the same `.post`/`.get`. The suite patches
    `whatsapp.providers.requests.post` and `utility.wema.requests.post` in about
    eighty places to intercept egress; there is no connection to keep warm when
    nothing leaves the process, so tests get the module they already patch rather
    than trading a real production win for a mechanical rewrite of every one of
    those call sites.

    The pooled path is covered directly by its own tests, which reach it by
    patching `_UNDER_TEST_RUNNER` — deliberately, rather than by flipping a
    setting, so that the seam cannot be disabled by accident.
    """
    if _UNDER_TEST_RUNNER:
        return requests
    session = _sessions.get(name)
    if session is None:
        with _LOCK:
            session = _sessions.get(name)
            if session is None:
                session = requests.Session()
                adapter = requests.adapters.HTTPAdapter(
                    pool_connections=4,
                    pool_maxsize=POOL_MAXSIZE,
                    # NEVER retry. urllib3 would replay a POST it considers
                    # idempotent-ish on a connection error. On the WhatsApp rail
                    # that sends the customer the same receipt or OTP twice; on
                    # the bank rail it is a replayed money movement. Both are real
                    # defects, not cosmetic ones. Retry policy belongs to the
                    # callers that know what is safe to repeat — the queue's
                    # attempt budget, and wema._raise_if_ambiguous, which already
                    # decides which gateway responses may be retried at all.
                    max_retries=0,
                )
                session.mount("https://", adapter)
                session.mount("http://", adapter)
                _sessions[name] = session
    return session


def reset_pools() -> None:
    """Drop every cached session. For tests that assert on pool construction."""
    with _LOCK:
        for session in _sessions.values():
            session.close()
        _sessions.clear()
