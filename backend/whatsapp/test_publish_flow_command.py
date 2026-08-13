"""`manage.py publish_flow` — the deploy step that used to be a paste.

The Flow JSON is a contract with Meta, not a copy of one: the endpoint must
answer with exactly the properties the published screen declares. Publishing it
by hand made the two halves ship at different times, and the failure mode of a
mismatch is invisible — "Couldn't load content. Try again later.", identical to
a timeout, on every ending of the Flow.
"""
import json
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from io import StringIO

LIVE = dict(
    WHATSAPP={"MODE": "live", "TOKEN": "t", "PHONE_NUMBER_ID": "1",
              "BASE_URL": "https://graph.facebook.com/v21.0"},
    WHATSAPP_FLOW={"FLOW_ID": "999", "PRIVATE_KEY": "k"},
)


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.content = json.dumps(payload).encode()

    def json(self):
        return self._payload


def _run(*args, **opts):
    out, err = StringIO(), StringIO()
    call_command("publish_flow", *args, stdout=out, stderr=err, **opts)
    return out.getvalue() + err.getvalue()


class PublishFlowCommandTests(SimpleTestCase):

    @override_settings(**LIVE)
    def test_upload_alone_does_not_publish(self):
        """The draft is safe to replace at any time; going live is not. Two
        steps precisely so the validation errors can be read before anyone
        commits to the half that customers see."""
        with patch("whatsapp.management.commands.publish_flow.requests.post",
                   return_value=_Resp({"success": True})) as post, \
             patch("whatsapp.management.commands.publish_flow.published_flow_report", return_value={"status": "unconfigured"}):
            output = _run()
        self.assertEqual(post.call_count, 1)
        self.assertIn("/assets", post.call_args[0][0])
        self.assertIn("re-run with --publish", output)

    @override_settings(**LIVE)
    def test_publish_sends_the_whole_asset_then_publishes(self):
        import whatsapp
        from pathlib import Path

        raw = (Path(whatsapp.__file__).parent / "flow_assets" / "pin_flow.json").read_text()
        with patch("whatsapp.management.commands.publish_flow.requests.post",
                   return_value=_Resp({"success": True})) as post, \
             patch("whatsapp.management.commands.publish_flow.published_flow_report",
                   return_value={"status": "published", "stale": False}):
            _run("--publish")
        upload, publish = post.call_args_list
        # The file goes up byte-for-byte — a truncated upload would validate and
        # then render a Flow missing screens.
        self.assertEqual(upload.kwargs["files"]["file"][1], raw.encode())
        self.assertEqual(upload.kwargs["data"]["asset_type"], "FLOW_JSON")
        self.assertIn("/999/publish", publish.args[0])

    @override_settings(**LIVE)
    def test_validation_errors_block_the_publish(self):
        """Meta would refuse anyway; refusing here says why, once, and never
        leaves a half-applied state."""
        with patch("whatsapp.management.commands.publish_flow.requests.post",
                   return_value=_Resp({"validation_errors": [
                       {"error_type": "INVALID_PROPERTY", "message": "bad"}]})) as post:
            with self.assertRaises(CommandError) as caught:
                _run("--publish")
        self.assertIn("validation error", str(caught.exception))
        self.assertEqual(post.call_count, 1)   # uploaded, never published

    @override_settings(**LIVE)
    def test_a_rejected_upload_never_reaches_publish(self):
        with patch("whatsapp.management.commands.publish_flow.requests.post",
                   return_value=_Resp({"error": {"message": "no"}}, status=400)) as post:
            with self.assertRaises(CommandError):
                _run("--publish")
        self.assertEqual(post.call_count, 1)

    @override_settings(WHATSAPP={"MODE": "sandbox"}, WHATSAPP_FLOW={"FLOW_ID": "999"})
    def test_it_refuses_to_publish_from_a_host_that_is_not_live(self):
        """A sandbox host holds no production token; running this there should
        fail loudly rather than send a Flow somewhere unexpected."""
        with patch("whatsapp.management.commands.publish_flow.requests.post") as post:
            with self.assertRaises(CommandError):
                _run()
        post.assert_not_called()

    @override_settings(**LIVE)
    def test_dry_run_sends_nothing(self):
        with patch("whatsapp.management.commands.publish_flow.requests.post") as post, \
             patch("whatsapp.management.commands.publish_flow.published_flow_report",
                   return_value={"status": "published", "stale": True,
                                 "missing_screens": [], "drifted_screens": ["SUCCESS"]}):
            output = _run("--dry-run")
        post.assert_not_called()
        self.assertIn("SUCCESS", output)

    @override_settings(**LIVE)
    def test_the_local_asset_is_reported_before_anything_is_sent(self):
        """17 screens and ~250KB is the sanity check that the file on this host
        is the real one — a truncated checkout should be obvious immediately."""
        with patch("whatsapp.management.commands.publish_flow.requests.post"), \
             patch("whatsapp.management.commands.publish_flow.published_flow_report", return_value={"status": "unconfigured"}):
            output = _run("--dry-run")
        self.assertIn("17 screens", output)

    @override_settings(**LIVE)
    def test_an_unreadable_probe_never_reports_a_match(self):
        """`stale` is False when the probe could not READ Meta, which is not the
        same as agreeing with it — the most reassuring output for the least
        informed state."""
        with patch("whatsapp.management.commands.publish_flow.published_flow_report",
                   return_value={"status": "unreachable", "detail": "Timeout"}):
            output = _run("--dry-run")
        self.assertIn("drift unknown", output)
        self.assertNotIn("matches the repo", output)
