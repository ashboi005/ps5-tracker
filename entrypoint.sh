#!/usr/bin/env bash
# Run one check pass every CHECK_INTERVAL_SECONDS.
#
# A sleep loop rather than cron: Coolify deploys this as a long-running service,
# and cron inside a container needs extra plumbing to forward env vars and logs
# to stdout. This keeps both working by default.
set -uo pipefail

INTERVAL="${CHECK_INTERVAL_SECONDS:-1800}"

# Exit promptly on stop/redeploy instead of waiting out the sleep.
trap 'echo "[entrypoint] shutting down"; kill -- -$$ 2>/dev/null; exit 0' TERM INT

echo "[entrypoint] starting; interval=${INTERVAL}s"

while true; do
    # Never let a crashed pass kill the loop — the next run may well succeed.
    python main.py || echo "[entrypoint] run exited non-zero, continuing"
    sleep "$INTERVAL" &
    wait $!
done
