from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("whatsapp", "0008_alter_whatsapplink_link_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="wamessagelog",
            name="processing_payload",
            field=models.BinaryField(blank=True, default=bytes, editable=False),
        ),
        migrations.AddField(
            model_name="wamessagelog",
            name="next_attempt_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="broadcast",
            name="approval_request",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="broadcast",
                to="whatsapp.approvalrequest",
            ),
        ),
        migrations.AddField(
            model_name="broadcast",
            name="count_unknown",
            field=models.IntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="broadcast",
            name="status",
            field=models.CharField(
                choices=[("draft", "draft"), ("queued", "queued"),
                         ("sending", "sending"), ("done", "done")],
                default="draft",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="broadcastrecipient",
            name="processing_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="broadcastrecipient",
            name="processed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="broadcastrecipient",
            name="processing_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="broadcastrecipient",
            name="next_attempt_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="broadcastrecipient",
            constraint=models.UniqueConstraint(
                fields=("broadcast", "wa_msisdn"), name="uniq_broadcast_recipient",
            ),
        ),
    ]
