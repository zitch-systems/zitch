from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("wallet", "0026_bill_funding_immutable")]
    operations = [migrations.AddField(
        model_name="wemafacesession", name="is_account_recovery",
        field=models.BooleanField(default=False),
    )]
