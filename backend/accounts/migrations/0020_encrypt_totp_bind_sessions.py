import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import migrations, models


PREFIX = "enc:v1:"


def _encrypt_existing_totp(apps, schema_editor):
    materials = getattr(settings, "TOTP_ENCRYPTION_KEYS", None) or [settings.SECRET_KEY]
    if isinstance(materials, str):
        materials = [value.strip() for value in materials.split(",") if value.strip()]
    material = str(materials[0] if materials else settings.SECRET_KEY)
    digest = hashlib.sha256(b"zitch.operator-totp.v1\0" + material.encode()).digest()
    fernet = Fernet(base64.urlsafe_b64encode(digest))
    OperatorTotp = apps.get_model("accounts", "OperatorTotp")
    for row in OperatorTotp.objects.exclude(secret="").iterator():
        if row.secret.startswith(PREFIX):
            continue
        row.secret = PREFIX + fernet.encrypt(row.secret.encode()).decode()
        row.save(update_fields=["secret"])


class Migration(migrations.Migration):
    dependencies = [("accounts", "0019_flag_short_pins_for_reset")]

    operations = [
        migrations.AddField(
            model_name="accesstoken",
            name="device_id",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AlterField(
            model_name="operatortotp",
            name="secret",
            field=models.CharField(max_length=255),
        ),
        migrations.RunPython(_encrypt_existing_totp, migrations.RunPython.noop),
    ]
