"""Sync Wema/ALAT's VAS catalogue codes onto our seeded plans.

Wema fulfils a data or cable purchase against its OWN packageCode/packageId, which
differs from the plan_code we seed. Until a plan carries a `wema_code`,
utility.providers.vtu_purchase has nothing to send and REFUSES the purchase (before
any debit) — so running this command is what puts data/cable on sale at all, not an
optional optimisation. Electricity requires a distinct prepaid/postpaid package.
Airtime needs no catalogue. Every live product still requires status-query access;
catalogue mapping does not grant the bank subscription or settlement permission.

Matching is best-effort: data plans are matched within a network by exact price, then
by a normalised size/name; cable bouquets by exact price, then by a normalised name.
Run it with LIVE Wema keys and REVIEW the result — the ALAT catalogue field names are
VERIFY-BEFORE-LIVE (this reads `packageCode`/`packageId`/`code`/`price`/`name` defensively).

    python manage.py seed_wema_plans            # sync data, cable and electricity
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
    help = "Map Wema catalogue package IDs onto data/cable plans and electricity meter variants."

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

    # Exact biller identities observed in the live Electricity catalogue. A
    # distributor/state substring is insufficient: other categories sell unrelated
    # products under the same state name. Package IDs always come from nested
    # packages, never from a biller ID or the flattened catalogue's fallback.
    _DISCO_BILLERS = {
        "ikeja": "Ikeja Electricity",
        "eko": "EKEDC",
        "abuja": "AEDC",
        "kano": "Kano Electricity Distribution Company",
        "port harcourt": "Porthacourt Electricity Distribution Company",
        "jos": "Jos Electricity Distribution Company",
        "kaduna": "Kaduna Electric Distribution Company",
        "enugu": "Enugu Electricity Distribution Company",
        "ibadan": "Ibadan Electricity Distribution Company",
    }

    def _sync_billers(self, dry):
        """Map exact electricity billers and explicit prepaid/postpaid packages.

        A missing or ambiguous match changes nothing. Existing unspecified rows
        remain historical evidence and cannot route electricity; betting mappings
        are maintained separately. Dry-run does not write any model.
        """
        from django.db import transaction
        from utility.views import DISCO_NAMES

        res = wema.get_bills()
        raw = res.get("raw") if res.get("success") is True else None
        categories = raw.get("result") if isinstance(raw, dict) else None
        if not isinstance(categories, list):
            self.stdout.write(self.style.WARNING(
                "billers: nested catalogue unavailable — no mappings changed"))
            return
        billers = []
        for category in categories:
            if not isinstance(category, dict) or _norm(str(category.get("name", ""))) != "electricity":
                continue
            nested = category.get("billers")
            if isinstance(nested, list):
                billers.extend(row for row in nested if isinstance(row, dict))
        mapped = unresolved = 0
        updates = []
        for disco in sorted(DISCO_NAMES.values()):
            service_id = f"{disco.lower()}-electric"
            identity = _norm(self._DISCO_BILLERS.get(disco.lower(), ""))
            hits = [row for row in billers
                    if identity and _norm(str(row.get("name", ""))) == identity]
            packages = hits[0].get("packages") if len(hits) == 1 else None
            for meter_type in ("prepaid", "postpaid"):
                candidates = []
                if isinstance(packages, list):
                    for package in packages:
                        if not isinstance(package, dict):
                            continue
                        # Only an explicit, single meter type is sufficient. A
                        # package naming both types cannot safely select either.
                        words = re.findall(r"\b(?:pre|post)[ -]?paid\b",
                                           str(package.get("name", "")).lower())
                        variants = {_norm(word) for word in words}
                        package_id = package.get("id")
                        if (variants == {meter_type}
                                and isinstance(package_id, (str, int))
                                and not isinstance(package_id, bool)
                                and str(package_id).strip()):
                            candidates.append(package)
                if len(candidates) != 1:
                    unresolved += 1
                    self.stdout.write(self.style.WARNING(
                        f"  biller {service_id:26} {meter_type:8} "
                        f"{len(candidates)} package candidate(s), {len(hits)} biller(s) "
                        "— mapping unchanged"))
                    continue
                package = candidates[0]
                code = str(package["id"]).strip()
                mapped += 1
                self.stdout.write(f"  biller {service_id:26} {meter_type:8} -> {code} "
                                  f"({hits[0]['name']})")
                updates.append((service_id, meter_type, {
                    "package_id": code,
                    "biller_id": str(hits[0].get("id") or "")[:60],
                    "name": str(package.get("name", ""))[:120],
                }))
        if not dry:
            with transaction.atomic():
                for service_id, meter_type, values in updates:
                    # Keep an existing operator stop. A legacy unspecified stop
                    # also applies when splitting it into new variant rows.
                    legacy_stopped = WemaBiller.objects.filter(
                        service_id=service_id, meter_type="", active=False).exists()
                    row, created = WemaBiller.objects.get_or_create(
                        service_id=service_id, meter_type=meter_type,
                        defaults={**values, "active": not legacy_stopped})
                    if not created:
                        for key, value in values.items():
                            setattr(row, key, value)
                        row.save(update_fields=[*values, "updated"])
        self.stdout.write(self.style.SUCCESS(
            f"billers: {mapped} variant(s) matched, {unresolved} unresolved"
            f"{' (dry-run)' if dry else ''}"))
        if unresolved:
            self.stdout.write(
                "  Unresolved variants were not changed. Missing variant mappings "
                "cannot route payments; review existing mappings separately.")
