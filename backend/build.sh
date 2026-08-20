#!/usr/bin/env bash
# Render build step. Make executable: chmod +x build.sh
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
python manage.py seed_plans

# Map Wema's VAS catalogue onto our seeded plans, when asked to.
#
# Same reason as the operator bootstrap below: there is no shell on this plan, so
# a deploy is the only place a one-off management command can run against the
# production database with the live keys. Gated on an env var rather than run
# every time, because this is a best-effort NAME MATCH against the bank's
# catalogue and a wrong match routes somebody's meter number to the wrong
# distributor — it wants reading before it is trusted, not repeating unattended.
#
#   SEED_WEMA_CATALOGUE=dry   report the matches, write nothing
#   SEED_WEMA_CATALOGUE=billers|data|cable|all   write that catalogue
#
# Never fatal. errexit is on, so an unreachable catalogue or a rail hiccup would
# otherwise fail the build and take the API down with it — a VAS mapping is not
# worth an outage, and leaving a service unmapped simply keeps it on VTU.ng.
case "${SEED_WEMA_CATALOGUE:-}" in
  "") ;;
  dry) python manage.py seed_wema_plans --dry-run || echo "==> catalogue dry-run failed (ignored)" ;;
  all) python manage.py seed_wema_plans || echo "==> catalogue sync failed (ignored)" ;;
  billers|data|cable)
    python manage.py seed_wema_plans --only "$SEED_WEMA_CATALOGUE" \
      || echo "==> catalogue sync failed (ignored)" ;;
  *) echo "==> WARNING: SEED_WEMA_CATALOGUE='$SEED_WEMA_CATALOGUE' is not a known value; skipping." ;;
esac

# Auto-provision a super_admin operator from env vars (Render free tier has no
# shell, so this is the only way to bootstrap admin access without one). Skipped
# when DJANGO_SUPERUSER_PASSWORD is unset, and idempotent: seed_ops upserts the
# account, so re-deploys never duplicate or reset an existing password unless
# DJANGO_SUPERUSER_PASSWORD changed.
if [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
  ADMIN_USERNAME="${DJANGO_SUPERUSER_USERNAME:-admin}"
  python manage.py seed_ops \
    --username "$ADMIN_USERNAME" \
    --role super_admin \
    --password "$DJANGO_SUPERUSER_PASSWORD" \
    --email "${DJANGO_SUPERUSER_EMAIL:-admin@zitch.ng}"
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
  echo "==>          account is upserted, and the password is reset to match."
fi
