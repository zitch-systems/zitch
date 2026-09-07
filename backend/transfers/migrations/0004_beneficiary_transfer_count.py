from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("transfers", "0003_beneficiary_nickname_beneficiary_saved"),
    ]

    operations = [
        migrations.AddField(
            model_name="beneficiary",
            name="transfer_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
