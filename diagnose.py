#!/usr/bin/env python3
"""Report what the parsers actually see on a page, to debug an UNKNOWN result.

Run this in the container when a checker reports "could not determine stock" —
the retailer sites behave differently from a VPS than from a laptop, so the only
reliable way to see the real markup is from where the tracker runs.

    docker compose exec ps5-tracker python diagnose.py croma
    docker compose exec ps5-tracker python diagnose.py croma --save /data/croma.html
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from checkers import BROWSER_CHECKERS
from checkers.http import (
    AVAILABILITY_RE,
    DELIVERY_BLOCKED_SIGNALS,
    IN_STOCK_SIGNALS,
    OUT_OF_STOCK_SIGNALS,
    fetch,
    parse_jsonld_availability,
    parse_name,
    parse_price,
    parse_stock,
    strip_tags,
)

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def context(text: str, needle: str, width: int = 70) -> str:
    """Show surrounding text, so a template-string hit is obvious as one."""
    index = text.find(needle)
    start = max(0, index - width // 2)
    return " ".join(text[start : index + len(needle) + width // 2].split())


async def get_html(retailer: str, url: str) -> str:
    if retailer in BROWSER_CHECKERS:
        from checkers.browser import render

        wait_for = getattr(BROWSER_CHECKERS[retailer], "WAIT_FOR", None)
        return await render(url, wait_for=wait_for, wait_until="networkidle")
    return await fetch(url)


async def diagnose(retailer: str, url: str, save: Path | None) -> None:
    print("=" * 78)
    print(f"{retailer}  {url}")
    print("=" * 78)

    try:
        html = await get_html(retailer, url)
    except Exception as exc:  # noqa: BLE001 - report whatever went wrong
        print(f"  FETCH FAILED: {type(exc).__name__}: {exc}")
        return

    text = strip_tags(html).lower()
    print(f"  html length : {len(html)}")
    print(f"  title       : {parse_name(html)}")
    print(f"  price       : {parse_price(html)}")
    print(f"  VERDICT     : {parse_stock(html)}   (None = unknown)")

    values = AVAILABILITY_RE.findall(html)
    print(f"\n  schema.org availability values ({len(values)}):")
    for value in values[:10] or ["    (none found)"]:
        print(f"    {value}")
    print(f"  -> jsonld verdict: {parse_jsonld_availability(html)}")

    print("\n  delivery-blocked signals:")
    hits = [s for s in DELIVERY_BLOCKED_SIGNALS if s in text]
    for signal in hits or ["    (none)"]:
        print(f"    HIT {signal!r}" if hits else signal)
        if hits:
            print(f"        ...{context(text, signal)}...")

    print("\n  out-of-stock text signals:")
    out_hits = [s for s in OUT_OF_STOCK_SIGNALS if s in text]
    for signal in out_hits or ["    (none)"]:
        print(f"    HIT {signal!r}" if out_hits else signal)
        if out_hits:
            print(f"        ...{context(text, signal)}...")

    print("\n  in-stock text signals:")
    in_hits = [s for s in IN_STOCK_SIGNALS if s in text]
    for signal in in_hits or ["    (none)"]:
        print(f"    HIT {signal!r}" if in_hits else signal)
        if in_hits:
            print(f"        ...{context(text, signal)}...")

    if out_hits and in_hits:
        print(
            "\n  => AMBIGUOUS: both kinds present. Usually a related-products\n"
            "     carousel, or a JS template string. Needs a scoped selector."
        )

    if save:
        save.write_text(html)
        print(f"\n  saved rendered html -> {save}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("retailer", help="retailer key from config.json")
    parser.add_argument("--url", help="one URL (default: all configured for it)")
    parser.add_argument("--save", type=Path, help="write rendered HTML here")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")

    import main as main_module

    config = main_module.load_config()
    urls = [args.url] if args.url else config["retailers"].get(args.retailer, [])
    if not urls:
        print(f"no URLs configured for {args.retailer!r}")
        print(f"configured: {json.dumps({k: len(v) for k, v in config['retailers'].items()})}")
        return 1

    for url in urls:
        await diagnose(args.retailer, url, args.save)
        # Only the first page is worth saving; later ones would overwrite it.
        args.save = None
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
