"""Last-known status per product URL, so we only alert on transitions.

Shape of state.json:
    {"<url>": {"in_stock": bool, "fail_count": int, "broken_notified": bool}}
"""

import json
import logging
from pathlib import Path

STATE_PATH = Path(__file__).parent / "state.json"

# Consecutive failed checks before we warn that a checker looks broken.
# At a 30-minute cadence this is ~90 minutes blind.
FAIL_THRESHOLD = 3

log = logging.getLogger(__name__)


def load(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.error("could not read %s (%s), starting from empty state", path.name, exc)
        return {}


def save(state: dict, path: Path = STATE_PATH) -> None:
    """Write atomically, so an interrupted run cannot leave corrupt state."""
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, indent=2))
    temp.replace(path)


def entry(state: dict, url: str) -> dict:
    return state.get(url, {"in_stock": False, "fail_count": 0, "broken_notified": False})


def apply(state: dict, result) -> tuple[bool, bool]:
    """Fold one CheckResult into state.

    Returns (alert_stock, alert_broken):
      alert_stock  - stock newly appeared (not-in-stock/unknown -> in-stock)
      alert_broken - this checker just crossed FAIL_THRESHOLD for the first time
    """
    previous = entry(state, result.url)

    if not result.ok:
        fail_count = previous["fail_count"] + 1
        already_warned = previous["broken_notified"]
        alert_broken = fail_count >= FAIL_THRESHOLD and not already_warned
        state[result.url] = {
            # Preserve last known stock state; a failed check tells us nothing new.
            "in_stock": previous["in_stock"],
            "fail_count": fail_count,
            "broken_notified": already_warned or alert_broken,
        }
        return False, alert_broken

    alert_stock = result.in_stock and not previous["in_stock"]
    state[result.url] = {
        "in_stock": result.in_stock,
        "fail_count": 0,
        "broken_notified": False,
    }
    return alert_stock, False
