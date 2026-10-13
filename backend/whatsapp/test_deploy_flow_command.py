"""The deployment hook performs only the explicitly reviewed Flow operation."""
import hashlib
import json
import os
from contextlib import nullcontext
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from whatsapp.management.commands.deploy_whatsapp_flow import ASSET, PREFIX, _operation_lock, _owned_flows
from whatsapp.test_publish_flow_command import ENDPOINT, LIVE, _Resp


TARGET = "whatsapp.management.commands.deploy_whatsapp_flow"
REQUEST = "partnership-20261013-01"
NAME = f"Zitch Partnership {REQUEST}"
BASE = "https://graph.facebook.com/v21.0"


def _digest():
    return hashlib.sha256(json.dumps(json.loads(ASSET.read_bytes()), sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


@override_settings(**LIVE)
class DeployFlowCommandTests(SimpleTestCase):
    def setUp(self):
        self.env = {
            PREFIX + "OPERATION": "create-draft", PREFIX + "REQUEST": REQUEST,
            PREFIX + "SHA256": _digest(), PREFIX + "ENDPOINT_URI": ENDPOINT,
            PREFIX + "FLOW_ID": "",
            PREFIX + "REPLACEMENT_FLOW_ID": "",
        }
        self.enterContext(patch.dict(os.environ, self.env))
        self.lock = self.enterContext(patch(TARGET + "._operation_lock", side_effect=lambda _: nullcontext()))
        self.record = self.enterContext(patch(TARGET + "._request_record", return_value=(
            MagicMock(), {"sha256": _digest(), "create_attempted": False})))
        self.owned = self.enterContext(patch(TARGET + "._owned_flows", return_value=[]))
        self.delegate = self.enterContext(patch(TARGET + ".call_command"))
        self.get = self.enterContext(patch(TARGET + ".requests.get", return_value=_Resp({
            "id": "777", "name": NAME, "status": "DRAFT", "endpoint_uri": ENDPOINT,
        })))
        self.post = self.enterContext(patch(TARGET + ".requests.post", return_value=_Resp({"success": True})))
        self.verify = self.enterContext(patch(TARGET + ".PublishCommand._report_drift"))
        self.old_asset = self.enterContext(patch(TARGET + ".published_flow_report", return_value={
            "flow_id": "555", "status": "published", "published_screens": ["PIN_SCREEN", "VAS_SETUP"],
        }))
        self.out, self.err = StringIO(), StringIO()

    def run_hook(self, operation=None):
        if operation:
            os.environ[PREFIX + "OPERATION"] = operation
        call_command("deploy_whatsapp_flow", stdout=self.out, stderr=self.err)

    def arm_publish(self, status="DRAFT"):
        os.environ[PREFIX + "FLOW_ID"] = "777"
        self.owned.return_value = [{"id": "777", "name": NAME, "status": status}]
        self.get.return_value = _Resp({"id": "777", "name": NAME, "status": status,
                                      "endpoint_uri": ENDPOINT})

    def arm_deprecate(self):
        os.environ[PREFIX + "FLOW_ID"] = "555"
        os.environ[PREFIX + "REPLACEMENT_FLOW_ID"] = "777"
        self.owned.return_value = [{"id": "777", "name": NAME, "status": "PUBLISHED"},
                                  {"id": "555", "name": "Old account setup", "status": "PUBLISHED"}]
        self.get.side_effect = [_Resp({"id": "555", "status": "PUBLISHED"}),
                               _Resp({"id": "555", "status": "DEPRECATED"})]
        self.enterContext(override_settings(WHATSAPP_FLOW={**LIVE["WHATSAPP_FLOW"], "FLOW_ID": "777"}))

    def test_unarmed_build_is_inert_even_without_review_inputs(self):
        for name in self.env:
            os.environ.pop(name, None)
        self.run_hook()
        self.lock.assert_not_called()
        self.owned.assert_not_called()
        self.post.assert_not_called()
        self.delegate.assert_not_called()

    def test_review_inputs_fail_before_database_or_meta_work(self):
        for changes in ({"OPERATION": "automatic"}, {"REQUEST": "bad / request"},
                        {"SHA256": "0" * 64}, {"SHA256": ""},
                        {"ENDPOINT_URI": "https://user:secret@example.com/flow"},
                        {"ENDPOINT_URI": "http://example.com/flow"}, {"FLOW_ID": "777"}):
            with self.subTest(changes=changes), patch.dict(os.environ, {
                **self.env, **{PREFIX + key: value for key, value in changes.items()}
            }), self.assertRaises(CommandError):
                self.run_hook()
        self.lock.assert_not_called()
        self.owned.assert_not_called()
        self.post.assert_not_called()

    def test_new_request_prepares_one_draft_without_publishing(self):
        self.run_hook()
        self.delegate.assert_called_once_with("publish_flow", create_name=NAME, waba_id="88",
                                              endpoint_uri=ENDPOINT, stdout=self.delegate.call_args.kwargs["stdout"],
                                              stderr=self.delegate.call_args.kwargs["stderr"])
        self.post.assert_not_called()
        self.assertNotIn("publish", self.delegate.call_args.kwargs)
        self.assertIn(_digest(), self.out.getvalue())
        self.assertIn("24 screens", self.out.getvalue())
        self.assertNotIn("test-token-never-print", self.out.getvalue() + self.err.getvalue())

    def test_retry_reuses_exact_named_draft_and_never_creates_again(self):
        self.owned.return_value = [{"id": "777", "name": NAME, "status": "DRAFT"},
                                  {"id": "888", "name": "Unrelated", "status": "DRAFT"}]
        self.run_hook()
        self.assertEqual(self.delegate.call_args.kwargs["flow_id"], "777")
        self.assertNotIn("create_name", self.delegate.call_args.kwargs)
        self.assertIn("Reusing draft Flow 777", self.out.getvalue())
        self.post.assert_not_called()

    def test_duplicate_request_names_and_published_names_never_create(self):
        for rows in ([{"id": "777", "name": NAME, "status": "DRAFT"},
                      {"id": "778", "name": NAME, "status": "DRAFT"}],
                     [{"id": "777", "name": NAME, "status": "PUBLISHED"}]):
            with self.subTest(rows=rows), self.assertRaises(CommandError):
                self.owned.return_value = rows
                self.run_hook()
        self.delegate.assert_not_called()
        self.post.assert_not_called()

    def test_publish_requires_explicit_id_owned_by_this_request(self):
        with self.assertRaises(CommandError):
            self.run_hook("publish")
        os.environ[PREFIX + "FLOW_ID"] = "777"
        for rows in ([], [{"id": "778", "name": NAME, "status": "DRAFT"}],
                     [{"id": "777", "name": "Someone else's request", "status": "DRAFT"}]):
            with self.subTest(rows=rows), self.assertRaises(CommandError):
                self.owned.return_value = rows
                self.run_hook("publish")
        self.post.assert_not_called()
        self.delegate.assert_not_called()

    def test_publish_checks_reviewed_remote_contract_before_post_and_never_uploads(self):
        self.arm_publish()
        order = []
        self.verify.side_effect = lambda *a, **k: order.append(k.get("expected_status", "published"))
        self.post.side_effect = lambda *a, **k: order.append("publish") or _Resp({"success": True})
        self.run_hook("publish")
        self.assertEqual(order, ["draft", "publish", "published"])
        self.post.assert_called_once_with(f"{BASE}/777/publish", timeout=60, allow_redirects=False,
                                         headers={"Authorization": "Bearer test-token-never-print"})
        self.delegate.assert_not_called()
        self.assertIn("WHATSAPP_FLOW_ID remains unchanged", self.out.getvalue())

    def test_drift_blocks_publish_instead_of_uploading_a_replacement(self):
        self.arm_publish()
        self.verify.side_effect = CommandError("remote asset differs")
        with self.assertRaisesMessage(CommandError, "remote asset differs"):
            self.run_hook("publish")
        self.post.assert_not_called()
        self.delegate.assert_not_called()

    def test_changed_node_identity_or_endpoint_blocks_publish(self):
        self.arm_publish()
        for field, value in (("id", "778"), ("name", "Unreviewed"),
                             ("endpoint_uri", "https://other.example.com/flow"), ("status", "DEPRECATED")):
            self.get.return_value = _Resp({"id": "777", "name": NAME, "status": "DRAFT",
                                          "endpoint_uri": ENDPOINT, field: value})
            with self.subTest(field=field), self.assertRaises(CommandError):
                self.run_hook("publish")
        self.post.assert_not_called()

    def test_retry_of_already_published_flow_only_verifies(self):
        self.arm_publish(status="PUBLISHED")
        self.run_hook("publish")
        self.verify.assert_called_once_with("777", _digest(), strict=True,
                                            expected_status="published", endpoint=ENDPOINT)
        self.post.assert_not_called()
        self.delegate.assert_not_called()

    def test_post_publish_readback_failure_stops_deploy_and_claims_no_success(self):
        self.arm_publish()
        self.verify.side_effect = [None, CommandError("cannot verify publication")]
        with self.assertRaises(CommandError):
            self.run_hook("publish")
        self.assertNotIn("Flow 777 published", self.out.getvalue())

    def test_unconfirmed_publication_stops_deploy(self):
        self.arm_publish()
        self.post.return_value = _Resp({"success": False})
        with self.assertRaisesMessage(CommandError, "did not confirm publication"):
            self.run_hook("publish")

    def test_owner_id_can_be_explicit_environment_when_runtime_dict_omits_it(self):
        with override_settings(WHATSAPP={k: v for k, v in LIVE["WHATSAPP"].items() if k != "WABA_ID"}), \
                patch.dict(os.environ, {"WHATSAPP_WABA_ID": "88"}):
            self.run_hook()
        self.lock.assert_called_once_with("88")

    def test_deprecate_requires_selected_verified_replacement_and_old_setup_screens(self):
        self.arm_deprecate()
        self.run_hook("deprecate")
        self.verify.assert_called_once_with("777", _digest(), strict=True, endpoint=ENDPOINT)
        self.old_asset.assert_called_once_with(force=True, flow_id="555")
        self.post.assert_called_once_with(f"{BASE}/555/deprecate", timeout=60, allow_redirects=False,
                                         headers={"Authorization": "Bearer test-token-never-print"})
        self.delegate.assert_not_called()
        self.assertIn("Flow 555 deprecated", self.out.getvalue())

    def test_deprecate_never_targets_selected_or_unselected_replacement(self):
        self.arm_deprecate()
        for replacement, configured in (("555", "555"), ("777", "999")):
            with self.subTest(replacement=replacement), patch.dict(os.environ, {
                PREFIX + "REPLACEMENT_FLOW_ID": replacement,
            }), override_settings(WHATSAPP_FLOW={"FLOW_ID": configured}), self.assertRaises(CommandError):
                self.run_hook("deprecate")
        self.post.assert_not_called()
        self.lock.assert_not_called()

    def test_deprecate_rejects_replacement_drift_before_touching_old_flow(self):
        self.arm_deprecate()
        self.verify.side_effect = CommandError("replacement is not exact and published")
        with self.assertRaises(CommandError):
            self.run_hook("deprecate")
        self.get.assert_not_called()
        self.old_asset.assert_not_called()
        self.post.assert_not_called()

    def test_deprecate_refuses_pin_only_or_unreadable_old_flow(self):
        self.arm_deprecate()
        self.get.side_effect = None
        self.get.return_value = _Resp({"id": "555", "status": "PUBLISHED"})
        for report in ({"flow_id": "555", "status": "published", "published_screens": ["PIN_SCREEN", "SUCCESS"]},
                       {"flow_id": "556", "status": "published", "published_screens": ["VAS_SETUP"]},
                       {"status": "unreachable"}):
            self.old_asset.return_value = report
            with self.subTest(report=report), self.assertRaises(CommandError):
                self.run_hook("deprecate")
        self.post.assert_not_called()

    def test_deprecate_refuses_old_flow_outside_the_owned_inventory(self):
        self.arm_deprecate()
        self.owned.return_value = self.owned.return_value[:1]
        with self.assertRaises(CommandError):
            self.run_hook("deprecate")
        self.post.assert_not_called()

    def test_already_deprecated_retry_only_verifies_replacement_and_status(self):
        self.arm_deprecate()
        self.get.side_effect = [_Resp({"id": "555", "status": "DEPRECATED"})]
        self.run_hook("deprecate")
        self.verify.assert_called_once()
        self.old_asset.assert_not_called()
        self.post.assert_not_called()
        self.assertIn("already deprecated", self.out.getvalue())

    def test_deprecation_requires_status_readback_before_success(self):
        self.arm_deprecate()
        self.get.side_effect = [_Resp({"id": "555", "status": "PUBLISHED"}),
                               _Resp({"id": "555", "status": "PUBLISHED"})]
        with self.assertRaisesMessage(CommandError, "could not be verified"):
            self.run_hook("deprecate")
        self.assertNotIn("Flow 555 deprecated", self.out.getvalue())


class FlowDeploymentLockAndInventoryTests(SimpleTestCase):
    def test_session_lock_releases_after_success_and_failure_without_a_transaction(self):
        for fails in (False, True):
            db = MagicMock(vendor="postgresql")
            cursor = db.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = (True,)
            with self.subTest(fails=fails), patch(TARGET + ".connection", db):
                try:
                    with _operation_lock("88"):
                        if fails:
                            raise CommandError("stop")
                except CommandError:
                    pass
            calls = cursor.execute.call_args_list
            self.assertEqual([call.args[0] for call in calls],
                             ["SELECT pg_try_advisory_lock(%s)", "SELECT pg_advisory_unlock(%s)"])
            self.assertEqual(calls[0].args[1], calls[1].args[1])
            db.set_autocommit.assert_not_called()
            db.commit.assert_not_called()

    def test_concurrent_operation_is_refused_without_entering_body(self):
        db = MagicMock(vendor="postgresql")
        db.cursor.return_value.__enter__.return_value.fetchone.return_value = (False,)
        with patch(TARGET + ".connection", db), self.assertRaises(CommandError):
            with _operation_lock("88"):
                self.fail("body must not run without the advisory lock")

    def test_inventory_uses_only_owner_graph_endpoint_and_cursors(self):
        with patch(TARGET + ".requests.get", side_effect=[
            _Resp({"data": [{"id": "1"}], "paging": {"next": "https://untrusted.example/next",
                                                         "cursors": {"after": "cursor-2"}}}),
            _Resp({"data": [{"id": "2"}]}),
        ]) as get:
            rows = _owned_flows(BASE, "88", {"Authorization": "Bearer token"})
        self.assertEqual(rows, [{"id": "1"}, {"id": "2"}])
        self.assertEqual([call.args[0] for call in get.call_args_list], [f"{BASE}/88/flows"] * 2)
        self.assertEqual(get.call_args.kwargs["params"]["after"], "cursor-2")
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertEqual(get.call_args.kwargs["timeout"], (5, 15))

    def test_cyclic_or_incomplete_pagination_fails_closed(self):
        for paging in ({"next": "more"}, {"next": "more", "cursors": {"after": "repeat"}}):
            with self.subTest(paging=paging), patch(TARGET + ".requests.get", return_value=_Resp(
                    {"data": [], "paging": paging})), self.assertRaises(CommandError):
                _owned_flows(BASE, "88", {})

    def test_build_hook_is_api_only_after_migrations_and_explicitly_gated(self):
        root = Path(__file__).resolve().parents[2]
        build = (root / "backend/build.sh").read_text()
        self.assertLess(build.index("python manage.py migrate\n"), build.index("python manage.py deploy_whatsapp_flow"))
        self.assertIn('if [ -n "${WHATSAPP_FLOW_DEPLOY_OPERATION:-}" ]; then\n  python manage.py deploy_whatsapp_flow\nfi', build)
        for filename in ("render.yaml", "render.frankfurt.yaml"):
            self.assertNotIn("deploy_whatsapp_flow", (root / filename).read_text())


@override_settings(**LIVE)
class FlowDeploymentRequestPersistenceTests(TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            PREFIX + "OPERATION": "create-draft", PREFIX + "REQUEST": REQUEST,
            PREFIX + "SHA256": _digest(), PREFIX + "ENDPOINT_URI": ENDPOINT,
            PREFIX + "FLOW_ID": "", PREFIX + "REPLACEMENT_FLOW_ID": "",
        }))
        self.enterContext(patch(TARGET + "._operation_lock", side_effect=lambda _: nullcontext()))
        self.owned = self.enterContext(patch(TARGET + "._owned_flows", return_value=[]))
        self.delegate = self.enterContext(patch(TARGET + ".call_command", side_effect=CommandError("creation outcome unknown")))

    def run_hook(self):
        call_command("deploy_whatsapp_flow", stdout=StringIO(), stderr=StringIO())

    def test_unknown_creation_outcome_never_creates_again_when_inventory_is_empty(self):
        with self.assertRaisesMessage(CommandError, "creation outcome unknown"):
            self.run_hook()
        with self.assertRaisesMessage(CommandError, "no second draft created"):
            self.run_hook()
        self.assertEqual(self.delegate.call_count, 1)

    def test_retry_reuses_visible_draft_after_unknown_creation_response(self):
        with self.assertRaises(CommandError):
            self.run_hook()
        self.owned.return_value = [{"id": "777", "name": NAME, "status": "DRAFT"}]
        self.delegate.side_effect = None
        self.run_hook()
        self.assertEqual(self.delegate.call_count, 2)
        self.assertEqual(self.delegate.call_args.kwargs["flow_id"], "777")
        self.assertNotIn("create_name", self.delegate.call_args.kwargs)

    def test_request_cannot_be_reused_for_a_changed_endpoint(self):
        with self.assertRaises(CommandError):
            self.run_hook()
        with patch.dict(os.environ, {PREFIX + "ENDPOINT_URI": "https://another.example/flow"}), \
                self.assertRaisesMessage(CommandError, "different or unreadable review binding"):
            self.run_hook()
        self.assertEqual(self.delegate.call_count, 1)
