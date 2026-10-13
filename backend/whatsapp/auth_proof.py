"""Credential snapshots used to bind deferred WhatsApp authentication."""
import json

from django.utils.crypto import salted_hmac


def credentials(user):
    value = json.dumps([user.pk, user.phone, user.email, user.password,
                        user.transaction_pin, user.phone_verified, user.email_verified])
    # Keep the established signature domain so a deployment alone does not
    # invalidate an otherwise sound, already-authorised unlock challenge.
    return salted_hmac("whatsapp.vas.identity.credentials.v1", value,
                       algorithm="sha256").hexdigest()
