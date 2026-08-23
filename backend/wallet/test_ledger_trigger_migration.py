"""Contract tests for the PostgreSQL ledger guard migration."""

import importlib
from types import SimpleNamespace

from django.test import SimpleTestCase


migration = importlib.import_module(
    "wallet.migrations.0017_ledger_database_immutability"
)


class _Ops:
    @staticmethod
    def quote_name(value):
        return f'"{value}"'


class _SchemaEditor:
    def __init__(self, vendor="postgresql"):
        self.connection = SimpleNamespace(vendor=vendor, ops=_Ops())
        self.statements = []

    def execute(self, statement):
        self.statements.append(" ".join(statement.split()))


class LedgerTriggerMigrationTests(SimpleTestCase):
    def test_postgres_guard_blocks_core_updates_and_deletes(self):
        editor = _SchemaEditor()
        migration.install_guard(None, editor)
        sql = "\n".join(editor.statements)
        self.assertIn("BEFORE UPDATE OR DELETE", sql)
        self.assertIn("NEW.user_id IS DISTINCT FROM OLD.user_id", sql)
        self.assertIn("NEW.service IS DISTINCT FROM OLD.service", sql)
        self.assertIn("NEW.amount IS DISTINCT FROM OLD.amount", sql)
        self.assertIn("NEW.direction IS DISTINCT FROM OLD.direction", sql)
        self.assertIn("NEW.currency IS DISTINCT FROM OLD.currency", sql)
        self.assertIn("NEW.reference IS DISTINCT FROM OLD.reference", sql)
        self.assertIn("NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key", sql)
        self.assertIn("NEW.created IS DISTINCT FROM OLD.created", sql)
        self.assertIn("TG_OP = 'DELETE'", sql)

    def test_non_postgres_database_is_left_portable(self):
        editor = _SchemaEditor(vendor="sqlite")
        migration.install_guard(None, editor)
        self.assertEqual(editor.statements, [])

    def test_reverse_removes_trigger_before_function(self):
        editor = _SchemaEditor()
        migration.remove_guard(None, editor)
        self.assertIn("DROP TRIGGER IF EXISTS", editor.statements[0])
        self.assertIn("DROP FUNCTION IF EXISTS", editor.statements[1])
