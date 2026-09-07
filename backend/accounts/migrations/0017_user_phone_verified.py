from django.db import migrations, models


def backfill_existing(apps, schema_editor):
    """Every account that exists before this migration proved its phone.

    App signup cannot complete without the SMS OTP, and a WhatsApp signup only
    exists because a message arrived from that number. Adding the field with a
    plain default of False would therefore assert something untrue about every
    current customer — and, because recompute_tier now requires it, would drop
    each of them to the unverified floor the next time their tier was derived.
    Accounts created AFTER this point earn the flag through their own signup.
    """
    User = apps.get_model("accounts", "User")
    User.objects.update(phone_verified=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0016_user_email_verified_user_onboarded_via_whatsapp_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="phone_verified",
            field=models.BooleanField(default=False),
        ),
        # reverse_code is a no-op rather than an error: rolling back should drop
        # the column, not refuse because the data step cannot be undone.
        migrations.RunPython(backfill_existing, migrations.RunPython.noop),
    ]
