"""The worker reports its selected public Flow contract without calling Meta."""
import hashlib
import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from whatsapp.management.commands.publish_flow import ASSET


TARGET = "whatsapp.management.commands.whatsapp_worker"


class WorkerStartupDiagnosticTests(SimpleTestCase):
    @override_settings(WHATSAPP_FLOW={"FLOW_ID": "777", "PRIVATE_KEY": "secret-private-key"})
    def test_startup_reports_numeric_selection_and_canonical_contract_without_network(self):
        digest = hashlib.sha256(json.dumps(json.loads(ASSET.read_bytes()), sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()
        output = StringIO()
        with patch(TARGET + ".process_once", return_value=(0, 0)), \
                patch("requests.sessions.Session.request", side_effect=AssertionError("no Graph call")) as network:
            call_command("whatsapp_worker", "--once", stdout=output)
        self.assertIn(f"whatsapp_worker_flow flow_id=777 contract_sha256={digest}", output.getvalue())
        self.assertNotIn("secret-private-key", output.getvalue())
        network.assert_not_called()

    @override_settings(WHATSAPP_FLOW={"FLOW_ID": "accidentally-pasted-secret"})
    def test_malformed_identifier_and_unreadable_asset_never_print_private_values(self):
        output = StringIO()
        with patch(TARGET + ".process_once", return_value=(0, 0)), \
                patch(TARGET + ".Path.read_bytes", side_effect=OSError("private filesystem detail")):
            call_command("whatsapp_worker", "--once", stdout=output)
        self.assertIn("flow_id=unset contract_sha256=unreadable", output.getvalue())
        self.assertNotIn("accidentally-pasted-secret", output.getvalue())
        self.assertNotIn("private filesystem detail", output.getvalue())
