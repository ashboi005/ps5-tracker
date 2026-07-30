"""Flipkart checker — real pincode serviceability, via the browser.

Flipkart's product page does not reveal serviceability to an anonymous request:
the delivery verdict appears only after a location is set through its UI. That
matters because a listing can hold national stock (JSON-LD `InStock`) while
refusing your pincode — and stock you cannot receive is not worth an alert.

The pincode is set once per pass and then reused: it persists across product
pages within the same browser context (verified), so priming costs one extra
page load per run rather than one per URL.

Flow, established by probing the live site:
  1. click "Select Delivery Location"
  2. type the pincode into the "Search by area, street name, pin code" typeahead
  3. click the "<State> <pin> India" suggestion (pressing Enter does nothing)
  4. click "Confirm" on the map dialog
Afterwards the "Delivery details" block shows either an address plus a delivery
date, or "Not deliverable at your location".
"""

from __future__ import annotations

import contextlib
import logging

import httpx

from checkers.browser import (
    SETTLE_MS,
    TIMEOUT_MS,
    BrowserUnavailable,
    Session,
    settle,
)
from checkers.common import CheckResult, truncate
from checkers.generic import page_check
from checkers.http import (
    detect_block,
    parse_jsonld_availability,
    parse_name,
    parse_price,
    parse_stock_from_text,
    signal_report,
    strip_tags,
)

RETAILER = "flipkart"
CONTEXT_KEY = "flipkart"

WAIT_FOR = "script[type='application/ld+json'], button"

PIN_INPUT = "input[placeholder*='pin code' i]"
OPEN_WIDGET = "text=Select Delivery Location"

# Both phrasings appear on the same page: one beside the storage variants, one in
# the Delivery details block.
UNDELIVERABLE = (
    "not deliverable at your location",
    "not deliverable in your location",
)

# "Delivery details" appears only once a location has actually been applied.
LOCATION_APPLIED = "delivery details"
LOCATION_UNSET = "location not set"

log = logging.getLogger("ps5")


async def prime_pincode(page, url: str, pincode: str) -> bool:
    """Set the delivery pincode on a fresh context. True if it took effect."""
    await page.goto(url, wait_until="domcontentloaded")
    await settle(page, WAIT_FOR, "networkidle")

    try:
        await page.locator(OPEN_WIDGET).first.click(timeout=SETTLE_MS)
    except Exception:  # noqa: BLE001 - widget missing or renamed
        return False

    await page.wait_for_timeout(1500)
    try:
        await page.fill(PIN_INPUT, pincode)
    except Exception:  # noqa: BLE001
        return False

    # The typeahead needs a moment, and its suggestion must be clicked.
    await page.wait_for_timeout(3000)
    suggestion = page.locator(f"text={pincode}").first
    if not await suggestion.count():
        return False
    with contextlib.suppress(Exception):
        await suggestion.click(timeout=SETTLE_MS)

    await page.wait_for_timeout(4000)
    confirm = page.locator("text=Confirm").first
    if await confirm.count():
        with contextlib.suppress(Exception):
            await confirm.click(timeout=SETTLE_MS)

    await page.wait_for_timeout(5000)
    text = (await page.content()).lower()
    return LOCATION_APPLIED in text and LOCATION_UNSET not in text


def read_verdict(html: str, url: str, pincode_applied: bool) -> CheckResult:
    """Turn a pincode-primed Flipkart page into a result."""
    text = strip_tags(html).lower()
    name = truncate(parse_name(html))

    if any(phrase in text for phrase in UNDELIVERABLE):
        # National stock is irrelevant — it cannot reach this pincode.
        return CheckResult(
            retailer=RETAILER,
            url=url,
            in_stock=False,
            name=name,
            pincode_verified=pincode_applied,
        )

    # No refusal, so decide whether it is buyable at all. The delivery-text
    # heuristics are skipped here: this page already answered that question.
    in_stock = parse_jsonld_availability(html)
    if in_stock is None:
        in_stock = parse_stock_from_text(html)
    if in_stock is None:
        return CheckResult(
            retailer=RETAILER,
            url=url,
            name=name,
            error="could not determine stock (no schema.org markup, ambiguous text)",
            debug=signal_report(html),
        )

    return CheckResult(
        retailer=RETAILER,
        url=url,
        in_stock=in_stock,
        price=parse_price(html),
        name=name,
        pincode_verified=pincode_applied,
    )


async def check(
    url: str,
    pincode: str,
    client: httpx.AsyncClient | None = None,
    session_obj: Session | None = None,
) -> CheckResult:
    """Check one Flipkart product URL against the configured pincode."""
    if session_obj is None:
        # No browser available: an unverified national reading at best, which
        # main.py will not alert on.
        return await page_check(RETAILER, url, client=client)

    try:
        context, created = await session_obj.keyed_context(CONTEXT_KEY)
        page = await context.new_page()
        page.set_default_timeout(TIMEOUT_MS)
        try:
            if created:
                applied = await prime_pincode(page, url, pincode)
                if not applied:
                    log.warning(
                        "flipkart: could not apply pincode %s — results are "
                        "national-only and will not raise alerts",
                        pincode,
                    )
                # Remember it so later URLs in this pass agree.
                session_obj.flipkart_pincode_applied = applied  # type: ignore[attr-defined]
            else:
                applied = getattr(session_obj, "flipkart_pincode_applied", False)

            await page.goto(url, wait_until="domcontentloaded")
            await settle(page, WAIT_FOR, "networkidle")
            html = await page.content()
        finally:
            with contextlib.suppress(Exception):
                await page.close()
    except BrowserUnavailable as exc:
        return CheckResult(retailer=RETAILER, url=url, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - any browser failure is a failed check
        return CheckResult(
            retailer=RETAILER, url=url, error=f"{type(exc).__name__}: {exc}"
        )

    blocked = detect_block(html)
    if blocked:
        return CheckResult(
            retailer=RETAILER, url=url, error=blocked, debug=signal_report(html)
        )

    return read_verdict(html, url, applied)
