from django.db import migrations


def flush_tokens(apps, schema_editor):
    """AccessToken now stores only the SHA-256 hash of the token, so any rows
    written before this change hold a plaintext key that can no longer resolve
    (and shouldn't sit in the DB as a usable credential). Delete them; affected
    users simply sign in again."""
    AccessToken = apps.get_model("accounts", "AccessToken")
    AccessToken.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_user_user_email_lower_idx"),
    ]

    operations = [
        migrations.RunPython(flush_tokens, migrations.RunPython.noop),
    ]
