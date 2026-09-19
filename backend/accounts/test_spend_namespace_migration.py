"""The spend namespace migration must be safe for an existing user table."""
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.db import connections, router
from django.db.migrations.executor import MigrationExecutor


class SpendNamespaceMigrationTests(unittest.TestCase):
    def test_multiple_legacy_users_receive_distinct_namespaces(self):
        alias = f"namespace_migration_{uuid.uuid4().hex}"
        with tempfile.TemporaryDirectory() as directory:
            database = dict(settings.DATABASES["default"])
            database.update({
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(Path(directory) / "migration.sqlite3"),
            })
            database["TEST"] = dict(database.get("TEST") or {})
            connections.databases[alias] = database
            connection = connections[alias]
            try:
                # Several older RunPython migrations omitted `.using()` and
                # therefore consult Django's router. Route their historical
                # models to this isolated database while building the fixture.
                with patch.object(router, "db_for_read", return_value=alias), \
                        patch.object(router, "db_for_write", return_value=alias):
                    executor = MigrationExecutor(connection)
                    executor.migrate([
                        ("accounts", "0027_user_privacy_consent_at_user_privacy_consent_version"),
                    ])
                    legacy_apps = executor.loader.project_state([
                        ("accounts", "0027_user_privacy_consent_at_user_privacy_consent_version"),
                    ]).apps
                    LegacyUser = legacy_apps.get_model("accounts", "User")
                    LegacyUser.objects.using(alias).create(username="legacy-one")
                    LegacyUser.objects.using(alias).create(username="legacy-two")

                    # The schema migration itself must not try to stamp one
                    # default UUID onto both rows under the UNIQUE constraint.
                    executor = MigrationExecutor(connection)
                    executor.migrate([("accounts", "0028_user_spend_namespace")])
                    interim_apps = executor.loader.project_state([
                        ("accounts", "0028_user_spend_namespace"),
                    ]).apps
                    InterimUser = interim_apps.get_model("accounts", "User")
                    self.assertEqual(
                        InterimUser.objects.using(alias).filter(
                            spend_namespace__isnull=True,
                        ).count(),
                        2,
                    )

                    executor = MigrationExecutor(connection)
                    executor.migrate([("accounts", "0029_backfill_spend_namespace")])
                    current_apps = executor.loader.project_state([
                        ("accounts", "0029_backfill_spend_namespace"),
                    ]).apps
                    CurrentUser = current_apps.get_model("accounts", "User")
                    namespaces = list(CurrentUser.objects.using(alias).order_by("pk")
                                      .values_list("spend_namespace", flat=True))
                    self.assertEqual(len(namespaces), 2)
                    self.assertTrue(all(namespaces))
                    self.assertEqual(len(set(namespaces)), 2)
                    self.assertTrue(
                        CurrentUser._meta.get_field("spend_namespace").has_default()
                    )
            finally:
                connection.close()
                connections.databases.pop(alias, None)
