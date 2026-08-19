"""Sync Wema/ALAT's VAS catalogue codes onto our seeded plans.

Wema fulfils a data or cable purchase against its OWN packageCode/packageId, which
differs from the VTU.ng plan_code we seed. Until a plan carries a `wema_code`,
utility.providers.vtu_purchase keeps that service on VTU.ng — so data/cable move to
Wema only after this command has mapped the catalogue. Airtime needs no catalogue and
routes to Wema regardless.

Matching is best-effort: data plans are matched within a network by exact price, then
by a normalised size/name; cable bouquets by exact price, then by a normalised name.
Run it with LIVE Wema keys and REVIEW the result — the ALAT catalogue field names are
VERIFY-BEFORE-LIVE (this reads `packageCode`/`packageId`/`code`/`price`/`name` defensively).

    python manage.py seed_wema_plans            # sync both data and cable
    python manage.py seed_wema_plans --dry-run  # report matches, write nothing
    python manage.py seed_wema_plans --only data
"""
import re
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand

from utility import wema
from utility.models import CablePlan, DataPlan, WemaBiller

_NET = {"1": "mtn", "2": "glo", "3": "airtel", "4": "9mobile", "MTN": "mtn",
        "GLO": "glo", "AIRTEL": "airtel", "9MOBILE": "9mobile", "ETISALAT": "9mobile"}


def _num(v):
    try:
        return Decimal(str(v).replace(",", "").replace("₦", "").strip()).quantize(Decimal("0.01"))
    except (TypeError, ValueError, InvalidOperation):
        return None


def _norm(s: str) -> str:
    """Lowercased alphanumerics only — so '1.5GB' == '1.5 gb'."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _code(row: dict) -> str:
    for k in ("packageCode", "packageId", "code", "planCode", "productCode", "id"):
        if row.get(k):
            return str(row[k])
    return ""


def _price(row: dict):
    for k in ("price", "amount", "cost", "faceValue"):
        if row.get(k) is not None:
            n = _num(row[k])
            if n is not None:
                return n
    return None


def _name(row: dict) -> str:
    for k in ("name", "planName", "size", "description", "bouquet", "productName"):
        if row.get(k):
            return str(row[k])
    return ""


class Command(BaseCommand):
    help = "Map Wema's VAS catalogue (packageCode/packageId) onto DataPlan/CablePlan.wema_code."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report matches; write nothing.")
        parser.add_argument("--only", choices=["data", "cable", "billers"],
                            help="Sync only one catalogue.")

    def handle(self, *args, **opts):
        dry, only = opts["dry_run"], opts.get("only")
        if not (wema.wema_live() or wema.wema_simulation()):
            self.stdout.write(self.style.WARNING(
                "Wema is not configured (no live keys). Set WEMA_CHANNEL_ID + the VAS "
                "product keys, then re-run. Nothing changed."))
            return
        if only in (None, "data"):
            self._sync_data(dry)
        if only in (None, "cable"):
            self._sync_cable(dry)
        if only in (None, "billers"):
            self._sync_billers(dry)

    def _sync_data(self, dry):
        # wema.get_data_plans() flattens ALAT's result[].dataPackages[] into
        # {code, name, amount, network} rows (code = the dataPackage id); passing a
        # network filters client-side. Match a DataPlan within its network by exact
        # price, then a normalised name.
        matched = 0
        by_net: dict[str, list] = {}
        for net_key in ("1", "2", "3", "4"):
            res = wema.get_data_plans(_NET.get(net_key, ""))
            if res.get("success"):
                by_net[net_key] = res.get("plans", []) or []
        for plan in DataPlan.objects.all():
            rows = by_net.get(plan.network, [])
            hit = next((r for r in rows if _price(r) == plan.price), None) \
                or next((r for r in rows if _norm(_name(r)) == _norm(plan.name)), None)
            code = _code(hit) if hit else ""
            if code and plan.wema_code != code:
                matched += 1
                self.stdout.write(f"  data  {plan.get_network_display():8} {plan.name:12} -> {code}")
                if not dry:
                    plan.wema_code = code
                    plan.save(update_fields=["wema_code"])
        self.stdout.write(self.style.SUCCESS(f"data: {matched} plan(s) mapped{' (dry-run)' if dry else ''}"))

    def _sync_cable(self, dry):
        # wema.get_bills() already flattens categories -> billers -> packages into
        # {code, name, amount, biller, category} rows (code = the packageId PayBill
        # wants). Scope each CablePlan's search to rows whose biller/category names
        # the provider (DStv/GOtv/StarTimes) so an unrelated biller package with a
        # coincidentally equal price can't be mis-mapped; fall back to the full set.
        res = wema.get_bills()
        rows = res.get("bills", []) or [] if res.get("success") else []
        matched = 0
        for plan in CablePlan.objects.all():
            prov = _norm(plan.get_provider_display())
            scoped = [r for r in rows
                      if prov and (prov in _norm(r.get("biller", "")) or prov in _norm(r.get("category", "")))]
            pool = scoped or rows
            hit = next((r for r in pool if _price(r) == plan.price), None) \
                or next((r for r in pool if _norm(_name(r)) == _norm(plan.name)), None)
            code = _code(hit) if hit else ""
            if code and plan.wema_code != code:
                matched += 1
                self.stdout.write(f"  cable {plan.get_provider_display():10} {plan.name:16} -> {code}")
                if not dry:
                    plan.wema_code = code
                    plan.save(update_fields=["wema_code"])
        self.stdout.write(self.style.SUCCESS(f"cable: {matched} bouquet(s) mapped{' (dry-run)' if dry else ''}"))

    # Disco slug (as the app builds service_id) -> words that identify the biller in
    # ALAT's catalogue. Matching is by name because ALAT publishes no stable code we
    # share; each entry needs only enough words to be unambiguous among Nigerian
    # discos. A slug that matches nothing is REPORTED, never guessed — a wrong
    # packageId here would pay a stranger's electricity bill.
    _DISCO_WORDS = {
        "ikeja": ("ikeja",), "eko": ("eko",), "abuja": ("abuja",), "kano": ("kano",),
        "port harcourt": ("portharcourt", "phed"), "jos": ("jos",),
        "kaduna": ("kaduna",), "enugu": ("enugu", "eedc"), "ibadan": ("ibadan",),
    }

    def _sync_billers(self, dry):
        """Map electricity and betting service_ids onto ALAT packageIds.

        Unlike data and cable, these have no plan row to hang a code on, so they get
        their own table (WemaBiller). Only rows we can identify by name are written;
        anything ambiguous is printed for a human, because the failure mode of a bad
        mapping is paying the wrong biller, not a missing feature.
        """
        from utility.views import DISCO_NAMES
        res = wema.get_bills()
        rows = res.get("bills", []) or [] if res.get("success") else []
        if not rows:
            self.stdout.write(self.style.WARNING(
                "billers: catalogue empty — electricity/betting stay on VTU.ng"))
            return
        wanted = {f"{DISCO_NAMES[k].lower()}-electric": self._DISCO_WORDS.get(DISCO_NAMES[k].lower(), ())
                  for k in DISCO_NAMES}
        # Scope to electricity billers before matching, exactly as _sync_cable scopes
        # to the provider. ALAT's catalogue carries state water boards, waste boards
        # and revenue agencies alongside the discos, and several share a state name —
        # so an unscoped search for "kano" can match "Kano State Water Board" and map
        # a customer's electricity payment to somebody's water bill. Falls back to the
        # full set only when no row declares a power category, so a catalogue that
        # labels things differently still maps rather than silently mapping nothing.
        power = [r for r in rows
                 if any(w in _norm(f"{r.get('category', '')} {r.get('biller', '')}")
                        for w in ("electric", "power", "disco", "energy"))]
        pool = power or rows
        if not power:
            self.stdout.write(self.style.WARNING(
                "  billers: no electricity category found — matching against the whole "
                "catalogue, so REVIEW each mapping below before applying"))
        mapped = unmatched = 0
        for service_id, words in sorted(wanted.items()):
            hits = [r for r in pool
                    if any(w in _norm(f"{r.get('biller', '')} {_name(r)}") for w in words)]
            if len(hits) != 1:
                unmatched += 1
                self.stdout.write(self.style.WARNING(
                    f"  biller {service_id:26} {len(hits)} candidate(s) — left on VTU.ng"))
                continue
            code = _code(hits[0])
            if not code:
                unmatched += 1
                continue
            mapped += 1
            self.stdout.write(f"  biller {service_id:26} -> {code}  ({hits[0].get('biller', '')})")
            if not dry:
                # `active` is deliberately NOT in defaults. An operator switches a row
                # off to stop a bad mapping taking payments; a catalogue sync is not a
                # decision to undo that, and silently re-enabling it would resume the
                # exact routing somebody had intervened to halt.
                WemaBiller.objects.update_or_create(
                    service_id=service_id,
                    defaults={"package_id": code, "biller_id": str(hits[0].get("biller", ""))[:60],
                              "name": _name(hits[0])[:120]},
                )
        self.stdout.write(self.style.SUCCESS(
            f"billers: {mapped} mapped, {unmatched} unresolved{' (dry-run)' if dry else ''}"))
        if unmatched:
            self.stdout.write(
                "  Unresolved services keep working on VTU.ng. Map them by hand in the "
                "admin (utility > Wema billers) once you can see the ALAT catalogue.")
