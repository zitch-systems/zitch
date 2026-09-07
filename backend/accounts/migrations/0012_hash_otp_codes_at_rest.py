import hashlib
import hmac

from django.conf import settings
from django.db import migrations, models


def _hash_code(raw: str) -> str:
    key = getattr(settings, "OTP_HASH_KEY", "") or settings.SECRET_KEY
    return hmac.new(key.encode(), (raw or "").encode(), hashlib.sha256).hexdigest()


def hash_existing_codes(apps, schema_editor):
    """Hash any codes still on disk so in-flight OTPs keep verifying after deploy.

    Codes expire in 10 minutes and are single-use, so in practice almost nothing
    is live at deploy time — but hashing them (rather than dropping them) means a
    code sent seconds before the migration still works."""
    OTP = apps.get_model("accounts", "OTP")
    for otp in OTP.objects.all().iterator():
        otp.code_hash = _hash_code(otp.code)
        otp.save(update_fields=["code_hash"])


def noop_reverse(apps, schema_editor):
    # The raw code is not recoverable from the hash; reversing just leaves code
    # blank (the field is being re-added empty), which is acceptable — any such
    # OTP has long since expired.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_accesstoken_scope"),
    ]

    operations = [
        migrations.AddField(
            model_name="otp",
            name="code_hash",
            field=models.CharField(default="", max_length=64),
            preserve_default=False,
        ),
        migrations.RunPython(hash_existing_codes, noop_reverse),
        migrations.RemoveField(
            model_name="otp",
            name="code",
        ),
    ]
