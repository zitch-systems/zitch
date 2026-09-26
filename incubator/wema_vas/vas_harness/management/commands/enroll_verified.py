"""Enroll a bank-approved identity into an independent validation database."""
import json
import re
import secrets
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from ...identity import encrypt_identity
from ...fixtures import CUSTOMERS
from ...models import VirtualAccount


class Command(BaseCommand):
    help = "Read a single verified KYC JSON object on stdin; print only the allocated account number."

    def handle(self, *args, **options):
        if settings.VAS_MODE != "validation" or settings.DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
            raise CommandError("Enrollment requires an isolated validation PostgreSQL database.")
        if sys.stdin.isatty():
            raise CommandError("Provide approved identity details via protected stdin; never use command arguments.")
        raw = sys.stdin.read(4097)
        if len(raw) > 4096:
            raise CommandError("Enrollment input is too large")
        try:
            data = json.loads(raw)
            required = {"customer_reference", "customer_name", "bvn", "nin", "phone",
                        "verification_reference", "consent_reference"}
            if not isinstance(data, dict) or set(data) != required:
                raise ValueError("Missing or extra fields")
            for key in required:
                if not isinstance(data[key], str) or len(data[key]) > 160 or any(ord(c) < 32 for c in data[key]):
                    raise ValueError("Invalid field")
            if not all(data[key].strip() for key in ("customer_reference", "customer_name", "verification_reference", "consent_reference")):
                raise ValueError("Verification and consent evidence are required")
            if not re.fullmatch(r"[A-Za-z0-9._:/-]{8,128}", data["verification_reference"]):
                raise ValueError("Invalid evidence reference")
            if not re.fullmatch(r"[A-Za-z0-9._:/-]{8,128}", data["consent_reference"]):
                raise ValueError("Invalid consent reference")
            name = "Zitch/" + data["customer_name"].strip()
            if len(name) > 160:
                raise ValueError("Account name too long")
            encrypted = encrypt_identity(bvn=data["bvn"], nin=data["nin"], phone=data["phone"])
        except (ValueError, TypeError, KeyError) as exc:
            raise CommandError("Invalid or unverified enrollment payload") from exc
        # The command cannot itself verify an identity. An operator must match
        # these references to an approved verifier and consent record first.
        if VirtualAccount.objects.filter(customer_reference=data["customer_reference"]).exists():
            raise CommandError("Customer reference already enrolled; use an audited update process")
        for _ in range(20):
            number = settings.VAS_PREFIX + f"{secrets.randbelow(10_000_000):07d}"
            if number in CUSTOMERS:
                continue
            try:
                with transaction.atomic():
                    VirtualAccount.objects.create(
                        number=number, mode="validation", customer_reference=data["customer_reference"],
                        display_name=name, encrypted_identity=encrypted,
                        verification_reference=data["verification_reference"],
                        consent_reference=data["consent_reference"],
                        verified_at=timezone.now(),
                    )
                self.stdout.write(number)
                return
            except IntegrityError:
                if VirtualAccount.objects.filter(customer_reference=data["customer_reference"]).exists():
                    raise CommandError("Customer reference already enrolled") from None
        raise CommandError("No unique account number could be allocated")
