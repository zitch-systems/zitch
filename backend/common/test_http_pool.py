"""The pooled outbound sessions.

Both provider rails used to build a throwaway `requests` Session per call, paying
a TCP + TLS handshake before every WhatsApp message and before every call to the
bank gateway. These assert the replacement is actually pooled and — more
importantly — that it never retries, because a replayed POST is a duplicated
message on one rail and a duplicated payment on the other.
"""
from unittest import mock

import requests
from django.test import TestCase

from common import http_pool
from utility import wema
from whatsapp import providers

#: Reach the pooled path the way the helper documents: by saying "this process is
#: not the test runner", not by flipping settings.TESTING. The seam deliberately
#: does not read that setting — several tests turn it off to reach production-only
#: branches, and they must keep their `requests` patches.
_as_production = lambda: mock.patch.object(http_pool, "_UNDER_TEST_RUNNER", False)  # noqa: E731


class PooledSessionTests(TestCase):
    def setUp(self):
        http_pool.reset_pools()

    def tearDown(self):
        http_pool.reset_pools()

    def test_one_pool_per_rail_reused_across_calls(self):
        """A connection pool for the process, not one per message."""
        with _as_production():
            graph = http_pool.pooled_session("whatsapp-graph")
            gateway = http_pool.pooled_session("wema-gateway")
            self.assertIsNot(graph, requests)
            self.assertIs(graph, http_pool.pooled_session("whatsapp-graph"))
            self.assertIs(gateway, http_pool.pooled_session("wema-gateway"))
            # Separate rails: a stalled gateway must not starve WhatsApp of sockets.
            self.assertIsNot(graph, gateway)

    def test_never_retries(self):
        """urllib3 replaying a POST duplicates a message, or a payment."""
        with _as_production():
            for name in ("whatsapp-graph", "wema-gateway"):
                session = http_pool.pooled_session(name)
                for scheme in ("https://", "http://"):
                    adapter = session.get_adapter(f"{scheme}example.test")
                    self.assertEqual(adapter.max_retries.total, 0, name)

    def test_pool_covers_every_concurrent_caller(self):
        """Sized above gunicorn's 8 threads and the worker's 16-thread ceiling.

        A pool smaller than the number of threads calling at once discards the
        surplus connection after each use — a handshake per request again, which
        is the exact cost this exists to remove.
        """
        with _as_production():
            adapter = http_pool.pooled_session("whatsapp-graph").get_adapter("https://example.test")
        self.assertGreaterEqual(adapter._pool_maxsize, 16)

    def test_both_rails_route_through_the_pool(self):
        """The seams themselves, not just the helper."""
        with _as_production():
            self.assertIs(providers._graph(), http_pool.pooled_session("whatsapp-graph"))
            self.assertIs(wema._gateway(), http_pool.pooled_session("wema-gateway"))

    def test_test_suite_keeps_its_patch_seam(self):
        """Under the test runner the module is returned, so patches still bite.

        Roughly eighty call sites across the suite patch
        `whatsapp.providers.requests.*` and `utility.wema.requests.*`.
        """
        self.assertIs(providers._graph(), providers.requests)
        self.assertIs(wema._gateway(), wema.requests)

    def test_overriding_the_TESTING_setting_does_not_disable_the_seam(self):
        """The regression that motivated reading sys.argv instead of settings.

        Several suites use override_settings(TESTING=False) to reach a
        production-only branch. When the seam read that setting, those tests
        silently began taking the pooled path — their `requests` patches stopped
        intercepting and the calls went to the real network.
        """
        from django.test import override_settings

        with override_settings(TESTING=False):
            self.assertIs(providers._graph(), providers.requests)
            self.assertIs(wema._gateway(), wema.requests)
