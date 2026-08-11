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


def _meta(published_screens, status="published"):
    """Fake the three-hop Graph read: node → assets → the JSON itself."""
    def get(url, **kw):
        if url.endswith("/999"):
            return _Resp({"id": "999", "name": "Zitch", "status": status})
        if url.endswith("/assets"):
            return _Resp({"data": [{"asset_type": "FLOW_JSON",
                                    "download_url": "https://cdn/flow.json"}]})
        return _Resp({"screens": [{"id": s} for s in published_screens]})
    return get


class PublishedFlowProbeTests(SimpleTestCase):

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
