"""Remove retired models from Django while older service processes finish."""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("wema_vas", "0004_separate_account_modes"),
                    ("wallet", "0030_detach_retired_funding")]
    operations = [migrations.SeparateDatabaseAndState(state_operations=[
        migrations.DeleteModel(name="Receipt"),
        migrations.DeleteModel(name="MigrationApproval"),
        migrations.DeleteModel(name="VirtualAccount"),
    ])]
