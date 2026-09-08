"""Retired compatibility command.

VTU.ng has been removed. Partner-bank VAS settlement is handled by
reconcile_wema, including its 10-second worker pass and recovery cron.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Retired: partner-bank reconciliation is the only VAS settlement rail."

    def handle(self, *args, **options):
        self.stdout.write("VTU.ng reconciliation disabled; partner-bank reconciliation is authoritative.")
