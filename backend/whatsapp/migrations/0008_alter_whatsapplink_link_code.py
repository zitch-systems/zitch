from django.db import migrations, models


def disable_unconsented_ai(apps, schema_editor):
    """Existing users never made an explicit third-party-AI opt-in choice."""
    apps.get_model("whatsapp", "WhatsAppLink").objects.update(ai_enabled=False)


class Migration(migrations.Migration):
    dependencies = [("whatsapp", "0007_approvalrequest")]

    operations = [
        migrations.AddField(
            model_name="wamessagelog",
            name="processed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="wamessagelog",
            name="processing_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="wamessagelog",
            name="processing_error",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="wamessagelog",
            name="processing_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="whatsapplink",
            name="ai_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="whatsapplink",
            name="link_code",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.RunPython(disable_unconsented_ai, migrations.RunPython.noop),
    ]
