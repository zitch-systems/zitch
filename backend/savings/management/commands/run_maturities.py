"""Pay out matured Fixed Save plans. Run from Render Cron (daily):

    python manage.py run_maturities
"""
from django.core.management.base import BaseCommand, CommandError

from common.products import product_available
from savings.models import FixedSave
from savings.services import run_maturities


class Command(BaseCommand):
    help = "Credit principal + interest for any Fixed Save plans that have matured."

    def handle(self, *args, **options):
        from whatsapp.ops import record_audit
        if not product_available("savings"):
            # Disabled product with no liability is an intentional no-op, not a
            # daily outage. Unpaid plans remain visible and require bank-backed
            # resolution; never manufacture a local payout while live settlement
            # is unavailable, even if a plan's status was changed unexpectedly.
            unpaid = FixedSave.objects.filter(paid_out=False).count()
            record_audit("recon.maturities_run", actor_type="system",
                         after={"paid_out": 0, "product_disabled": True,
                                "unpaid_plans": unpaid})
            if unpaid:
                raise CommandError("Fixed savings disabled with unpaid plans; operator review required.")
            self.stdout.write("Fixed savings disabled; no unpaid plans. Nothing to settle.")
            return
        n = run_maturities()
        record_audit("recon.maturities_run", actor_type="system", after={"paid_out": n})
        self.stdout.write(self.style.SUCCESS(f"Paid out {n} matured plan(s)."))
