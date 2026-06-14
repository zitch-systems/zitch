#!/usr/bin/env bash
# Render build step. Make executable: chmod +x build.sh
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
python manage.py seed_plans

# Auto-provision a super_admin operator from env vars (Render free tier has no
# shell, so this is the only way to bootstrap admin access without one). Skipped
# when DJANGO_SUPERUSER_PASSWORD is unset, and idempotent: seed_ops upserts the
# account, so re-deploys never duplicate or reset an existing password unless
# DJANGO_SUPERUSER_PASSWORD changed.
if [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
  python manage.py seed_ops \
    --username "${DJANGO_SUPERUSER_USERNAME:-admin}" \
    --role super_admin \
    --password "$DJANGO_SUPERUSER_PASSWORD" \
    --email "${DJANGO_SUPERUSER_EMAIL:-admin@zitch.ng}"
fi
