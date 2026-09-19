"""Boot-time database guards: a money ledger must never fall back to SQLite."""
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROBE = (
    "from django.conf import settings; "
    "print(settings.DATABASES['default']['ENGINE'])"
)


class DatabaseSettingsTests(SimpleTestCase):
    def run_settings(self, **extra):
        env = os.environ.copy()
        for key in (
            "DATABASE_URL", "DJANGO_ALLOW_SQLITE", "DJANGO_DEBUG", "CI",
            "GITHUB_ACTIONS",
            "RENDER", "RENDER_EXTERNAL_HOSTNAME",
        ):
            env.pop(key, None)
        env.update({
            "DJANGO_SETTINGS_MODULE": "zitch_api.settings",
            "DJANGO_SECRET_KEY": "test-only-strong-database-settings-secret-4fJ9xQ2mN7",
            "DJANGO_REQUIRE_SHARED_CACHE": "false",
            "DJANGO_ALLOWED_HOSTS": "testserver",
            "WHATSAPP_MODE": "disabled",
            **extra,
        })
        return subprocess.run(
            [sys.executable, "-c", PROBE], cwd=BACKEND_ROOT, env=env,
            capture_output=True, text=True, check=False,
        )

    def test_production_refuses_missing_database_url(self):
        result = self.run_settings(DJANGO_DEBUG="false")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DATABASE_URL is required", result.stderr)

    def test_production_refuses_explicit_sqlite_database_url(self):
        result = self.run_settings(
            DJANGO_DEBUG="false", DATABASE_URL="sqlite:////tmp/not-production.sqlite3",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must use PostgreSQL", result.stderr)

    def test_production_refuses_other_non_postgres_database_schemes(self):
        result = self.run_settings(
            DJANGO_DEBUG="false",
            DATABASE_URL="mysql://user:password@db.internal:3306/zitch",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must use PostgreSQL", result.stderr)

    def test_sqlite_override_is_not_a_production_escape_hatch(self):
        result = self.run_settings(
            DJANGO_DEBUG="false", DJANGO_ALLOW_SQLITE="true",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DATABASE_URL is required", result.stderr)

    def test_local_sqlite_requires_debug_and_explicit_override(self):
        without_override = self.run_settings(DJANGO_DEBUG="true")
        self.assertNotEqual(without_override.returncode, 0)

        allowed = self.run_settings(
            DJANGO_DEBUG="true", DJANGO_ALLOW_SQLITE="true",
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn("django.db.backends.sqlite3", allowed.stdout)

    def test_ci_can_explicitly_exercise_sqlite_with_debug_off(self):
        result = self.run_settings(
            DJANGO_DEBUG="false", GITHUB_ACTIONS="true",
            DJANGO_ALLOW_SQLITE="true",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("django.db.backends.sqlite3", result.stdout)

    def test_render_rejects_sqlite_even_with_ci_override_markers(self):
        result = self.run_settings(
            DJANGO_DEBUG="false", RENDER="true", GITHUB_ACTIONS="true",
            DJANGO_ALLOW_SQLITE="true",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("never allowed on Render", result.stderr)

    def test_production_accepts_postgresql(self):
        result = self.run_settings(
            DJANGO_DEBUG="false",
            DATABASE_URL="postgresql://user:password@db.internal:5432/zitch",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("django.db.backends.postgresql", result.stdout)
