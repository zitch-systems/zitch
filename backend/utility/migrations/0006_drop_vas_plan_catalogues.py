"""Drop the airtime/data and cable plan catalogues with the VAS rail.

Both described products bought through the 9PSB VAS rail: a data bundle per
network, a cable bouquet per provider. The rail, its endpoints, its WhatsApp
flows and its Flow screens are gone, so the catalogues describe nothing anyone
can buy.

Unlike `0005_drop_exam_and_betting_catalogues`, this is a plain DeleteModel
rather than raw SQL. That one had to drop tables by name because the apps that
owned them were deleted outright, leaving Django with no model state to
migrate from. `utility` survives -- it still holds WemaBiller and the SMS,
email, KYC, QR and card-issuer code -- so Django can manage these two itself.

WemaBiller is deliberately NOT dropped. It is Wema-era evidence, retained
read-only like the balance archives, and it is not 9PSB.

Nothing references either table: a purchase recorded its plan code inside
Transaction.meta, never a foreign key, so no ledger row loses a reference. The
money's evidence stays in wallet_transaction, which this does not touch.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("utility", "0005_drop_exam_and_betting_catalogues"),
    ]

    operations = [
        migrations.DeleteModel(
            name="CablePlan",
        ),
        migrations.DeleteModel(
            name="DataPlan",
        ),
    ]
