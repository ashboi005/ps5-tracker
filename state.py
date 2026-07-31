"""Last-known status per product URL, so we only alert on transitions.

Shape of state.json:
    {
      "<url>": {"in_stock": bool, "fail_count": int, "broken_notified": bool},
      "_meta": {"last_heartbeat": <epoch seconds>}
    }

Product keys are always URLs, so "_meta" cannot collide with one.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

# Overridable so a container can keep state on a mounted volume; without that,
# every redeploy would forget what was in stock and re-alert on everything.
STATE_PATH = Path(
    os.getenv("PS5_STATE_PATH") or Path(__file__).parent / "state.json"
)

# Consecutive failed checks before we warn that a checker looks broken.
# At a 30-minute cadence this is ~90 minutes blind.
FAIL_THRESHOLD = 3

log = logging.getLogger(__name__)


def _resolve(path: Path | None) -> Path:
    """Resolve the state path at call time.

    Deliberately not a default argument: those bind at import time, which would
    ignore any later change to STATE_PATH (as tests and container setups do).
    """
    return Path(path) if path is not None else STATE_PATH


def load(path: Path | None = None) -> dict:
    target = _resolve(path)
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.error("could not read %s (%s), starting from empty state", target.name, exc)
        return {}


def save(state: dict, path: Path | None = None) -> None:
    """Write atomically, so an interrupted run cannot leave corrupt state."""
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, indent=2))
    temp.replace(target)


META_KEY = "_meta"

# How long without any message before we send a "still alive" summary. Without
# this, a silent tracker and a dead tracker look identical.
HEARTBEAT_SECONDS = int(os.getenv("HEARTBEAT_SECONDS") or 12 * 60 * 60)


def key_for(result) -> str:
    """State key for a result. Includes the pincode: one URL may be tracked at
    several pincodes, and each needs its own transition history."""
    pincode = getattr(result, "pincode", "") or ""
    return f"{result.url}@{pincode}" if pincode else result.url


def entry(state: dict, url: str) -> dict:
    return state.get(url, {"in_stock": False, "fail_count": 0, "broken_notified": False})


def product_entries(state: dict) -> dict:
    """State minus bookkeeping, so callers can iterate products safely."""
    return {key: value for key, value in state.items() if key != META_KEY}


def heartbeat_due(state: dict, now: float) -> bool:
    """True if a heartbeat is overdue. A fresh state file starts the clock."""
    last = (state.get(META_KEY) or {}).get("last_heartbeat")
    if last is None:
        return False
    return (now - float(last)) >= HEARTBEAT_SECONDS


def mark_heartbeat(state: dict, now: float) -> None:
    state.setdefault(META_KEY, {})["last_heartbeat"] = now


def ensure_heartbeat_clock(state: dict, now: float) -> None:
    """Start the heartbeat clock on first run, so it fires 12h later, not now."""
    if (state.get(META_KEY) or {}).get("last_heartbeat") is None:
        mark_heartbeat(state, now)


def apply(state: dict, result) -> tuple[bool, bool]:
    """Fold one CheckResult into state.

    Returns (alert_stock, alert_broken):
      alert_stock  - stock newly appeared (not-in-stock/unknown -> in-stock)
      alert_broken - this checker just crossed FAIL_THRESHOLD for the first time
    """
    key = key_for(result)
    previous = entry(state, key)

    if not result.ok:
        fail_count = previous["fail_count"] + 1
        already_warned = previous["broken_notified"]
        alert_broken = fail_count >= FAIL_THRESHOLD and not already_warned
        state[key] = {
            # Preserve last known stock state; a failed check tells us nothing new.
            "in_stock": previous["in_stock"],
            "fail_count": fail_count,
            "broken_notified": already_warned or alert_broken,
        }
        return False, alert_broken

    alert_stock = result.in_stock and not previous["in_stock"]
    state[key] = {
        "in_stock": result.in_stock,
        "fail_count": 0,
        "broken_notified": False,
    }
    return alert_stock, False
