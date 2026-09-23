from django.core.management.base import BaseCommand

from vas_harness.fixtures import seed_customers


class Command(BaseCommand):
    help = "Create three synthetic 711 accounts without changing existing records."

    def handle(self, *args, **options):
        seed_customers()
        self.stdout.write("Three synthetic fixture accounts available; no live customers imported.")
