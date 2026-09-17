#!/usr/bin/env bash
# One-time, cross-region Render Postgres migration.
# It intentionally does not echo connection strings or passwords.
set -euo pipefail

required=(SOURCE_DATABASE_URL TARGET_DATABASE_URL CONFIRM_TARGET_OVERWRITE)
for key in "${required[@]}"; do
  if [ -z "${!key:-}" ]; then
    echo "ERROR: ${key} must be set."
    exit 2
  fi
done

if [ "${CONFIRM_TARGET_OVERWRITE}" != "frankfurt-db-ry6y" ]; then
  echo "ERROR: Refusing to overwrite the target. Set CONFIRM_TARGET_OVERWRITE=frankfurt-db-ry6y."
  exit 2
fi

if [ "${SOURCE_DATABASE_URL}" = "${TARGET_DATABASE_URL}" ]; then
  echo "ERROR: Source and target database URLs must be different."
  exit 2
fi

for command in pg_dump pg_restore psql; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "ERROR: ${command} is not installed in this job image."
    exit 3
  fi
done

echo "Checking source database connection..."
psql "${SOURCE_DATABASE_URL}" --no-psqlrc --quiet --tuples-only --command 'SELECT 1' >/dev/null

echo "Checking target database connection..."
psql "${TARGET_DATABASE_URL}" --no-psqlrc --quiet --tuples-only --command 'SELECT 1' >/dev/null

echo "Disconnecting applications from the Frankfurt target..."
psql "${TARGET_DATABASE_URL}" --no-psqlrc --quiet --command "
  SELECT pg_terminate_backend(pid)
  FROM pg_stat_activity
  WHERE datname = current_database()
    AND pid <> pg_backend_pid();
" >/dev/null

echo "Copying Oregon data into the Frankfurt target..."
pg_dump "${SOURCE_DATABASE_URL}" \
  --format=custom \
  --no-owner \
  --no-acl \
  --verbose \
| pg_restore \
    --dbname="${TARGET_DATABASE_URL}" \
    --clean \
    --if-exists \
    --no-owner \
    --no-acl \
    --exit-on-error \
    --verbose

echo "Verifying that the target accepts queries..."
psql "${TARGET_DATABASE_URL}" --no-psqlrc --quiet --tuples-only --command 'SELECT 1' >/dev/null
echo "Migration complete. Oregon was read only; Frankfurt now contains the restored data."
