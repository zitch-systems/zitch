import hashlib
import hmac

from django.conf import settings
from django.db import migrations, models


def _hash(value):
    if not value:
        return ""
    return hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def migrate_identifiers(apps, schema_editor):
    """Replace any stored raw BVN/NIN with a keyed hash + last 4 BEFORE the raw
    columns are dropped, so existing records keep an audit hash and the last
    digits but no recoverable plaintext."""
    User = apps.get_model("accounts", "User")
    for u in User.objects.all().iterator():
        bvn = getattr(u, "bvn", "") or ""
        nin = getattr(u, "nin", "") or ""
        if not bvn and not nin:
            continue
        if bvn:
            u.bvn_hash = _hash(bvn)
            u.bvn_last4 = bvn[-4:]
        if nin:
            u.nin_hash = _hash(nin)
            u.nin_last4 = nin[-4:]
        u.save(update_fields=["bvn_hash", "bvn_last4", "nin_hash", "nin_last4"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_flush_legacy_plaintext_tokens"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="bvn_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="user",
            name="bvn_last4",
            field=models.CharField(blank=True, default="", max_length=4),
        ),
        migrations.AddField(
            model_name="user",
            name="nin_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="user",
            name="nin_last4",
            field=models.CharField(blank=True, default="", max_length=4),
        ),
        # Backfill from the raw columns while they still exist, then drop them.
        migrations.RunPython(migrate_identifiers, migrations.RunPython.noop),
        migrations.RemoveField(model_name="user", name="bvn"),
        migrations.RemoveField(model_name="user", name="nin"),
    ]
