"""Let concurrent Render builds wait for the API's shared-database migration.

This command never applies migrations. If the API leaves required migrations
unapplied, dependants fail after a bounded wait; a conflicting graph fails immediately.
"""
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, InterfaceError, OperationalError, connections
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = "Wait, read-only and with a deadline, for the API to apply this release's migrations."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--database", default=DEFAULT_DB_ALIAS, choices=tuple(connections))
        parser.add_argument("--timeout", type=int, default=600,
                            help="Maximum polling time in seconds (1–1800; default 600).")
        parser.add_argument("--interval", type=int, default=5,
                            help="Seconds between checks (1–60; default 5).")

    def handle(self, *args, **options):
        timeout, interval = options["timeout"], options["interval"]
        if not 1 <= timeout <= 1800 or not 1 <= interval <= 60:
            raise CommandError("--timeout must be 1–1800 and --interval must be 1–60 seconds.")

        connection = connections[options["database"]]
        original_options = connection.settings_dict["OPTIONS"]
        # The polling deadline must not hide an unbounded database connection or
        # lock wait. These are connection-local settings, restored before exit.
        # An in-flight PostgreSQL probe can run at most five seconds per query.
        if connection.vendor == "postgresql":
            connection.close()
            connection.settings_dict["OPTIONS"] = {
                **original_options,
                "connect_timeout": 5,
                "options": (str(original_options.get("options", ""))
                            + " -c statement_timeout=5000 -c lock_timeout=5000"),
            }
        deadline = time.monotonic() + timeout
        previous_status = None
        try:
            while True:
                try:
                    # A new loader on every pass sees migrations the API has
                    # applied since our preceding poll.
                    executor = MigrationExecutor(connection)
                    executor.loader.check_consistent_history(connection)
                    conflicts = executor.loader.detect_conflicts()
                    if conflicts:
                        raise CommandError("Conflicting migrations in this release; refusing deployment.")
                    pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
                    if not pending:
                        self.stdout.write(self.style.SUCCESS("Shared database migrations are ready."))
                        return
                    status = f"Waiting for API migration: {len(pending)} migration(s) pending."
                except (OperationalError, InterfaceError):
                    # A database restart during deployment may be transient.
                    # Do not print a driver exception that can contain credentials.
                    connection.close()
                    status = "Waiting for the shared database to become available."

                if status != previous_status:
                    self.stdout.write(status)
                    previous_status = status
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CommandError(
                        f"Shared database migrations were not ready after {timeout} seconds; "
                        "deployment blocked. Check the API migration deployment."
                    )
                time.sleep(min(interval, remaining))
        finally:
            if connection.vendor == "postgresql":
                connection.close()
                connection.settings_dict["OPTIONS"] = original_options
