from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class CardUniquenessPreflightTests(SimpleTestCase):
    @patch(
        "cards.management.commands.audit_card_uniqueness.Command._table_exists",
        return_value=False,
    )
    def test_new_database_without_card_table_is_safe(self, _table_exists):
        stdout = StringIO()

        call_command("audit_card_uniqueness", stdout=stdout)

        self.assertIn("not created yet", stdout.getvalue())

    @patch(
        "cards.management.commands.audit_card_uniqueness.Command._duplicate_groups",
        return_value=[],
    )
    @patch(
        "cards.management.commands.audit_card_uniqueness.Command._table_exists",
        return_value=True,
    )
    def test_clean_database_passes(self, _table_exists, _duplicate_groups):
        stdout = StringIO()

        call_command("audit_card_uniqueness", stdout=stdout)

        self.assertIn("preflight passed", stdout.getvalue())

    @patch(
        "cards.management.commands.audit_card_uniqueness.Command._duplicate_groups",
        return_value=[{"user_id": 17, "card_count": 2}],
    )
    @patch(
        "cards.management.commands.audit_card_uniqueness.Command._table_exists",
        return_value=True,
    )
    def test_duplicates_abort_without_exposing_card_secrets(
        self, _table_exists, _duplicate_groups,
    ):
        stderr = StringIO()

        with self.assertRaisesRegex(CommandError, "Do not apply cards migration 0004"):
            call_command("audit_card_uniqueness", stderr=stderr)

        output = stderr.getvalue()
        self.assertIn("user_id=17 rows=2", output)
        self.assertNotIn("card_token", output)
        self.assertNotIn("last4", output)
