#!/usr/bin/env bash
# Render build step. Make executable: chmod +x build.sh
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
# Migration 0004 makes one card per user a database invariant. Detect legacy
# duplicates first so the deploy stops with actionable, non-sensitive evidence
# rather than failing while adding the constraint. The migration repeats the
# check to close the race between this preflight and schema application.
python manage.py audit_card_uniqueness
python manage.py migrate
# Explicit, one-shot Flow administration for plans without a shell. Draft
# preparation and publication are separate operations; neither selects the
# customer Flow ID. Missing/incorrect review inputs stop the candidate build.
if [ -n "${WHATSAPP_FLOW_DEPLOY_OPERATION:-}" ]; then
  python manage.py deploy_whatsapp_flow
fi
# Read-only, redacted evidence for operators while public maintenance is on.
# This reports configuration gaps; it cannot authorize release or change funds.
python manage.py bank_preflight --report-only
# Optional incident evidence for a bounded set of internal user IDs. The command
# reads bank statements and local history only, prints redacted summaries, and
# never changes balances, checkpoints, KYC state or payment outcomes. Clear the
# selector after collecting evidence so routine deploys do not repeat bank reads.
if [ -n "${ZITCH_RECONCILIATION_EVIDENCE_USERS:-}" ]; then
  python manage.py reconciliation_evidence \
    --user-ids "$ZITCH_RECONCILIATION_EVIDENCE_USERS" --days 90 --max-rows 50 \
    || echo "==> Reconciliation evidence unavailable; launch review remains open."
fi
python manage.py sync_bank_directory --if-configured

# Auto-provision a super_admin operator from env vars (Render free tier has no
# shell, so this is the only way to bootstrap admin access without one). Skipped
# when DJANGO_SUPERUSER_PASSWORD is unset, and idempotent: seed_ops upserts the
# account without re-applying the bootstrap password to an existing operator.
# This prevents an old Render secret from undoing a password rotation on every
# deploy. An explicit one-deploy recovery reset is available below.
if [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
  ADMIN_USERNAME="${DJANGO_SUPERUSER_USERNAME:-admin}"
  PASSWORD_MODE="--preserve-existing-password"
  if [ "${DJANGO_SUPERUSER_RESET_PASSWORD:-false}" = "true" ]; then
    PASSWORD_MODE=""
    echo "==> WARNING: Explicit operator password recovery reset requested. Set DJANGO_SUPERUSER_RESET_PASSWORD=false immediately after this deploy."
  fi
  python manage.py seed_ops \
    --username "$ADMIN_USERNAME" \
    --role super_admin \
    --password "$DJANGO_SUPERUSER_PASSWORD" \
    --email "${DJANGO_SUPERUSER_EMAIL:-admin@zitch.ng}" \
    $PASSWORD_MODE
  echo "==> Operator bootstrap OK. Sign in at /portal/ as '$ADMIN_USERNAME' (or its email) with DJANGO_SUPERUSER_PASSWORD."
else
  # Loud on purpose. This used to skip in silence, which is indistinguishable in
  # the deploy log from a successful bootstrap — so the first sign anything was
  # wrong was being unable to log in, with nothing to point at.
  echo "==> WARNING: DJANGO_SUPERUSER_PASSWORD is not set."
  echo "==>          Skipping admin bootstrap: NO account is created, and /admin/"
  echo "==>          will reject every login. Set DJANGO_SUPERUSER_PASSWORD (and"
  echo "==>          optionally DJANGO_SUPERUSER_USERNAME, default 'admin') in the"
  echo "==>          Render dashboard, then redeploy. Re-running is safe: the"
  echo "==>          account is created on the next deploy. For recovery of an"
  echo "==>          existing operator, set DJANGO_SUPERUSER_RESET_PASSWORD=true"
  echo "==>          for one deploy only, then return it to false."
fi
