"""Provision staff operators for the ops/admin portal.

Operators are ``User(is_staff=True)`` accounts that log in at ``/api/ops/login/``
and ``/api/admin/login/`` with a password; their capabilities come from a Django
Group named after the role (finance / support / read_only) or from
``is_superuser`` (super_admin). Nothing else in the app creates these, so without
this command a fresh deploy has no way into the back office and the e2e harness
can't exercise its ops/admin sections.

Two modes:
  * Create one real operator (works anywhere):
        python manage.py seed_ops --username ada --role finance --password '...'
  * Seed the demo operator set used by e2e_smoke.py (dapo/funmi/...), with a
    password supplied through ZITCH_DEMO_OPERATOR_PASSWORD — blocked whenever
    DEBUG is off, so a production box never gets default-credential operators
    by accident:
        python manage.py seed_ops
"""
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()

ROLES = ["finance", "support", "read_only", "super_admin"]
DEMO_PASSWORD = os.environ.get("ZITCH_DEMO_OPERATOR_PASSWORD", "")
# (username, role) — matches the operators e2e_smoke.py logs into across the ops
# (/api/ops/) and console admin (/api/admin/) sections.
DEMO_OPERATORS = [
    ("amara", "super_admin"),
    ("dapo", "finance"),
    ("funmi", "support"),
    ("ada", "read_only"),
]


class Command(BaseCommand):
    help = "Create staff operators (roles: finance / support / read_only / super_admin)."

    def add_arguments(self, parser):
        parser.add_argument("--username")
        parser.add_argument("--role", choices=ROLES)
        parser.add_argument("--password")
        parser.add_argument("--email")

    def handle(self, *args, **opts):
        if opts.get("username"):
            if not opts.get("password"):
                raise CommandError(
                    "--password is required when creating or updating a named operator."
                )
            self._upsert(opts["username"], opts.get("role") or "read_only",
                         opts["password"],
                         opts.get("email") or f"{opts['username']}@zitch.ng")
            return

        if not settings.DEBUG:
            raise CommandError(
                "Refusing to seed demo operators while DEBUG is off. "
                "Create a real operator with --username/--role/--password."
            )
        if not DEMO_PASSWORD:
            raise CommandError(
                "Set ZITCH_DEMO_OPERATOR_PASSWORD before seeding local demo operators."
            )
        for username, role in DEMO_OPERATORS:
            self._upsert(username, role, DEMO_PASSWORD, f"{username}@zitch.ng")
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(DEMO_OPERATORS)} demo operators."))

    def _upsert(self, username, role, password, email):
        # phone stays NULL. It used to be fabricated from `hash(username)`, which
        # was both non-deterministic (Python salts str hashes per process, so a
        # re-deploy invented a different number) and a real hazard: the value
        # looked like a Nigerian mobile and `phone` is unique, so colliding with
        # a customer's number raised IntegrityError inside build.sh — which runs
        # under `set -o errexit`, failing the whole deploy and leaving the
        # previous release serving. Operators sign in by username or email, and
        # the column is nullable (Postgres permits many NULLs under UNIQUE).
        user, created = User.objects.get_or_create(
            username=username, defaults={"email": email, "phone": None},
        )
        user.is_staff = True
        user.is_active = True
        # Role updates replace privileges instead of accumulating them. Without
        # this, downgrading a super-admin left is_superuser=True and moving an
        # operator between groups kept the old role's capabilities.
        user.is_superuser = role == "super_admin"
        user.set_password(password)
        user.save()
        user.groups.clear()
        if role != "super_admin":
            group, _ = Group.objects.get_or_create(name=role)
            user.groups.add(group)
        self.stdout.write(f"  {'created' if created else 'updated'} operator '{username}' ({role})")

