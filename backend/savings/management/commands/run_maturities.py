"""Pay out matured Fixed Save plans. Run from Render Cron (daily):

    python manage.py run_maturities
"""
from django.core.management.base import BaseCommand, CommandError

from common.products import ProductUnavailable, require_product
from savings.services import run_maturities


class Command(BaseCommand):
    help = "Credit principal + interest for any Fixed Save plans that have matured."

    def handle(self, *args, **options):
        try:
            require_product("savings")
            n = run_maturities()
        except ProductUnavailable as exc:
            raise CommandError(str(exc)) from exc
        from whatsapp.ops import record_audit
        record_audit("recon.maturities_run", actor_type="system", after={"paid_out": n})
        self.stdout.write(self.style.SUCCESS(f"Paid out {n} matured plan(s)."))
