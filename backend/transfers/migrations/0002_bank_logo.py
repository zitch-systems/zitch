from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("transfers", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="bank",
            name="logo",
            field=models.URLField(blank=True, default="", max_length=300),
        ),
        migrations.AddField(
            model_name="bank",
            name="popular",
            field=models.BooleanField(default=False),
        ),
    ]
