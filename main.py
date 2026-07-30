#!/usr/bin/env python3
"""PS5 availability tracker — one pass per invocation, driven by cron.

Usage:
    python main.py                  # normal run: check, alert, persist state
    python main.py --dry-run        # check and print, send nothing, persist nothing
    python main.py --check croma    # run one retailer only
    python main.py --test-notify    # send a test message to every channel
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

import notifiers
import state as state_module
from checkers import ALL_CHECKERS, BROWSER_CHECKERS, HTTP_CHECKERS

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
LOG_PATH = ROOT / "logs" / "run.log"

log = logging.getLogger("ps5")


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)
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

    pincode = str(config.get("pincode", "")).strip()
    if not pincode:
        raise SystemExit(f"set 'pincode' in {CONFIG_PATH.name}")

    retailers = config.get("retailers") or {}
    unknown = set(retailers) - set(ALL_CHECKERS)
    if unknown:
        raise SystemExit(f"unknown retailer(s) in config: {', '.join(sorted(unknown))}")

    return {"pincode": pincode, "retailers": retailers}


def targets(config: dict, only: str | None) -> tuple[list, list]:
    """Split configured (retailer, url) pairs into http-based and browser-based."""
    http_targets, browser_targets = [], []
    for retailer, urls in config["retailers"].items():
        if only and retailer != only:
            continue
        for url in urls:
            if not isinstance(url, str) or not url.strip():
                continue
            bucket = browser_targets if retailer in BROWSER_CHECKERS else http_targets
            bucket.append((retailer, url.strip()))
    return http_targets, browser_targets


async def run_checks(config: dict, only: str | None) -> list:
    """Run http checkers concurrently, then browser checkers one at a time."""
    http_targets, browser_targets = targets(config, only)
    pincode = config["pincode"]
    results = []

    if http_targets:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            results.extend(
                await asyncio.gather(
                    *(
                        HTTP_CHECKERS[retailer].check(url, pincode, client=client)
                        for retailer, url in http_targets
                    )
                )
            )

    # Sequential: only one Chromium instance should ever exist at a time.
    for retailer, url in browser_targets:
        results.append(await BROWSER_CHECKERS[retailer].check(url, pincode))

    return results


def stock_message(result, pincode: str) -> str:
    return (
        "🎮 PS5 IN STOCK\n"
        f"Retailer: {result.retailer}\n"
        f"Product: {result.name or 'unknown'}\n"
        f"Price: {result.price or 'unknown'}\n"
        f"Pincode: {pincode}\n"
        f"{result.url}"
    )


def broken_message(result) -> str:
    return (
        "⚠️ Checker looks broken — treat this retailer as UNKNOWN, not sold out.\n"
        f"Retailer: {result.retailer}\n"
        f"Error: {result.error}\n"
        f"{result.url}"
    )


def report(results: list, pincode: str, dry_run: bool) -> int:
    """Diff results against saved state, alert on transitions, persist. Returns exit code."""
    current = state_module.load()
    stock_alerts, broken_alerts = [], []

    for result in results:
        status = (
            f"ERROR ({result.error})"
            if not result.ok
            else ("IN STOCK" if result.in_stock else "no stock")
        )
        log.info("%s | %s | %s", result.retailer, status, result.url)

        alert_stock, alert_broken = state_module.apply(current, result)
        if alert_stock:
            stock_alerts.append(result)
        if alert_broken:
            broken_alerts.append(result)

    if dry_run:
        for result in stock_alerts:
            log.info("[dry-run] would send stock alert:\n%s", stock_message(result, pincode))
        for result in broken_alerts:
            log.info("[dry-run] would send breakage alert:\n%s", broken_message(result))
        log.info(
            "[dry-run] %d stock alert(s), %d breakage alert(s); state not written",
            len(stock_alerts),
            len(broken_alerts),
        )
        return 0

    for result in stock_alerts:
        notifiers.broadcast(stock_message(result, pincode))
    for result in broken_alerts:
        notifiers.broadcast(broken_message(result), channels=notifiers.BREAKAGE_CHANNELS)

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

    return report(results, config["pincode"], args.dry_run)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        logging.getLogger("ps5").exception("run failed")
        sys.exit(1)
