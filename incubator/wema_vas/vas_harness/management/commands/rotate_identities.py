"""Rotate VAS identity ciphertexts after introducing a new primary Fernet key."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ...identity import cipher, decrypt_identity
from ...models import VirtualAccount


class Command(BaseCommand):
    help = "Re-encrypt all validation identities with the primary key; retains no plaintext output."

    def handle(self, *args, **options):
        if settings.VAS_MODE != "validation":
            raise CommandError("Identity rotation is only available in validation mode")
        keys = [key for key in settings.VAS_IDENTITY_KEYS.split(",") if key.strip()]
        if len(keys) < 2:
            raise CommandError("Configure the new primary and at least one previous key before rotating")
        changed = 0
        with transaction.atomic():
            for account in VirtualAccount.objects.select_for_update().filter(mode="validation").order_by("pk"):
                decrypt_identity(account.encrypted_identity)  # Validate before altering the stored identity.
                rotated = cipher().rotate(account.encrypted_identity.encode("ascii")).decode("ascii")
                VirtualAccount.objects.filter(pk=account.pk).update(encrypted_identity=rotated)
                changed += 1
        self.stdout.write(f"Rotated {changed} validation identities. Retain old keys until backups expire.")
