from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("transfers", "0004_beneficiary_transfer_count"),
    ]

    operations = [
        migrations.AddField(
            model_name="beneficiary",
            name="save_offer_sent",
            field=models.BooleanField(default=False),
        ),
    ]
