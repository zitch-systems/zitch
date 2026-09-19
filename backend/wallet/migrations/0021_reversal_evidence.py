import hashlib
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


_MAX_LEDGER_AMOUNT = Decimal("999999999999.99")
_MAX_BIGINT = (2 ** 63) - 1
_CENT = Decimal("0.01")


def _legacy_amount(value):
    """Return only values representable by DecimalField(14, 2)."""
    try:
        amount = Decimal(str(value or ""))
        rounded = amount.quantize(_CENT)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if (not amount.is_finite() or amount <= 0 or amount > _MAX_LEDGER_AMOUNT
            or rounded != amount):
        return None
    return rounded


def _provider_hash(value):
    return hashlib.sha256(f"wema\0{str(value or '').strip().upper()}".encode()).hexdigest()


def _legacy_approval_id(value):
    """Keep only exact positive signed-bigint identifiers from legacy JSON."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        return None
    return parsed if 0 < parsed <= _MAX_BIGINT else None


def _legacy_entries(marker):
    entries = [dict(item) for item in (marker.get("evidence") or [])
               if isinstance(item, dict)]
    if entries:
        return entries
    if marker.get("ledger_reference") or marker.get("inbound_reference"):
        return [{
            "reason": str(marker.get("reason") or "legacy_quarantine"),
            "inbound_reference": str(marker.get("inbound_reference") or ""),
            "ledger_reference": str(marker.get("ledger_reference") or ""),
            "received_amount": str(marker.get("received_amount") or ""),
            "resolved": marker.get("active") is False and bool(marker.get("resolution")),
            "resolution": marker.get("resolution") or {},
        }]
    return []


def backfill_reversal_evidence(apps, schema_editor):
    """Adopt active pre-table JSON holds and replace their unbounded arrays."""
    Transaction = apps.get_model("wallet", "Transaction")
    Evidence = apps.get_model("wallet", "ReversalEvidence")
    Observation = apps.get_model("wallet", "ReversalEvidenceObservation")
    Resolution = apps.get_model("wallet", "ReversalEvidenceResolution")

    payouts = Transaction.objects.filter(direction="out").exclude(
        meta__wema_reversal_quarantine__isnull=True)
    for payout in payouts.iterator(chunk_size=500):
        meta = dict(payout.meta or {})
        marker = meta.get("wema_reversal_quarantine") or {}
        if not isinstance(marker, dict):
            continue
        adopted = []
        invalid_amounts = []
        for index, item in enumerate(_legacy_entries(marker)):
            raw_amount = item.get("received_amount")
            amount = _legacy_amount(raw_amount)
            if amount is None:
                invalid_amounts.append(str(raw_amount or "")[:80])
                continue
            ledger_reference = str(item.get("ledger_reference") or "").strip()[:64]
            inbound_reference = str(item.get("inbound_reference") or "").strip()
            if not inbound_reference and ledger_reference.startswith("WEMA-CR-"):
                inbound_reference = ledger_reference[len("WEMA-CR-"):]
            if not inbound_reference:
                inbound_reference = f"legacy:{payout.reference}:{index}"
            resolution = (item.get("resolution")
                          if isinstance(item.get("resolution"), dict) else {})
            resolved = item.get("resolved") is True
            approval_id = _legacy_approval_id(resolution.get("approval_id"))
            if approval_id and Resolution.objects.filter(
                    approval_id=approval_id).exists():
                approval_id = None
            ledger_row = (Transaction.objects.filter(
                reference=ledger_reference, user_id=payout.user_id,
            ).first() if ledger_reference else None)
            initial_reason = str(item.get("initial_reason") or item.get("reason") or
                                 marker.get("reason") or "legacy_quarantine")[:64]
            provider_hash = _provider_hash(inbound_reference)
            ledger_owner = (Evidence.objects.filter(
                ledger_transaction_id=getattr(ledger_row, "pk", None),
            ).first() if ledger_row is not None else None)
            ledger_conflict = bool(
                ledger_owner is not None
                and ledger_owner.provider_reference_hash != provider_hash
            )
            created_state = ("conflict" if ledger_conflict else
                             "resolved" if resolved else "active")
            created_reason = ("ledger_reference_reused" if ledger_conflict
                              else initial_reason)
            evidence, created = Evidence.objects.get_or_create(
                provider="wema",
                provider_reference_hash=provider_hash,
                defaults={
                    "provider_reference": inbound_reference[:255],
                    "ledger_reference": ledger_reference,
                    "user_id": payout.user_id,
                    "payout_id": payout.pk,
                    "ledger_transaction_id": (
                        None if ledger_conflict else getattr(ledger_row, "pk", None)),
                    "amount": amount,
                    "initial_reason": initial_reason,
                    "reason": created_reason,
                    "state": created_state,
                    "resolved_amount": amount if created_state == "resolved" else None,
                    "resolution_disposition": str(
                        resolution.get("disposition") or "legacy_resolved")[:48]
                        if created_state == "resolved" else "",
                    "resolution_reason": str(resolution.get("reason") or "")[:300],
                    "resolution_approval_id": (
                        approval_id if created_state == "resolved" else None),
                    "resolved_at": (timezone.now()
                                    if created_state == "resolved" else None),
                },
            )
            conflict_reason = ""
            if evidence.user_id != payout.user_id or evidence.payout_id not in (
                    None, payout.pk):
                conflict_reason = "provider_reference_reused"
            elif evidence.amount != amount:
                conflict_reason = "evidence_amount_changed"
            elif ledger_conflict:
                conflict_reason = "ledger_reference_reused"
            if conflict_reason:
                Evidence.objects.filter(pk=evidence.pk).update(
                    state="conflict", reason=conflict_reason,
                    version=models.F("version") + 1,
                    resolved_amount=None, resolution_disposition="",
                    resolution_reason="", resolution_approval_id=None,
                    resolved_by_id=None, resolved_at=None,
                )
            elif not created:
                changes = {}
                if evidence.payout_id is None:
                    changes["payout_id"] = payout.pk
                if (evidence.ledger_transaction_id is None and ledger_row is not None
                        and not Evidence.objects.exclude(pk=evidence.pk).filter(
                            ledger_transaction_id=ledger_row.pk).exists()):
                    changes["ledger_transaction_id"] = ledger_row.pk
                    changes["ledger_reference"] = ledger_reference
                if changes:
                    Evidence.objects.filter(pk=evidence.pk).update(**changes)
            if ledger_conflict and ledger_owner is not None:
                Evidence.objects.filter(pk=ledger_owner.pk).update(
                    state="conflict", reason="ledger_reference_reused",
                    version=models.F("version") + 1,
                    resolved_amount=None, resolution_disposition="",
                    resolution_reason="", resolution_approval_id=None,
                    resolved_by_id=None, resolved_at=None,
                )
            evidence.associated_payouts.add(payout)
            Observation.objects.get_or_create(evidence_id=evidence.pk, amount=amount)
            evidence.refresh_from_db()
            if (evidence.state == "resolved"
                    and not Resolution.objects.filter(evidence_id=evidence.pk).exists()):
                Resolution.objects.create(
                    evidence_id=evidence.pk,
                    payout_id=payout.pk,
                    disposition=evidence.resolution_disposition or "legacy_resolved",
                    reason=evidence.resolution_reason,
                    confirmed_amount=amount,
                    approval_id=approval_id,
                    payout_status_before=payout.transaction_status,
                    payout_status_after=payout.transaction_status,
                )
            adopted.append(evidence)

        if not adopted and not invalid_amounts:
            continue
        active_ids = set(Evidence.objects.filter(
            payout_id=payout.pk, state__in=("active", "conflict")
        ).values_list("pk", flat=True))
        active_ids.update(
            row.pk for row in adopted if row.state in ("active", "conflict")
        )
        latest = sorted(adopted, key=lambda row: row.pk)[-1] if adopted else None
        summary = {
            "active": bool(active_ids or invalid_amounts),
            "active_count": len(active_ids),
            "payout_amount": str(payout.amount),
        }
        if latest is not None:
            summary.update({
                "evidence_id": latest.pk,
                "evidence_version": latest.version,
                "state": latest.state,
                "initial_reason": latest.initial_reason,
                "reason": latest.reason,
                "inbound_reference": latest.provider_reference,
                "ledger_reference": latest.ledger_reference,
                "received_amount": str(latest.amount),
            })
        if invalid_amounts:
            summary.update({
                "active": True,
                "state": "conflict",
                "reason": "invalid_legacy_evidence",
                "invalid_evidence_count": len(invalid_amounts),
                "invalid_amount_samples": invalid_amounts[:3],
            })
        if marker.get("failed_refund_correction_applied"):
            summary["failed_refund_correction_applied"] = True
        prior_resolution = marker.get("resolution")
        if isinstance(prior_resolution, dict):
            summary["resolution"] = {
                key: prior_resolution[key]
                for key in (
                    "disposition", "reason", "actor", "approval_id", "reference",
                    "movement", "direction", "original_status", "final_status",
                    "resolved_at",
                ) if key in prior_resolution
            }
            summary["resolved_at"] = str(marker.get("resolved_at") or "")
        meta["wema_reversal_quarantine"] = summary
        Transaction.objects.filter(pk=payout.pk).update(meta=meta)

class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("wallet", "0020_face_account_outcome"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReversalEvidence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("provider", models.CharField(choices=[("wema", "Wema")],
                                              default="wema", max_length=20)),
                ("provider_reference", models.CharField(max_length=255)),
                ("provider_reference_hash", models.CharField(max_length=64)),
                ("ledger_reference", models.CharField(blank=True, default="", max_length=64)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("initial_reason", models.CharField(max_length=64)),
                ("reason", models.CharField(max_length=64)),
                ("state", models.CharField(
                    choices=[("active", "active"), ("conflict", "conflict"),
                             ("resolved", "resolved")],
                    db_index=True, default="active", max_length=12)),
                ("version", models.PositiveIntegerField(default=1)),
                ("resolved_amount", models.DecimalField(
                    blank=True, decimal_places=2, max_digits=14, null=True)),
                ("resolution_disposition", models.CharField(blank=True, default="",
                                                             max_length=48)),
                ("resolution_reason", models.CharField(blank=True, default="",
                                                        max_length=300)),
                ("resolution_approval_id", models.PositiveBigIntegerField(blank=True, null=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("first_seen", models.DateTimeField(auto_now_add=True)),
                ("last_seen", models.DateTimeField(auto_now=True)),
                ("ledger_transaction", models.OneToOneField(
                    blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="reversal_evidence_claim", to="wallet.transaction")),
                ("payout", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="reversal_evidence", to="wallet.transaction")),
                ("associated_payouts", models.ManyToManyField(
                    blank=True, related_name="reversal_evidence_associations",
                    to="wallet.transaction")),
                ("resolved_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="resolved_reversal_evidence", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="reversal_evidence", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-last_seen", "-id"]},
        ),
        migrations.CreateModel(
            name="ReversalEvidenceObservation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("sightings", models.PositiveIntegerField(default=1)),
                ("first_seen", models.DateTimeField(auto_now_add=True)),
                ("last_seen", models.DateTimeField(auto_now=True)),
                ("evidence", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="observations", to="wallet.reversalevidence")),
            ],
            options={"ordering": ["first_seen", "id"]},
        ),
        migrations.CreateModel(
            name="ReversalEvidenceResolution",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("disposition", models.CharField(max_length=48)),
                ("reason", models.CharField(blank=True, default="", max_length=300)),
                ("confirmed_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("approval_id", models.PositiveBigIntegerField(blank=True, null=True,
                                                                unique=True)),
                ("movement_amount", models.DecimalField(
                    blank=True, decimal_places=2, max_digits=14, null=True)),
                ("movement_direction", models.CharField(
                    blank=True, choices=[("in", "Credit"), ("out", "Debit")],
                    default="", max_length=3)),
                ("payout_status_before", models.CharField(blank=True, default="",
                                                           max_length=12)),
                ("payout_status_after", models.CharField(blank=True, default="",
                                                          max_length=12)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="reversal_evidence_resolutions", to=settings.AUTH_USER_MODEL)),
                ("evidence", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="resolutions", to="wallet.reversalevidence")),
                ("payout", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="reversal_evidence_resolutions", to="wallet.transaction")),
                ("movement_transaction", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="reversal_resolutions", to="wallet.transaction")),
            ],
            options={"ordering": ["-created", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="reversalevidence",
            constraint=models.UniqueConstraint(
                fields=("provider", "provider_reference_hash"),
                name="uniq_reversal_provider_ref"),
        ),
        migrations.AddConstraint(
            model_name="reversalevidence",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount__gt", 0)),
                name="reversal_evidence_amount_positive"),
        ),
        migrations.AddConstraint(
            model_name="reversalevidence",
            constraint=models.CheckConstraint(
                condition=~models.Q(("initial_reason", "")),
                name="reversal_initial_reason_present"),
        ),
        migrations.AddConstraint(
            model_name="reversalevidence",
            constraint=models.CheckConstraint(
                condition=(~models.Q(("state", "resolved"))
                           | (models.Q(("resolved_amount__gt", 0))
                              & models.Q(("resolved_at__isnull", False)))),
                name="resolved_reversal_has_amount_time"),
        ),
        migrations.AddIndex(
            model_name="reversalevidence",
            index=models.Index(fields=["payout", "state"],
                               name="reversal_payout_state_idx"),
        ),
        migrations.AddIndex(
            model_name="reversalevidence",
            index=models.Index(fields=["user", "state"],
                               name="reversal_user_state_idx"),
        ),
        migrations.AddConstraint(
            model_name="reversalevidenceobservation",
            constraint=models.UniqueConstraint(
                fields=("evidence", "amount"),
                name="uniq_reversal_observed_amount"),
        ),
        migrations.AddConstraint(
            model_name="reversalevidenceobservation",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount__gt", 0)),
                name="reversal_observation_amount_positive"),
        ),
        migrations.AddIndex(
            model_name="reversalevidenceresolution",
            index=models.Index(fields=["evidence", "-created"],
                               name="reversal_resolution_idx"),
        ),
        migrations.AddIndex(
            model_name="reversalevidenceresolution",
            index=models.Index(fields=["payout", "-created"],
                               name="reversal_res_payout_idx"),
        ),
        migrations.AddConstraint(
            model_name="reversalevidenceresolution",
            constraint=models.CheckConstraint(
                condition=models.Q(("confirmed_amount__gt", 0)),
                name="reversal_resolution_amount_positive"),
        ),
        migrations.AddConstraint(
            model_name="reversalevidenceresolution",
            constraint=models.CheckConstraint(
                condition=((models.Q(("movement_amount__isnull", True))
                            & models.Q(("movement_direction", "")))
                           | (models.Q(("movement_amount__gt", 0))
                              & models.Q(("movement_direction__in", ("in", "out"))))),
                name="reversal_resolution_movement_valid"),
        ),
        # Deliberately irreversible: reversing beyond this point would drop the
        # indexed evidence tables after their legacy JSON arrays were compacted.
        migrations.RunPython(backfill_reversal_evidence),
    ]
