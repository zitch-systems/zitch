"""Drop the exam-PIN and betting-platform catalogues with their apps.

Both products were purchased through the 9PSB VAS rail. That rail is gone, so
the catalogues describe products nobody can buy, and the `exams` and `betting`
apps that owned these tables have been deleted.

The tables are dropped unconditionally, which is deliberately UNLIKE
`wallet/0031_drop_retired_account_schema`. That migration refused to run on
non-empty tables because its subject was financial evidence: receipts, approvals
and virtual accounts whose loss would have broken an audit trail. These two hold
seed catalogue data only -- a code, a display name, a price, an active flag --
written by `manage.py seed_plans` and referenced by nothing. Nothing points at
them: a purchase recorded the exam code inside `Transaction.meta`, never a
foreign key, so no ledger row loses a reference when they go.

The money those products moved is untouched. Its evidence lives in
`wallet_transaction`, which this does not read or alter.

It lives in `utility` because that app owns the adjacent catalogues (DataPlan,
CablePlan) and still exists; a deleted app cannot carry its own migration.
"""
from django.db import migrations


TABLES = ("exams_examproduct", "betting_bettingplatform")


def drop(apps, schema_editor):
    """Drop both tables if present, on PostgreSQL and SQLite alike.

    `IF EXISTS` rather than a bare DROP because the table may already be absent:
    a database created after the apps were deleted never had it, and this
    migration must still apply cleanly there.
    """
    for table in TABLES:
        schema_editor.execute("DROP TABLE IF EXISTS " + schema_editor.quote_name(table))


def unapply(apps, schema_editor):
    """Deliberately not reversible.

    Recreating empty tables would not restore the apps, the models, the views or
    the provider rail that gave them meaning, so a reverse that "succeeds" would
    be a lie. Restoring these products means restoring a VAS integration first,
    and that is a forward change, not a rollback.
    """
    raise migrations.exceptions.IrreversibleError(
        "exams and betting were removed with the 9PSB VAS rail; restoring them "
        "is a forward change, not a rollback")


class Migration(migrations.Migration):

    dependencies = [("utility", "0004_wemabiller_meter_type_alter_wemabiller_service_id_and_more")]

    operations = [migrations.RunPython(drop, unapply)]
