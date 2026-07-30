"""Handle an NDPR/GDPR data-subject request: export or erase.

A management command rather than an HTTP endpoint, on purpose. An export is the complete
personal record of one customer in one file — the highest-value single object this system
can produce. Putting that behind a bearer token means one stolen operator session
exfiltrates any customer's full history in a single request. A command requires shell
access to the production environment, which is a much smaller and better-audited set of
people, and it leaves the file where a human decides how to deliver it.

    python manage.py data_subject_request --user <id|email|phone> --export
    python manage.py data_subject_request --user <id|email|phone> --erase --confirm

Both record a DataSubjectRequest row with the outcome, which is the artifact an examiner
asks for.
"""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q


class Command(BaseCommand):
    help = "Export or erase one data subject's personal data (NDPR/GDPR)."

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True,
                           help="User id, email or phone.")
        parser.add_argument("--export", action="store_true",
                           help="Write the subject's data to --out (or stdout).")
        parser.add_argument("--erase", action="store_true",
                           help="Pseudonymise the subject. Requires --confirm.")
        parser.add_argument("--out", default="",
                           help="File to write the export to. Defaults to stdout.")
        parser.add_argument("--note", default="",
                           help="Free text recorded on the request (e.g. the ticket id).")
        parser.add_argument(
            "--confirm", action="store_true",
            help="Required for --erase. Erasure clears identifiers irreversibly and "
                 "deactivates the account; the financial record is retained.")

    def handle(self, *args, **options):
        from accounts.models import User
        from compliance.models import DataSubjectRequest
        from compliance.services import erase_user_data, export_user_data, record_request

        if options["export"] == options["erase"]:
            raise CommandError("Choose exactly one of --export or --erase")

        ident = options["user"].strip()
        query = Q(email__iexact=ident) | Q(phone=ident) | Q(username__iexact=ident)
        if ident.isdigit():
            query = query | Q(pk=int(ident))
        matches = list(User.objects.filter(query)[:2])
        if not matches:
            raise CommandError(f"No user matches {ident!r}")
        if len(matches) > 1:
            # Refuse rather than picking one: exporting or erasing the wrong customer's
            # data is not a recoverable mistake.
            raise CommandError(f"{ident!r} matches more than one account — use the id")
        user = matches[0]

        if options["export"]:
            data = export_user_data(user)
            req = record_request(user=user, kind=DataSubjectRequest.EXPORT,
                                 note=options["note"], completed=True,
                                 outcome={"sections": sorted(data)})
            payload = json.dumps(data, indent=2, ensure_ascii=False)
            if options["out"]:
                with open(options["out"], "w", encoding="utf-8") as fh:
                    fh.write(payload)
                self.stdout.write(f"Export written to {options['out']} "
                                  f"(request {req.pk}, due {req.due:%Y-%m-%d})")
            else:
                self.stdout.write(payload)
            return

        if not options["confirm"]:
            self.stdout.write(
                "Erasure clears this account's name, email, phone, address, avatar, "
                "identifier last-4s and verification hashes, deletes its sessions, "
                "WhatsApp links and linked banks, and deactivates it.\n"
                "The ledger, audit log and any AML cases are RETAINED — the 5+ year "
                "record-keeping commitment is not ours to waive.\n"
                "Re-run with --confirm to proceed.")
            raise SystemExit(1)

        # Captured before the erasure: afterwards there is nothing left to derive it
        # from, and the request has to stay traceable to the person who made it.
        label = user.email or user.phone or user.username or f"u_{user.pk}"
        outcome = erase_user_data(user, note=options["note"])
        req = record_request(user=user, kind=DataSubjectRequest.ERASE,
                            note=options["note"], completed=True, outcome=outcome,
                            subject_label=label)
        self.stdout.write(f"Erased. Pseudonym {outcome['pseudonym']}; "
                          f"cleared {', '.join(outcome['cleared_fields']) or 'nothing'} "
                          f"(request {req.pk})")
