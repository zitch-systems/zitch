"""Build-time reports remain read-only, secret-free and separate from release."""
import json
import re
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings

from wema_vas.management.commands.vas_deployment_diagnostics import deployment_report

MODULE = "wema_vas.management.commands.vas_deployment_diagnostics"
SAFE_READINESS = {"local_ready": False, "full_go_live_ready": False, "status": "blocked_local_requirements"}
SECRET = "private-value-never-print-this"


@override_settings(
    WEMA_VAS={"MODE": "live", "COLLECTION_ACCOUNT": "1234567890", "TOKEN": SECRET},
    BANK_ACCOUNT_PROVIDER="wema_vas", WEMA_PARTNERSHIP_MODE="archive", KYC_PROVIDER="prembly", VAS_PROVIDER="wema",
    WEMA_BILLER_MODE="active", WEMA_VAS_BILLER_ENABLED=True, WEMA_VAS_BILLER_SOURCE_ACCOUNT="1234567890",
    WEMA_VAS_BILLER_APPROVAL_REFERENCE=SECRET,
    PREMBLY={"API_KEY": SECRET, "APP_ID": SECRET},
    TERMII={"API_KEY": SECRET, "SENDER_ID": "private-sender"}, WEMA={"SIMULATION": False},
    WHATSAPP={"MODE": "live", "TOKEN": SECRET, "PHONE_NUMBER_ID": "private-phone-id"},
    WHATSAPP_FLOW={"FLOW_ID": "private-flow-id", "PRIVATE_KEY": SECRET, "PRIVATE_KEY_PASSPHRASE": SECRET,
                   "VAS_APPROVED_FLOW_ID": "private-flow-id", "VAS_ENROLLMENT_ENABLED": True},
)
class DeploymentDiagnosticsTests(TestCase):
    def test_command_prints_presence_only_and_exits_success_despite_pending_readiness(self):
        output = StringIO()
        with patch(MODULE + ".readiness_report", return_value=SAFE_READINESS):
            call_command("vas_deployment_diagnostics", stdout=output)
        raw = output.getvalue()
        result = json.loads(raw)
        self.assertEqual(result["status"], "inspection_completed")
        self.assertFalse(result["release_authorization"])
        self.assertFalse(result["full_go_live_ready"])
        self.assertFalse(result["vas_readiness"]["local_ready"])
        values = result["configuration"]
        self.assertEqual(values["account_provider"], "wema_vas")
        self.assertEqual(values["kyc_provider"], "prembly")
        self.assertTrue(values["prembly_credentials_configured"])
        self.assertTrue(values["termii_configuration_ready"])
        self.assertTrue(values["vas_biller_source_matches_collection"])
        self.assertTrue(values["flow_configured"])
        self.assertTrue(values["flow_current_matches_approved"])
        for secret in (SECRET, "1234567890", "private-flow-id", "private-phone-id", "private-sender"):
            self.assertNotIn(secret, raw)

    def test_invalid_provider_configuration_is_not_reflected_into_build_logs(self):
        with override_settings(KYC_PROVIDER=SECRET, BANK_ACCOUNT_PROVIDER=SECRET,
                               WEMA_PARTNERSHIP_MODE=SECRET, WEMA_BILLER_MODE=SECRET, VAS_PROVIDER=SECRET), \
                patch(MODULE + ".readiness_report", return_value=SAFE_READINESS):
            result = deployment_report()
        for key in ("account_provider", "partnership_mode", "kyc_provider_setting", "biller_provider", "biller_mode"):
            self.assertEqual(result["configuration"][key], "invalid")
        self.assertNotIn(SECRET, json.dumps(result))

    def test_diagnostics_use_top_level_biller_policy_and_never_echo_reference_or_account(self):
        with override_settings(WEMA_VAS_BILLER_ENABLED=False, WEMA_VAS_BILLER_SOURCE_ACCOUNT="0987654321",
                               WEMA_VAS_BILLER_APPROVAL_REFERENCE="pending"), \
                patch(MODULE + ".readiness_report", return_value=SAFE_READINESS):
            result = deployment_report()
        values = result["configuration"]
        self.assertFalse(values["vas_biller_enabled"])
        self.assertTrue(values["vas_biller_source_present"])
        self.assertFalse(values["vas_biller_source_matches_collection"])
        self.assertFalse(values["vas_biller_approval_reference_present"])
        self.assertNotIn("0987654321", json.dumps(result))

    def test_simulation_and_missing_credentials_do_not_look_live(self):
        with override_settings(PREMBLY={"API_KEY": SECRET, "APP_ID": ""}, TERMII={"API_KEY": "", "SENDER_ID": "Zitch"},
                               WEMA={"SIMULATION": True}), patch(MODULE + ".readiness_report", return_value=SAFE_READINESS):
            values = deployment_report()["configuration"]
        self.assertTrue(values["simulation"])
        self.assertFalse(values["prembly_credentials_configured"])
        self.assertFalse(values["prembly_live_configuration_ready"])
        self.assertFalse(values["termii_configuration_ready"])

    def test_flow_id_mismatch_and_disabled_channel_are_reported_without_flow_lookups(self):
        with override_settings(WHATSAPP={"MODE": "disabled", "TOKEN": SECRET, "PHONE_NUMBER_ID": "id"},
                               WHATSAPP_FLOW={"FLOW_ID": "current-private-id", "PRIVATE_KEY": SECRET,
                                              "VAS_APPROVED_FLOW_ID": "different-private-id", "VAS_ENROLLMENT_ENABLED": True}), \
                patch(MODULE + ".readiness_report", return_value=SAFE_READINESS):
            result = deployment_report()
        self.assertFalse(result["configuration"]["flow_configured"])
        self.assertFalse(result["configuration"]["flow_current_matches_approved"])
        self.assertNotIn("current-private-id", json.dumps(result))
        self.assertNotIn("different-private-id", json.dumps(result))

    def test_unexpected_inspection_failure_returns_fixed_redacted_result_and_exit_zero(self):
        output = StringIO()
        with patch(MODULE + ".readiness_report", side_effect=RuntimeError("credential=" + SECRET)):
            call_command("vas_deployment_diagnostics", stdout=output)
        self.assertEqual(json.loads(output.getvalue())["status"], "inspection_unavailable")
        self.assertNotIn(SECRET, output.getvalue())

    def test_real_inspection_makes_no_network_calls_and_no_database_writes(self):
        observed = []
        def read_only(execute, sql, params, many, context):
            observed.append(sql)
            self.assertIsNone(re.match(r"\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE)\b", sql, re.I))
            return execute(sql, params, many, context)
        with connection.execute_wrapper(read_only), patch("requests.sessions.Session.request") as network:
            result = deployment_report()
        self.assertTrue(result["read_only"])
        self.assertTrue(observed)
        network.assert_not_called()
