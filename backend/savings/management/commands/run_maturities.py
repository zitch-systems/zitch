"""Pay out matured Fixed Save plans. Run from Render Cron (daily):

    python manage.py run_maturities
"""
from django.core.management.base import BaseCommand

from savings.services import run_maturities


class Command(BaseCommand):
    help = "Credit principal + interest for any Fixed Save plans that have matured."

    def handle(self, *args, **options):
        n = run_maturities()
        self.stdout.write(self.style.SUCCESS(f"Paid out {n} matured plan(s)."))
