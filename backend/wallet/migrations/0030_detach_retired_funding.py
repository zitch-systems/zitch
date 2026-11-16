"""Retired: detached the bill funding link to the removed wema_vas tombstone app.

Applied everywhere before the tombstone app was deleted. Kept as a no-op so the
migration graph and every database's applied history stay consistent; migration
0025 no longer creates the retired ``vas_account`` field, so there is nothing to
detach on a fresh database.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("wallet", "0029_retired_funding_archive")]
    operations = []
