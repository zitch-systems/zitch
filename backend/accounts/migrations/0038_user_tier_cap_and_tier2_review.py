from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0037_login_challenge_face_purpose"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="tier_cap",
            field=models.PositiveSmallIntegerField(default=3),
        ),
        migrations.AddField(
            model_name="user",
            name="tier2_review",
            field=models.CharField(blank=True, default="", max_length=12),
        ),
        migrations.AddField(
            model_name="user",
            name="tier2_review_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="tier3_review",
            field=models.CharField(blank=True, default="", max_length=12),
        ),
        migrations.AddField(
            model_name="user",
            name="tier3_review_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
