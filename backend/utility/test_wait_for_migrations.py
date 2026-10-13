"""A concurrent worker build waits for the API without becoming a migrator."""
from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError
from django.db.migrations.exceptions import InconsistentMigrationHistory
from django.test import SimpleTestCase


MODULE = "utility.management.commands.wait_for_migrations"


class WaitForMigrationsTests(SimpleTestCase):
    def setUp(self):
        self.connection = Mock(vendor="postgresql", settings_dict={
            "OPTIONS": {"options": "-c search_path=public", "sslmode": "require"},
        })
        self.original_options = self.connection.settings_dict["OPTIONS"]
        self.connections = patch(f"{MODULE}.connections", {"default": self.connection}).start()
        self.executor = patch(f"{MODULE}.MigrationExecutor").start()
        self.sleep = patch(f"{MODULE}.time.sleep").start()
        self.clock = patch(f"{MODULE}.time.monotonic", return_value=0).start()
        self.addCleanup(patch.stopall)
        self.output = StringIO()

    def loader(self, pending=()):
        executor = Mock()
        executor.loader.detect_conflicts.return_value = {}
        executor.migration_plan.return_value = list(pending)
        return executor

    def run_wait(self, **options):
        call_command("wait_for_migrations", stdout=self.output, **options)

    def test_refreshes_applied_migrations_until_api_finishes_without_writing(self):
        before, after = self.loader([("wallet", "0050")]), self.loader()
        self.executor.side_effect = [before, after]
        self.run_wait()
        self.assertEqual(self.executor.call_count, 2)
        self.sleep.assert_called_once_with(5)
        for executor in (before, after):
            executor.loader.check_consistent_history.assert_called_once_with(self.connection)
            executor.migration_plan.assert_called_once_with(executor.loader.graph.leaf_nodes())
            executor.migrate.assert_not_called()
            executor.recorder.ensure_schema.assert_not_called()
        self.assertIn("1 migration(s) pending", self.output.getvalue())
        self.assertIn("migrations are ready", self.output.getvalue())

    def test_failed_api_deploy_has_a_deadline_and_blocks_worker(self):
        self.executor.return_value = self.loader([("wallet", "0050")])
        self.clock.side_effect = [0, 4, 10]
        with self.assertRaisesMessage(CommandError, "deployment blocked"):
            self.run_wait(timeout=10, interval=8)
        self.sleep.assert_called_once_with(6)
        self.assertEqual(self.executor.call_count, 2)
        self.assertNotIn("migrations are ready", self.output.getvalue())

    def test_transient_database_failure_reconnects_without_exposing_secrets(self):
        self.executor.side_effect = [OperationalError("password=do-not-log"), self.loader()]
        self.run_wait()
        self.assertEqual(self.connection.close.call_count, 3)  # initial, retry, final
        self.assertIn("become available", self.output.getvalue())
        self.assertNotIn("do-not-log", self.output.getvalue())

    def test_conflicting_migrations_fail_immediately(self):
        executor = self.loader()
        executor.loader.detect_conflicts.return_value = {"wallet": ["0050_a", "0050_b"]}
        self.executor.return_value = executor
        with self.assertRaisesMessage(CommandError, "Conflicting migrations"):
            self.run_wait()
        self.sleep.assert_not_called()
        executor.migration_plan.assert_not_called()

    def test_inconsistent_history_is_not_retried_as_a_deployment_race(self):
        executor = self.loader()
        executor.loader.check_consistent_history.side_effect = InconsistentMigrationHistory("bad order")
        self.executor.return_value = executor
        with self.assertRaises(InconsistentMigrationHistory):
            self.run_wait()
        self.sleep.assert_not_called()

    def test_database_timeouts_are_local_and_original_options_are_restored(self):
        def inspect_options(connection):
            opts = connection.settings_dict["OPTIONS"]
            self.assertEqual(opts["connect_timeout"], 5)
            self.assertEqual(opts["sslmode"], "require")
            self.assertEqual(opts["options"], "-c search_path=public -c statement_timeout=5000 -c lock_timeout=5000")
            return self.loader()

        self.executor.side_effect = inspect_options
        self.run_wait()
        self.assertIs(self.connection.settings_dict["OPTIONS"], self.original_options)

    def test_connection_options_restore_on_failure_too(self):
        self.executor.side_effect = RuntimeError("bad migration module")
        with self.assertRaises(RuntimeError):
            self.run_wait()
        self.assertIs(self.connection.settings_dict["OPTIONS"], self.original_options)

    def test_unbounded_or_busy_polling_arguments_are_rejected(self):
        for options in ({"timeout": 0}, {"timeout": 1801}, {"interval": 0}, {"interval": 61}):
            with self.subTest(options=options), self.assertRaises(CommandError):
                self.run_wait(**options)
        self.executor.assert_not_called()
