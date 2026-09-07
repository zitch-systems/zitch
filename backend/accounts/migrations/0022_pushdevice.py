from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0021_user_pin_lockout_strikes"),
    ]

    operations = [
        migrations.CreateModel(
            name="PushDevice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                            serialize=False, verbose_name="ID")),
                ("token", models.CharField(max_length=255, unique=True)),
                ("device_id", models.CharField(blank=True, db_index=True,
                                                default="", max_length=64)),
                ("platform", models.CharField(blank=True, default="", max_length=16)),
                ("enabled", models.BooleanField(default=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("last_seen", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name="push_devices",
                                           to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
