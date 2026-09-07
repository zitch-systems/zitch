"""The published-Flow probe: does Meta's Flow still have the screens we send?"""
import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from whatsapp.providers import published_flow_report


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.content = json.dumps(payload).encode()

    def json(self):
        return self._payload


LIVE = dict(
    WHATSAPP={"MODE": "live", "TOKEN": "t", "PHONE_NUMBER_ID": "1",
              "BASE_URL": "https://graph.facebook.com/v21.0"},
    WHATSAPP_FLOW={"FLOW_ID": "999", "PRIVATE_KEY": "k"},
)


def _local_screens():
    from pathlib import Path

    import whatsapp
    asset = Path(whatsapp.__file__).parent / "flow_assets" / "pin_flow.json"
    return {s["id"]: s for s in json.loads(asset.read_text())["screens"]}


def _meta(published_screens, status="published", props=None):
    """Fake the three-hop Graph read: node → assets → the JSON itself.

    Each published screen declares the SAME `data` properties the local file
    does unless `props` overrides one — so a test that only varies the screen
    list is saying "the ids differ, the contracts agree", and the property-drift
    case has to be asked for explicitly.
    """
    local = _local_screens()
    overrides = props or {}

    def _data(sid):
        if sid in overrides:
            return {k: {"type": "string"} for k in overrides[sid]}
        return (local.get(sid) or {}).get("data") or {}

    def get(url, **kw):
        if url.endswith("/999"):
            return _Resp({"id": "999", "name": "Zitch", "status": status})
        if url.endswith("/assets"):
            return _Resp({"data": [{"asset_type": "FLOW_JSON",
                                    "download_url": "https://cdn/flow.json"}]})
        return _Resp({"screens": [{"id": s, "data": _data(s)} for s in published_screens]})
    return get


class PublishedFlowProbeTests(SimpleTestCase):

    @override_settings(**LIVE)
    def test_public_liveness_never_waits_on_meta(self):
        """Meta latency cannot make Render recycle a healthy banking API."""
        with patch("whatsapp.providers.requests.get",
                   side_effect=AssertionError("healthz must not call Meta")) as get:
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["integrations"]["whatsapp_flow_published"]["status"],
            "diagnostic_only",
        )
        get.assert_not_called()

    @override_settings(**LIVE)
    def test_a_flow_one_publish_behind_names_the_missing_screens(self):
        """The failure this exists for: PIN works, the newer screens don't."""
        with patch("whatsapp.providers.requests.get",
                   side_effect=_meta(["PIN_SCREEN", "SUCCESS"])):
            r = published_flow_report()
        self.assertTrue(r["stale"])
        self.assertIn("IDENTITY_SCREEN", r["missing_screens"])
        self.assertIn("TRANSFER_FORM", r["missing_screens"])
        self.assertNotIn("PIN_SCREEN", r["missing_screens"])   # why it half-works

    @override_settings(**LIVE)
    def test_a_current_flow_is_not_stale(self):
        from pathlib import Path
        import whatsapp
        asset = Path(whatsapp.__file__).parent / "flow_assets" / "pin_flow.json"
        current = [s["id"] for s in json.loads(asset.read_text())["screens"]]
        with patch("whatsapp.providers.requests.get", side_effect=_meta(current)):
            r = published_flow_report()
        self.assertFalse(r["stale"])
        self.assertEqual(r["missing_screens"], [])

    @override_settings(**LIVE)
    def test_a_screen_present_on_both_sides_can_still_be_a_publish_behind(self):
        """The outage this probe missed. Every screen id matched, so it reported
        a healthy Flow — while SUCCESS had gained a `status` property here and
        not on Meta, and Meta rejected every terminal answer with the same
        "Couldn't load content" a timeout produces."""
        current = list(_local_screens())
        with patch("whatsapp.providers.requests.get",
                   side_effect=_meta(current, props={"SUCCESS": ["message"]})):
            r = published_flow_report()
        self.assertTrue(r["stale"])
        self.assertEqual(r["missing_screens"], [])          # ids alone say "fine"
        self.assertEqual(r["drifted_screens"], ["SUCCESS"])

    @override_settings(**LIVE)
    def test_a_property_meta_declares_and_the_code_drops_also_drifts(self):
        """Both directions fail at Meta: the endpoint must supply exactly the
        declared set, so a removal is as breaking as an addition."""
        current = list(_local_screens())
        with patch("whatsapp.providers.requests.get",
                   side_effect=_meta(current,
                                     props={"SUCCESS": ["status", "message", "extra"]})):
            self.assertEqual(published_flow_report()["drifted_screens"], ["SUCCESS"])

    @override_settings(**LIVE)
    def test_a_missing_screen_is_not_double_reported_as_drift(self):
        """A screen Meta doesn't have at all is one problem, not two."""
        with patch("whatsapp.providers.requests.get",
                   side_effect=_meta(["PIN_SCREEN", "SUCCESS"])):
            r = published_flow_report()
        self.assertIn("TRANSFER_FORM", r["missing_screens"])
        self.assertNotIn("TRANSFER_FORM", r["drifted_screens"])

    @override_settings(**LIVE)
    def test_a_draft_flow_is_reported_as_draft(self):
        with patch("whatsapp.providers.requests.get",
                   side_effect=_meta(["PIN_SCREEN"], status="DRAFT")):
            self.assertEqual(published_flow_report()["status"], "draft")

    @override_settings(**LIVE)
    def test_the_probe_never_raises_when_meta_is_unreachable(self):
        with patch("whatsapp.providers.requests.get", side_effect=OSError("boom")):
            self.assertEqual(published_flow_report()["status"], "unreachable")

    @override_settings(WHATSAPP={"MODE": "disabled"}, WHATSAPP_FLOW={})
    def test_it_makes_no_network_call_when_unconfigured(self):
        with patch("whatsapp.providers.requests.get") as get:
            self.assertEqual(published_flow_report()["status"], "unconfigured")
        get.assert_not_called()


class ProbeCachingTests(SimpleTestCase):
    """/healthz is unauthenticated and this is its only outbound reading.

    Uncached, three Graph calls per probe on a `--workers 1 --threads 8` dyno
    turned a public URL into a denial-of-service lever. The cache is the control
    that bounds it, so it is worth a test of its own.
    """

    def setUp(self):
        import whatsapp.providers as p
        p._flow_report_cache = None
        self.addCleanup(setattr, p, "_flow_report_cache", None)

    @override_settings(TESTING=False, **LIVE)
    def test_repeated_probes_ask_meta_once(self):
        screens = list(_local_screens())
        with patch("whatsapp.providers.requests.get", side_effect=_meta(screens)) as got:
            first = published_flow_report()
        calls = got.call_count
        self.assertGreater(calls, 0)
        with patch("whatsapp.providers.requests.get",
                   side_effect=AssertionError("probe went to the network again")):
            for _ in range(20):
                self.assertEqual(published_flow_report(), first)

    @override_settings(TESTING=False, **LIVE)
    def test_force_bypasses_the_cache(self):
        screens = list(_local_screens())
        with patch("whatsapp.providers.requests.get", side_effect=_meta(screens)):
            published_flow_report()
        with patch("whatsapp.providers.requests.get", side_effect=_meta(screens)) as got:
            published_flow_report(force=True)
        self.assertGreater(got.call_count, 0)

    @override_settings(TESTING=False, **LIVE)
    def test_a_probe_never_queues_behind_an_in_flight_refresh(self):
        """The point of the non-blocking lock: a burst holds no threads."""
        import whatsapp.providers as p

        p._flow_report_lock.acquire()
        self.addCleanup(lambda: p._flow_report_lock.locked() and p._flow_report_lock.release())
        with patch("whatsapp.providers.requests.get",
                   side_effect=AssertionError("waited on the refresh instead of returning")):
            self.assertEqual(published_flow_report(), {"status": "refreshing"})
