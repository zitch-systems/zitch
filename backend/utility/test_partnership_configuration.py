"""Retired account credentials cannot hide among active bill-payment settings."""
import json

from django.test import SimpleTestCase

from utility.partnership_configuration import retired_account_configuration


class PartnershipConfigurationTests(SimpleTestCase):
    def test_retired_settings_are_reported_without_their_values(self):
        report = retired_account_configuration({
            "WEMA_VAS_TOKEN": "private-token", "WEMA_VAS_ENABLED": "",
            "WHATSAPP_FLOW_VAS_ENROLLMENT_ENABLED": "false",
            "WEMA_PARTNERSHIP_RESTORE_VAS": "false", "BANK_ACCOUNT_PROVIDER": "partnership",
        })
        self.assertFalse(report["clean"])
        self.assertEqual(len(report["obsolete_variable_names"]), 5)
        self.assertNotIn("private-token", json.dumps(report))

    def test_partnership_payments_and_historical_key_are_preserved(self):
        report = retired_account_configuration({
            "WEMA_VAS_STATUS_LEGEND": "200=success", "WEMA_VAS_REQUERY_AFTER_SECONDS": "10",
            "WEMA_WALLET_KEY": "bank-secret", "HISTORICAL_FUNDING_IDENTITY_KEYS": "archive-secret",
        })
        self.assertTrue(report["clean"])
        self.assertTrue(report["historical_decryption_key_retained"])
        self.assertNotIn("secret", json.dumps(report))

    def test_unrecognised_retired_namespace_is_detected(self):
        self.assertFalse(retired_account_configuration({"WEMA_VAS_NEW_SWITCH": "true"})["clean"])
