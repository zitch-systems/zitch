"""Static invariants for artifacts that may receive production signing secrets."""
from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]


class AndroidProductionWorkflowTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = (ROOT / ".github/workflows/android-production.yml").read_text(
            encoding="utf-8"
        )
        cls.verifier = (ROOT / "scripts/verify-android-signing.sh").read_text(
            encoding="utf-8"
        )

    def test_production_secrets_cannot_build_an_operator_supplied_ref(self):
        self.assertNotIn("release_ref", self.workflow)
        self.assertIn("ref: refs/heads/main", self.workflow)
        self.assertIn("if: github.ref == 'refs/heads/main'", self.workflow)

    def test_signed_build_requires_core_ci_for_the_exact_checkout(self):
        self.assertIn('release_sha="$(git rev-parse HEAD)"', self.workflow)
        self.assertIn('echo "RELEASE_SHA=$release_sha"', self.workflow)
        for check in ("Backend (Django)", "Backend (PostgreSQL)",
                      "App (Expo)", "Meta connector"):
            self.assertIn(check, self.workflow)
        self.assertIn(".conclusion", self.workflow)

    def test_artifact_signer_is_compared_to_upload_keystore(self):
        self.assertIn("scripts/verify-android-signing.sh", self.workflow)
        self.assertIn("keytool -printcert -jarfile", self.verifier)
        self.assertIn("keytool -exportcert -rfc", self.verifier)
        self.assertIn('artifact_fingerprint" != "$upload_fingerprint', self.verifier)

    def test_release_revision_is_shipped_with_artifact(self):
        self.assertIn("zitch-release-sha.txt", self.workflow)


class BackendPostgreSQLWorkflowTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = (ROOT / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )
        cls.job = cls.workflow.split("  backend-postgres:", 1)[1].split(
            "\n  app:", 1
        )[0]

    def test_postgresql_18_job_uses_the_production_database_engine(self):
        self.assertIn("name: Backend (PostgreSQL)", self.job)
        self.assertIn("image: postgres:18", self.job)
        self.assertIn("DATABASE_URL:", self.job)
        self.assertIn("postgresql://", self.job)
        self.assertNotIn("DJANGO_ALLOW_SQLITE", self.job)

    def test_postgresql_job_checks_migrations_and_runs_the_full_suite(self):
        self.assertIn("python manage.py makemigrations --check --dry-run", self.job)
        self.assertIn("python manage.py migrate --no-input", self.job)
        self.assertIn("python manage.py migrate --check", self.job)
        self.assertIn("run: python manage.py test", self.job)

    def test_postgresql_job_does_not_require_a_ci_redis_service(self):
        self.assertIn('DJANGO_REQUIRE_SHARED_CACHE: "false"', self.job)

    def test_only_the_local_postgresql_service_disables_database_tls(self):
        self.assertIn('DJANGO_DB_SSL: "false"', self.job)
        outside_job = self.workflow.replace(self.job, "")
        self.assertNotIn('DJANGO_DB_SSL: "false"', outside_job)
