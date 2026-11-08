import warnings
from unittest.mock import patch

from django.core.cache import cache
from django.core.cache.backends.base import CacheKeyWarning
from django.test import SimpleTestCase

from .reconciliation import alert_due


class ReconciliationAlertTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_readable_incident_labels_work_without_cache_key_warnings(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", CacheKeyWarning)
            self.assertTrue(alert_due("9psb-balance reconciliation", ["a", "b"]))
            self.assertFalse(alert_due("9psb-balance reconciliation", ["b", "a"]))
            self.assertTrue(alert_due("9psb-settlement report", ["a", "b"]))
            self.assertTrue(alert_due("9psb-balance reconciliation", ["a", "c"]))
            self.assertTrue(alert_due("Review \N{NAIRA SIGN} " * 100, ["private-reference"]))

    def test_reference_lists_with_separators_remain_distinct_incidents(self):
        self.assertTrue(alert_due("reconciliation", ["a|b", "c"]))
        self.assertTrue(alert_due("reconciliation", ["a", "b|c"]))

    def test_a_cache_outage_cannot_suppress_an_incident(self):
        with patch("utility.reconciliation.cache.add", side_effect=RuntimeError("cache unavailable")):
            self.assertTrue(alert_due("reconciliation", ["reference"]))
