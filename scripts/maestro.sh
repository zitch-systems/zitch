#!/usr/bin/env bash
# Run the Maestro device flows.
#
#   scripts/maestro.sh                  every flow
#   scripts/maestro.sh transfer         one flow (name with or without .yaml)
#
# Credentials come from the environment so no test account is committed. Flows are
# checked for the variables they interpolate BEFORE Maestro runs: an unset variable
# reaches the app as the literal string "${ZITCH_TEST_PHONE}", which fails deep inside
# a flow with a confusing selector error instead of at the door.
set -o errexit
set -o nounset
set -o pipefail

cd "$(dirname "$0")/.."
FLOW_DIR=.maestro

if ! command -v maestro >/dev/null 2>&1; then
  echo "maestro is not installed. Install it with:" >&2
  echo "  curl -fsSL https://get.maestro.mobile.dev | bash" >&2
  exit 1
fi

# Pick the flows to run. Subflows are components, never entry points.
if [ "$#" -gt 0 ]; then
  name="${1%.yaml}"
  flows=("$FLOW_DIR/$name.yaml")
  if [ ! -f "${flows[0]}" ]; then
    echo "No such flow: ${flows[0]}" >&2
    echo "Available:" >&2
    find "$FLOW_DIR" -maxdepth 1 -name '*.yaml' -exec basename {} .yaml \; | sort | sed 's/^/  /' >&2
    exit 1
  fi
else
  # shellcheck disable=SC2207
  flows=($(find "$FLOW_DIR" -maxdepth 1 -name '*.yaml' | sort))
fi

# Collect every ZITCH_TEST_* variable the selected flows (and the subflows they pull in)
# actually reference, then fail once, listing all of them.
referenced() {
  {
    printf '%s\n' "${flows[@]}"
    # Any subflow is a dependency of the selection, so its variables count too.
    find "$FLOW_DIR/subflows" -name '*.yaml' 2>/dev/null || true
  } | xargs grep -oh '\${ZITCH_TEST_[A-Z_]*}' 2>/dev/null | tr -d '${}' | sort -u
}

missing=()
for var in $(referenced); do
  if [ -z "${!var:-}" ]; then
    missing+=("$var")
  fi
done

if [ "${#missing[@]}" -gt 0 ]; then
  echo "These flows need environment variables that are not set:" >&2
  for var in "${missing[@]}"; do
    echo "  $var" >&2
  done
  echo >&2
  echo "Example:" >&2
  echo "  export ZITCH_TEST_PHONE=08012345678" >&2
  echo "  export ZITCH_TEST_PASSWORD='…'" >&2
  echo "  export ZITCH_TEST_RECIPIENT=08087654321" >&2
  echo "  export ZITCH_TEST_SIGNUP_PHONE=08099998888   # the TEST_OTP_PHONE on the backend" >&2
  echo >&2
  echo "See .maestro/README.md — and do not point these at production." >&2
  exit 1
fi

env_args=()
for var in $(referenced); do
  env_args+=(-e "$var=${!var}")
done

status=0
for flow in "${flows[@]}"; do
  echo
  echo "── $(basename "$flow") ──────────────────────────────────────────────"
  # Keep going after a failure so one broken flow doesn't hide the state of the rest,
  # then exit non-zero at the end.
  maestro test "${env_args[@]}" "$flow" || status=1
done

exit "$status"
