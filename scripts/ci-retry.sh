#!/usr/bin/env bash
#
# Re-run a CI command, but ONLY when it failed for a reason that a second try
# could plausibly fix: a network fault or an upstream service having a bad
# minute. Anything else fails immediately.
#
# WHY THE DEFAULT IS "DO NOT RETRY". A blanket retry loop is worse than no loop
# at all on a gate like this. It triples the time to learn about a genuine
# failure, and — the part that actually costs you — it trains everyone reading
# the log to treat a red step as something that might go away, which is exactly
# the reflex you do not want around a dependency check. So the transient
# patterns below are an allow-list: a failure is retried only if it says, in so
# many words, that it never reached the far end.
#
# The patterns are deliberately anchored on wording rather than bare numbers.
# `expo install --check` reports drift as lines like "react@19.1.0 - expected
# version: 19.1.1"; matching a loose "503" against output full of version
# strings would eventually read a real drift report as an outage and retry it
# into a timeout instead of a clear answer.
#
# Usage:  scripts/ci-retry.sh <command> [args...]
#         CI_RETRY_ATTEMPTS=4 CI_RETRY_BACKOFF_S=15 scripts/ci-retry.sh …
set -uo pipefail

attempts="${CI_RETRY_ATTEMPTS:-4}"
backoff="${CI_RETRY_BACKOFF_S:-15}"

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <command> [args...]" >&2
  exit 2
fi

transient='ECONNRESET|ECONNREFUSED|ETIMEDOUT|ENOTFOUND|EAI_AGAIN|EHOSTUNREACH|ENETUNREACH|EPIPE|ERR_SOCKET|socket hang up|fetch failed|request to .* failed|getaddrinfo|network (error|timeout|is unreachable)|npm ERR! network|Service Unavailable|Bad Gateway|Gateway Time-?out|Internal Server Error|Too Many Requests|(status|statusCode|HTTP)[^0-9]{0,12}(429|5[0-9][0-9])|Unable to (reach|connect)|Connection (reset|timed out|refused)|TLS connection|read ECONN'

log="$(mktemp)"
trap 'rm -f "$log"' EXIT
cmd_desc="$*"

status=0
for attempt in $(seq 1 "$attempts"); do
  status=0
  # Truncated per attempt, and appended to from BOTH streams. Letting one
  # attempt's output survive into the next would mean a transient error from
  # attempt 1 still matching when attempt 2 failed for a real reason — the loop
  # would retry a genuine failure to exhaustion on the strength of stale text.
  : > "$log"
  # Tee so the log is visible live — a step that goes quiet for two minutes
  # while it retries is indistinguishable from a hung runner.
  "$@" > >(tee -a "$log") 2> >(tee -a "$log" >&2) || status=$?
  # The process substitutions above are reaped asynchronously; without this the
  # grep below can race the last of the output into the file.
  wait
  [ "$status" -eq 0 ] && exit 0

  if ! grep -Eqi "$transient" "$log"; then
    echo "ci-retry: '$cmd_desc' failed (exit $status) for a reason that will not change on a retry; not retrying." >&2
    exit "$status"
  fi

  if [ "$attempt" -lt "$attempts" ]; then
    wait_s=$(( backoff * attempt ))
    echo "ci-retry: '$cmd_desc' hit a transient network/service failure (attempt ${attempt}/${attempts}); retrying in ${wait_s}s…" >&2
    sleep "$wait_s"
  fi
done

echo "ci-retry: '$cmd_desc' still failing after ${attempts} attempts (exit ${status}); the upstream service looks genuinely down." >&2
exit "$status"
