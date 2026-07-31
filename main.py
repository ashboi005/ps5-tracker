#!/usr/bin/env python3
"""PS5 availability tracker — one pass per invocation, driven by cron.

Usage:
    python main.py                  # normal run: check, alert, persist state
    python main.py --dry-run        # check and print, send nothing, persist nothing
    python main.py --check croma    # run one retailer only
    python main.py --test-notify    # send a test message to every channel
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

import httpx
from dotenv import load_dotenv

import notifiers
import state as state_module
from checkers import ALL_CHECKERS, BROWSER_CHECKERS, HTTP_CHECKERS, PINCODE_AWARE
from checkers import http as http_module
from checkers.common import CheckResult

ROOT = Path(__file__).parent
# Both overridable so a container can read config from, and log to, a volume.
CONFIG_PATH = Path(os.getenv("PS5_CONFIG_PATH") or ROOT / "config.json")
LOG_PATH = Path(os.getenv("PS5_LOG_PATH") or ROOT / "logs" / "run.log")

log = logging.getLogger("ps5")


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_PATH)],
    )


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"missing {CONFIG_PATH.name}")
    try:
        config = json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{CONFIG_PATH.name} is not valid JSON: {exc}") from exc

    pincodes = normalise_pincodes(config.get("pincodes") or config.get("pincode"))
    if not pincodes:
        raise SystemExit(f"set 'pincodes' (or 'pincode') in {CONFIG_PATH.name}")

    retailers = config.get("retailers") or {}
    unknown = set(retailers) - set(ALL_CHECKERS)
    if unknown:
        raise SystemExit(f"unknown retailer(s) in config: {', '.join(sorted(unknown))}")

    disabled = config.get("disabled") or []
    if isinstance(disabled, str):
        disabled = [disabled]
    unknown_disabled = set(disabled) - set(ALL_CHECKERS)
    if unknown_disabled:
        raise SystemExit(
            f"unknown retailer(s) in disabled: {', '.join(sorted(unknown_disabled))}"
        )

    overrides = config.get("pincode_overrides") or {}
    unknown_overrides = set(overrides) - set(ALL_CHECKERS)
    if unknown_overrides:
        raise SystemExit(
            "unknown retailer(s) in pincode_overrides: "
            f"{', '.join(sorted(unknown_overrides))}"
        )
    overrides = {
        retailer: normalise_pincodes(value)
        for retailer, value in overrides.items()
        if normalise_pincodes(value)
    }

    return {
        "pincodes": pincodes,
        "retailers": retailers,
        "pincode_overrides": overrides,
        "disabled": set(disabled),
        "alert_without_pincode_check": set(
            config.get("alert_without_pincode_check") or []
        ),
    }


def normalise_pincodes(value) -> list[str]:
    """Accept a single pincode or a list, and return a de-duplicated list."""
    if value is None:
        return []
    if isinstance(value, (str, int)):
        value = [value]
    seen, out = set(), []
    for item in value:
        pin = str(item).strip()
        if pin and pin not in seen:
            seen.add(pin)
            out.append(pin)
    return out


def pincodes_for(config: dict, retailer: str) -> list[str]:
    """Pincodes to check for a retailer — its override if set, else the defaults.

    Only pincode-aware retailers are checked at more than one: for the others
    every pincode yields the same national answer, so extra passes are waste.
    """
    pins = config["pincode_overrides"].get(retailer) or config["pincodes"]
    if retailer in PINCODE_AWARE:
        return pins
    return pins[:1]


def pincode_for(config: dict, retailer: str) -> str:
    """Primary pincode for a retailer, for display and logging."""
    return pincodes_for(config, retailer)[0]


def targets(config: dict, only: str | None) -> tuple[list, list]:
    """Build (retailer, url, pincode) triples, split by transport.

    Browser targets come out grouped by (retailer, pincode) so that per-pincode
    site state — Flipkart's delivery location — is primed once and then reused
    across that pincode's URLs, instead of being rebuilt per URL.
    """
    http_targets, browser_targets = [], []
    disabled = config.get("disabled") or set()
    for retailer, urls in config["retailers"].items():
        if only and retailer != only:
            continue
        # Disabled retailers keep their URLs in config but are not checked.
        # Skipped even when named by --check, so the flag cannot silently
        # re-enable something switched off deliberately.
        if retailer in disabled:
            continue
        for url in urls:
            if not isinstance(url, str) or not url.strip():
                continue
            bucket = browser_targets if retailer in BROWSER_CHECKERS else http_targets
            for pincode in pincodes_for(config, retailer):
                bucket.append((retailer, url.strip(), pincode))

    browser_targets.sort(key=lambda item: (item[0], item[2]))
    return http_targets, browser_targets


async def stamped(coro, pincode: str):
    """Record which pincode a check was made for, for state keys and messages."""
    result = await coro
    result.pincode = pincode
    return result


async def run_checks(config: dict, only: str | None) -> list:
    """Run http checkers concurrently, then browser checkers one at a time."""
    http_targets, browser_targets = targets(config, only)
    results = []

    async with httpx.AsyncClient(
        follow_redirects=True, proxy=http_module.PROXY_URL
    ) as client:
        if http_targets:
            results.extend(
                await asyncio.gather(
                    *(
                        stamped(
                            HTTP_CHECKERS[retailer].check(url, pincode, client=client),
                            pincode,
                        )
                        for retailer, url, pincode in http_targets
                    )
                )
            )

        if not browser_targets:
            return results

        from checkers.browser import BrowserUnavailable, session

        async def run_browser_targets(active) -> None:
            """Check each browser-path URL, sequentially, sharing one browser.

            Both transports are passed: the hybrid checkers try HTTP first and
            only reach for the browser when blocked.
            """
            for retailer, url, pincode in browser_targets:
                result = await BROWSER_CHECKERS[retailer].check(
                    url,
                    pincode,
                    client=client,
                    session_obj=active,
                )
                result.pincode = pincode
                results.append(result)

        try:
            # One browser for the whole pass, reused across every URL. Launching
            # per URL cost minutes once most retailers moved to the browser path.
            async with session() as active:
                await run_browser_targets(active)
        except BrowserUnavailable:
            # No Chromium (e.g. a bare local venv). The hybrid checkers can still
            # succeed over HTTP, so run them without a browser rather than
            # failing every one outright.
            log.warning("no browser available; trying HTTP-only for browser-path sites")
            await run_browser_targets(None)

    return results


def stock_message(result, pincode: str) -> str:
    if result.pincode_verified:
        header = f"🎮 PS5 IN STOCK — deliverable to {pincode}"
        caveat = ""
    else:
        header = "🎮 PS5 IN STOCK (national) — pincode NOT verified"
        caveat = (
            f"\n⚠️ Confirm delivery to {pincode} on the site. This retailer "
            "renders serviceability client-side, so the check sees national "
            "stock only."
        )

    return (
        f"{header}\n"
        f"Retailer: {result.retailer}\n"
        f"Product: {result.name or 'unknown'}\n"
        f"Price: {result.price or 'unknown'}\n"
        f"{result.url}"
        f"{caveat}"
    )


def heartbeat_message(results: list, config: dict) -> str:
    """Periodic proof-of-life, listing why nothing has been worth alerting on."""
    hours = state_module.HEARTBEAT_SECONDS // 3600
    lines = [
        f"💤 Still nothing in stock (last {hours}h). Tracker is alive.",
        "",
    ]
    for result in sorted(results, key=lambda r: (r.retailer, r.url)):
        if not result.ok:
            status = f"unknown — {result.error}"
        elif result.in_stock:
            status = "IN STOCK"
        else:
            status = "no stock"
        name = result.name or result.url
        lines.append(f"• [{result.retailer}] {status} — {name}")

    failures = sum(1 for r in results if not r.ok)
    if failures:
        lines += [
            "",
            f"⚠️ {failures} of {len(results)} checks could not be read — those are "
            "UNKNOWN, not confirmed sold out.",
        ]
    return "\n".join(lines)


def broken_message(result) -> str:
    return (
        "⚠️ Checker looks broken — treat this retailer as UNKNOWN, not sold out.\n"
        f"Retailer: {result.retailer}\n"
        f"Error: {result.error}\n"
        f"{result.url}"
    )


def report(results: list, config: dict, dry_run: bool) -> int:
    """Diff results against saved state, alert on transitions, persist. Returns exit code."""
    current = state_module.load()
    stock_alerts, broken_alerts = [], []

    for result in results:
        if not result.ok:
            status = f"ERROR ({result.error})"
        elif result.in_stock:
            status = "IN STOCK" if result.pincode_verified else "IN STOCK (national)"
        else:
            status = "no stock"
        log.info(
            "%s [%s] | %s | %s",
            result.retailer,
            result.pincode or pincode_for(config, result.retailer),
            status,
            result.url,
        )

        # Surface the parser's view of an unclear page straight into the logs,
        # so it can be diagnosed without shell access to the container.
        if result.debug:
            log.warning("  %s diagnostic | %s", result.retailer, result.debug)

        # National stock that was never confirmed deliverable here is not worth
        # waking someone for — that is the case where Flipkart holds stock but
        # refuses the pincode. Gate it BEFORE folding into state: recording it as
        # in-stock would consume the transition, so a later genuinely-deliverable
        # check would find nothing changed and stay silent.
        gated = result
        if result.ok and result.in_stock and not result.pincode_verified:
            log.info(
                "  not alerting: %s has national stock but delivery to %s is "
                "unverified",
                result.retailer,
                result.pincode or pincode_for(config, result.retailer),
            )
            gated = replace(result, in_stock=False)

        alert_stock, alert_broken = state_module.apply(current, gated)
        if alert_stock:
            stock_alerts.append(result)
        if alert_broken:
            broken_alerts.append(result)

    now = time.time()
    state_module.ensure_heartbeat_clock(current, now)
    # Any real alert is itself proof of life, so it resets the quiet timer.
    sending_alerts = bool(stock_alerts or broken_alerts)
    send_heartbeat = not sending_alerts and state_module.heartbeat_due(current, now)

    if dry_run:
        for result in stock_alerts:
            log.info(
                "[dry-run] would send stock alert:\n%s",
                stock_message(result, result.pincode or pincode_for(config, result.retailer)),
            )
        for result in broken_alerts:
            log.info("[dry-run] would send breakage alert:\n%s", broken_message(result))
        if send_heartbeat:
            log.info(
                "[dry-run] would send heartbeat:\n%s", heartbeat_message(results, config)
            )
        log.info(
            "[dry-run] %d stock alert(s), %d breakage alert(s), heartbeat=%s; "
            "state not written",
            len(stock_alerts),
            len(broken_alerts),
            send_heartbeat,
        )
        return 0

    for result in stock_alerts:
        notifiers.broadcast(
            stock_message(result, result.pincode or pincode_for(config, result.retailer))
        )
    for result in broken_alerts:
        notifiers.broadcast(broken_message(result), channels=notifiers.BREAKAGE_CHANNELS)

    if send_heartbeat:
        notifiers.broadcast(
            heartbeat_message(results, config), channels=notifiers.HEARTBEAT_CHANNELS
        )
    if sending_alerts or send_heartbeat:
        state_module.mark_heartbeat(current, now)

    state_module.save(current)
    failures = sum(1 for r in results if not r.ok)
    log.info(
        "done: %d checked, %d stock alert(s), %d failed check(s)",
        len(results),
        len(stock_alerts),
        failures,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PS5 availability tracker")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="check and print, but send nothing and do not write state",
    )
    parser.add_argument(
        "--check", metavar="RETAILER", help=f"run one of: {', '.join(ALL_CHECKERS)}"
    )
    parser.add_argument(
        "--test-notify",
        action="store_true",
        help="send a test message to every configured channel and exit",
    )
    args = parser.parse_args()

    setup_logging()
    load_dotenv(ROOT / ".env")

    if args.test_notify:
        notifiers.broadcast("✅ PS5 tracker test notification — channels are working.")
        return 0

    if args.check and args.check not in ALL_CHECKERS:
        raise SystemExit(f"unknown retailer '{args.check}'; try: {', '.join(ALL_CHECKERS)}")

    config = load_config()
    results = asyncio.run(run_checks(config, args.check))

    if not results:
        log.warning("no product URLs configured — add some to config.json")
        return 0

    return report(results, config, args.dry_run)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        logging.getLogger("ps5").exception("run failed")
        sys.exit(1)
