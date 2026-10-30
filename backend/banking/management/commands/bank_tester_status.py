import json

from django.core.management.base import BaseCommand, CommandError

from banking.readiness import find_tester, tester_report


class Command(BaseCommand):
    help = "Explain which onboarding gate blocks one tester; no bank requests, credentials or full PII."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--user-id", type=int)
        target.add_argument("--phone", help="The tester's Nigerian mobile number in any common form")

    def handle(self, *args, **options):
        try:
            user = find_tester(user_id=options["user_id"], mobile=options["phone"])
        except ValueError:
            raise CommandError("Enter a valid Nigerian mobile number") from None
        if user is None:
            raise CommandError("No Zitch user matches; register in the app first")
        self.stdout.write(json.dumps(tester_report(user), sort_keys=True))
