from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("wallet", "0019_wallet_identity_upgrade_required")]

    operations = [
        migrations.AddField(
            model_name="wemafacesession", name="account_state",
            field=models.CharField(max_length=24, default="unknown", choices=[
                (value, value) for value in
                ("unknown", "awaiting_callback", "rejected", "review_required")])),
        migrations.AddField(
            model_name="wemafacesession", name="account_failure_category",
            field=models.CharField(max_length=48, blank=True, default="")),
        migrations.AddField(
            model_name="wemafacesession", name="account_http_status",
            field=models.PositiveSmallIntegerField(null=True, blank=True)),
    ]
