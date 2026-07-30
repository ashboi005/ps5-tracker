#!/usr/bin/env python3
"""Liveness check for the tracker loop.

There is no HTTP server to probe — this is a worker that sleeps between passes.
So health means "a check pass completed recently": the log file was written
within a few intervals. Without an explicit check like this, an orchestrator
looking for an open port concludes the container is unhealthy and restart-loops
it forever.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

LOG_PATH = Path(os.getenv("PS5_LOG_PATH") or "/data/run.log")
INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS") or 600)

# Tolerate a couple of missed or slow passes (browser checks can be slow)
# before declaring the loop dead.
GRACE = INTERVAL * 3 + 300


def main() -> int:
    if not LOG_PATH.exists():
        print(f"unhealthy: {LOG_PATH} does not exist yet")
        return 1

    age = time.time() - LOG_PATH.stat().st_mtime
    if age > GRACE:
        print(f"unhealthy: no log activity for {int(age)}s (limit {GRACE}s)")
        return 1

    print(f"healthy: last activity {int(age)}s ago")
    return 0


if __name__ == "__main__":
    sys.exit(main())
