from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0027_user_privacy_consent_at_user_privacy_consent_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="spend_namespace",
            # Keep NULL valid until every writer is running code that knows the
            # column. Render runs migrations while the previous release still
            # serves, and Django application defaults are not database defaults.
            # Do not put uuid.uuid4 here: AddField evaluates a callable default
            # once while altering an existing table, which would try to assign
            # the same value to every legacy row before UNIQUE is created.
            field=models.UUIDField(editable=False, null=True, unique=True),
        ),
    ]
