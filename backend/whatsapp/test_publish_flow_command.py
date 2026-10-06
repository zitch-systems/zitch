"""Management publishing must verify the selected Flow, endpoint and whole JSON."""
import hashlib
import json
from io import StringIO
from unittest.mock import patch

import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from whatsapp.management.commands.publish_flow import ASSET

LIVE = dict(
    WHATSAPP={"MODE": "live", "TOKEN": "test-token-never-print", "PHONE_NUMBER_ID": "1",
              "WABA_ID": "88", "BASE_URL": "https://graph.facebook.com/v21.0"},
    WHATSAPP_FLOW={"FLOW_ID": "999", "PRIVATE_KEY": "k"},
)
ENDPOINT = "https://api.zitch.ng/webhooks/whatsapp/flow"
TARGET = "whatsapp.management.commands.publish_flow"


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


def _report(status="draft", flow_id="999", **overrides):
    doc = json.loads(ASSET.read_bytes())
    digest = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"status": status, "flow_id": flow_id, "endpoint_uri": ENDPOINT,
            "stale": False, "validation_errors": [], "contract_matches": True,
            "local_contract_sha256": digest, "published_contract_sha256": digest, **overrides}


@override_settings(**LIVE)
class PublishFlowCommandTests(SimpleTestCase):
    def setUp(self):
        get = patch(TARGET + ".requests.get", return_value=_Resp(
            {"id": "999", "status": "DRAFT", "endpoint_uri": ENDPOINT}))
        post = patch(TARGET + ".requests.post", return_value=_Resp({"success": True}))
        report = patch(TARGET + ".published_flow_report", return_value=_report())
        self.get, self.post, self.report = get.start(), post.start(), report.start()
        for mock in (get, post, report):
            self.addCleanup(mock.stop)

    def test_upload_alone_does_not_publish(self):
        output = _run()
        self.assertEqual(self.post.call_count, 1)
        self.assertTrue(self.post.call_args.args[0].endswith("/999/assets"))
        self.report.assert_called_once_with(force=True, flow_id="999")
        self.assertIn("Not published", output)
        self.assertIn("--flow-id 999 --publish", output)

    def test_publish_sends_the_whole_asset_then_publishes(self):
        self.report.side_effect = [_report(), _report("published")]
        output = _run("--publish")
        upload, publish = self.post.call_args_list
        self.assertEqual(upload.kwargs["files"]["file"][1], ASSET.read_bytes())
        self.assertEqual(upload.kwargs["data"]["asset_type"], "FLOW_JSON")
        self.assertTrue(publish.args[0].endswith("/999/publish"))
        self.assertIn("Flow 999 is live", output)

    def test_validation_errors_block_the_publish(self):
        self.post.return_value = _Resp({"success": False, "validation_errors": [{"message": "unsafe detail"}]})
        with self.assertRaisesMessage(CommandError, "1 validation error"):
            _run("--publish")
        self.assertEqual(self.post.call_count, 1)
        self.report.assert_not_called()

    def test_rejected_upload_never_reaches_publish_or_prints_provider_secrets(self):
        self.post.return_value = _Resp({"error": {"message": "test-token-never-print", "code": 100}}, status=400)
        with self.assertRaises(CommandError) as caught:
            _run("--publish")
        self.assertIn("HTTP 400, Meta code 100", str(caught.exception))
        self.assertNotIn("test-token", str(caught.exception))
        self.assertEqual(self.post.call_count, 1)

    @override_settings(WHATSAPP={"MODE": "sandbox"}, WHATSAPP_FLOW={"FLOW_ID": "999"})
    def test_it_refuses_to_publish_from_a_host_that_is_not_live(self):
        with self.assertRaisesMessage(CommandError, "live WhatsApp channel"):
            _run()
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_dry_run_sends_no_mutations_and_uses_the_requested_flow(self):
        self.report.return_value = _report("published", "777", contract_matches=False,
                                           drifted_screens=["SUCCESS"])
        output = _run("--dry-run", "--flow-id", "777")
        self.post.assert_not_called()
        self.get.assert_not_called()
        self.report.assert_called_once_with(force=True, flow_id="777")
        self.assertIn("SUCCESS", output)
        self.assertNotIn("matches the repo", output)

    def test_local_screen_count_byte_size_and_digest_are_reported(self):
        output = _run("--dry-run")
        raw = ASSET.read_bytes()
        self.assertIn(f"{len(json.loads(raw)['screens'])} screens", output)
        self.assertIn(f"{len(raw):,} bytes", output)
        self.assertIn(_report()["local_contract_sha256"], output)

    def test_unknown_or_unreadable_status_never_reports_a_match(self):
        for status in ("unknown", "unreachable", "error", "refreshing", "draft", None):
            with self.subTest(status=status):
                self.report.return_value = _report(status)
                output = _run("--dry-run")
                self.assertIn("drift unknown", output)
                self.assertNotIn("matches the repo", output)

    def test_equal_properties_cannot_hide_a_different_full_json(self):
        self.report.return_value = _report("published", published_contract_sha256="0" * 64)
        output = _run("--dry-run")
        self.assertNotIn("matches the repo", output)

    def test_only_exact_published_flow_hash_reports_match(self):
        self.report.return_value = _report("published")
        self.assertIn("Published Meta Flow matches the repo", _run("--dry-run"))
        self.report.return_value = _report("published", "777")
        self.assertNotIn("matches the repo", _run("--dry-run"))

    def test_create_is_draft_only_and_preserves_configured_customer_flow(self):
        self.get.return_value = _Resp({"id": "777", "status": "DRAFT"})
        self.post.side_effect = [_Resp({"id": "777"}), _Resp({"success": True}), _Resp({"success": True})]
        self.report.return_value = _report(flow_id="777")
        output = _run("--create-name", "Zitch reviewed VAS", "--endpoint-uri", ENDPOINT)
        create, bind, upload = self.post.call_args_list
        self.assertTrue(create.args[0].endswith("/88/flows"))
        self.assertEqual(create.kwargs["json"], {"name": "Zitch reviewed VAS", "categories": ["SIGN_UP", "OTHER"]})
        self.assertTrue(bind.args[0].endswith("/777"))
        self.assertEqual(bind.kwargs["files"], {"endpoint_uri": (None, ENDPOINT)})
        self.assertNotIn("json", bind.kwargs)
        self.assertTrue(upload.args[0].endswith("/777/assets"))
        self.report.assert_called_once_with(force=True, flow_id="777")
        self.assertIn("Created draft Flow 777", output)
        self.assertIn("Not published", output)
        from django.conf import settings
        self.assertEqual(settings.WHATSAPP_FLOW["FLOW_ID"], "999")

    def test_create_can_use_explicit_waba_without_backend_waba_configuration(self):
        with override_settings(WHATSAPP={k: v for k, v in LIVE["WHATSAPP"].items() if k != "WABA_ID"}):
            output = _run("--create-name", "New VAS", "--waba-id", "123", "--endpoint-uri", ENDPOINT, "--dry-run")
        self.assertIn("no requests sent", output)
        self.post.assert_not_called()
        self.get.assert_not_called()
        self.report.assert_not_called()

    def test_create_requires_review_before_publish_and_an_endpoint(self):
        for args in (("--create-name", "New", "--publish"),
                     ("--create-name", "New", "--flow-id", "777"),
                     ("--create-name", "New")):
            with self.subTest(args=args), self.assertRaises(CommandError):
                _run(*args)
        self.post.assert_not_called()

    def test_invalid_identifiers_or_endpoint_are_rejected_before_writes(self):
        for args in (("--flow-id", "999/assets"), ("--endpoint-uri", "http://example.test/flow"),
                     ("--create-name", "   "),
                     ("--endpoint-uri", "https://user:password@example.test/flow"),
                     ("--endpoint-uri", "https://example.test/flow?token=secret"),
                     ("--waba-id", "88")):
            with self.subTest(args=args), self.assertRaises(CommandError):
                _run(*args)
        self.post.assert_not_called()

    def test_full_draft_readback_is_required_before_publication(self):
        for change in ({"contract_matches": False}, {"published_contract_sha256": "0" * 64},
                       {"status": "unknown"}, {"flow_id": "777"}, {"endpoint_uri": ""},
                       {"validation_errors": ["error"]}):
            with self.subTest(change=change):
                self.post.reset_mock()
                self.report.return_value = _report(**change)
                with self.assertRaisesMessage(CommandError, "could not be verified"):
                    _run("--publish")
                self.assertEqual(self.post.call_count, 1)

    def test_endpoint_binding_must_be_read_back_exactly_before_publish(self):
        with self.assertRaisesMessage(CommandError, "could not be verified"):
            _run("--endpoint-uri", "https://staging.zitch.ng/webhooks/whatsapp/flow", "--publish")
        self.assertEqual(self.post.call_count, 2)  # metadata and asset, never publish

    def test_existing_endpoint_is_pinned_without_an_explicit_binding_update(self):
        self.report.return_value = _report(endpoint_uri="https://other.zitch.ng/webhooks/whatsapp/flow")
        with self.assertRaisesMessage(CommandError, "could not be verified"):
            _run("--publish")
        self.assertEqual(self.post.call_count, 1)  # asset only, never publish

    def test_existing_endpoint_must_remain_the_same_after_publication(self):
        self.report.side_effect = [_report(), _report("published", endpoint_uri="https://other.zitch.ng/flow")]
        out = StringIO()
        with self.assertRaisesMessage(CommandError, "could not be verified"):
            call_command("publish_flow", "--publish", stdout=out)
        self.assertEqual(self.post.call_count, 2)
        self.assertNotIn("is live", out.getvalue())

    def test_positive_publish_response_is_not_proof_that_flow_is_published(self):
        self.report.side_effect = [_report(), _report("draft")]
        out = StringIO()
        with self.assertRaisesMessage(CommandError, "could not be verified"):
            call_command("publish_flow", "--publish", stdout=out)
        self.assertNotIn("is live", out.getvalue())

    def test_already_published_exact_flow_can_be_verified_without_mutation(self):
        self.get.return_value = _Resp({"id": "999", "status": "PUBLISHED", "endpoint_uri": ENDPOINT})
        self.report.return_value = _report("published")
        self.assertIn("already live", _run("--publish"))
        self.post.assert_not_called()

    def test_published_or_unknown_targets_are_not_overwritten(self):
        for status in ("PUBLISHED", "DEPRECATED", "UNKNOWN", ""):
            self.get.return_value = _Resp({"id": "999", "status": status})
            with self.subTest(status=status), self.assertRaisesMessage(CommandError, "not a draft"):
                _run()
        self.post.assert_not_called()

    def test_an_inspection_for_a_different_id_never_mutates(self):
        self.get.return_value = _Resp({"id": "777", "status": "DRAFT"})
        with self.assertRaisesMessage(CommandError, "different Flow ID"):
            _run()
        self.post.assert_not_called()

    def test_unconfirmed_or_redirected_upload_cannot_publish(self):
        for response in (_Resp({"success": False}), _Resp({}), _Resp({"success": True}, status=302)):
            with self.subTest(response=response):
                self.post.reset_mock()
                self.post.return_value = response
                with self.assertRaises(CommandError):
                    _run("--publish")
                self.assertEqual(self.post.call_count, 1)

    def test_timeout_is_reported_without_retry_or_secret_exception_text(self):
        self.post.side_effect = requests.Timeout("Bearer test-token-never-print")
        with self.assertRaises(CommandError) as caught:
            _run("--publish")
        self.assertIn("outcome unknown", str(caught.exception))
        self.assertNotIn("test-token", str(caught.exception))
        self.assertEqual(self.post.call_count, 1)

    def test_malformed_response_is_a_clean_command_error(self):
        self.post.return_value.json = lambda: (_ for _ in ()).throw(ValueError("secret"))
        with self.assertRaisesMessage(CommandError, "unreadable response"):
            _run()
