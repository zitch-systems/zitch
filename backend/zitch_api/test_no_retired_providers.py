"""9PSB and Wema VAS (virtual accounts) were deleted for good on 2026-11-12.

This fails if their code, Django app or management commands come back. The
only permitted mentions are guards that refuse retired numbers and labels, and
historical migrations that cannot be rewritten.
"""
import re
from pathlib import Path

from django.apps import apps
from django.test import SimpleTestCase

BACKEND = Path(__file__).resolve().parents[1]
ALLOWED = {
    "wallet/partnership.py",          # refuses 9PSB/Wema VAS labels and 711 numbers
    "wallet/migrations/0034_purge_retired_providers.py",
}


class NoRetiredProviderTests(SimpleTestCase):
    def test_no_retired_app_or_package(self):
        self.assertFalse((BACKEND / "banking").exists())
        self.assertNotIn("banking", {a.label for a in apps.get_app_configs()})

    def test_no_retired_management_commands(self):
        for name in ("export_previous_bank_accounts", "restore_archived_bank_accounts",
                     "wema_cutover_audit", "partnership_recovery_worksheet",
                     "reconcile_archived_balances"):
            self.assertEqual(list(BACKEND.glob(f"*/management/commands/{name}.py")), [], name)

    def test_9psb_is_named_only_by_the_guard_that_refuses_it(self):
        offenders = []
        for path in BACKEND.rglob("*.py"):
            rel = path.relative_to(BACKEND).as_posix()
            if "/migrations/" in rel or rel in ALLOWED or path.name.startswith("test"):
                continue
            if re.search(r"9\s?psb", path.read_text(errors="ignore"), re.IGNORECASE):
                offenders.append(rel)
        self.assertEqual(offenders, [])

    def test_no_code_reads_retired_tables(self):
        pattern = re.compile(r"\b(banking_[a-z]+|wema_vas_virtualaccount)\b")
        offenders = []
        for path in BACKEND.rglob("*.py"):
            rel = path.relative_to(BACKEND).as_posix()
            if "/migrations/" in rel and rel not in ALLOWED:
                continue  # applied history; the 0034 purge drops the tables
            if rel in ALLOWED or path.name.startswith("test"):
                continue  # migration tests build the old tables on purpose
            if pattern.search(path.read_text(errors="ignore")):
                offenders.append(rel)
        self.assertEqual(offenders, [])
