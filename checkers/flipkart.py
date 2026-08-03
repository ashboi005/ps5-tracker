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
    capture,
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

# Phrases meaning the delivery verdict itself has resolved. The block renders
# before its verdict line, so a read that stops at the block sees no refusal and
# wrongly concludes the item is deliverable — the cause of a false "in stock"
# alert for Noida 201301 that read "not deliverable" minutes later.
DELIVERABLE_PHRASES = (
    "delivery by",
    "delivery in",
    "free delivery",
    "expected delivery",
    "get it by",
)

log = logging.getLogger("ps5")

# Whether priming succeeded, per pincode, for the current pass. Keyed by pincode
# because each has its own browser context.
_applied_pins: dict[str, bool] = {}


async def prime_pincode(page, url: str, pincode: str, attempts: int = 2) -> bool:
    """Set the delivery pincode, retrying once. True if it took effect.

    The typeahead is timing-sensitive — in a 6-pincode run two of six failed on
    the first try — so a failed attempt is retried before giving up.
    """
    for attempt in range(attempts):
        if await _try_prime(page, url, pincode):
            return True
        if attempt < attempts - 1:
            log.info("flipkart: pincode %s did not take, retrying", pincode)
            await page.wait_for_timeout(2000)
    return False


async def _try_prime(page, url: str, pincode: str) -> bool:
    """One attempt at setting the delivery pincode."""
    await page.goto(url, wait_until="domcontentloaded")
    await settle(page, WAIT_FOR, "networkidle")

    # Once a location is set, the widget is labelled with the address instead.
    for opener in (OPEN_WIDGET, "text=Deliver To", "text=Change"):
        try:
            target = page.locator(opener).first
            if await target.count():
                await target.click(timeout=SETTLE_MS)
                break
        except Exception:  # noqa: BLE001 - try the next label
            continue
    else:
        return False

    await page.wait_for_timeout(1500)
    try:
        await page.fill(PIN_INPUT, pincode)
    except Exception:  # noqa: BLE001
        return False

    # The typeahead needs a moment, and its suggestion must be clicked.
    await page.wait_for_timeout(4000)
    suggestion = page.locator(f"text={pincode}").first
    try:
        await suggestion.wait_for(state="visible", timeout=SETTLE_MS)
    except Exception:  # noqa: BLE001 - no suggestion appeared
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


def delivery_evidence(html: str) -> str | None:
    """The "Delivery details ..." line, so an alert can be judged on its own."""
    text = strip_tags(html)
    marker = text.lower().find(LOCATION_APPLIED)
    if marker == -1:
        return None
    return truncate(text[marker : marker + 160], 160)


def read_verdict(html: str, url: str, pincode_applied: bool) -> CheckResult:
    """Turn a pincode-primed Flipkart page into a result."""
    text = strip_tags(html).lower()
    name = truncate(parse_name(html))
    evidence = delivery_evidence(html)

    if any(phrase in text for phrase in UNDELIVERABLE):
        # National stock is irrelevant — it cannot reach this pincode.
        return CheckResult(
            retailer=RETAILER,
            url=url,
            in_stock=False,
            name=name,
            evidence=evidence,
            pincode_verified=pincode_applied,
        )

    # Only claim deliverability when the page actually said so. Without a
    # resolved verdict this is national stock at best, so it stays unverified and
    # cannot raise an alert.
    resolved = any(phrase in text for phrase in DELIVERABLE_PHRASES)
    verified = pincode_applied and resolved

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
            evidence=evidence,
            error="could not determine stock (no schema.org markup, ambiguous text)",
            debug=signal_report(html),
        )

    return CheckResult(
        retailer=RETAILER,
        url=url,
        in_stock=in_stock,
        price=parse_price(html),
        name=name,
        evidence=evidence,
        pincode_verified=verified,
    )


async def await_verdict(page, timeout_ms: int = 20_000) -> str:
    """Poll until the delivery verdict resolves, then return the HTML.

    Returns whatever it has on timeout; the caller treats a missing verdict as
    unverified rather than assuming either answer.
    """
    waited = 0
    step = 1000
    html = await page.content()
    while waited < timeout_ms:
        text = strip_tags(html).lower()
        if any(p in text for p in UNDELIVERABLE) or any(
            p in text for p in DELIVERABLE_PHRASES
        ):
            return html
        await page.wait_for_timeout(step)
        waited += step
        html = await page.content()
    return html


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
        # One primed context per pincode: the delivery location is context
        # state, so pincodes must not share it. Only the active one is kept —
        # holding six open at once exhausted the container.
        key = f"flipkart:{pincode}"
        await session_obj.close_contexts("flipkart:", keep=key)
        context, created = await session_obj.keyed_context(key)
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
                # Remember per pincode, so later URLs in this pass agree.
                _applied_pins[pincode] = applied
            else:
                applied = _applied_pins.get(pincode, False)

            await page.goto(url, wait_until="domcontentloaded")
            await settle(page, WAIT_FOR, "networkidle")
            html = await await_verdict(page)

            blocked = detect_block(html)
            result = (
                CheckResult(
                    retailer=RETAILER, url=url, error=blocked, debug=signal_report(html)
                )
                if blocked
                else read_verdict(html, url, applied)
            )
            if result.in_stock:
                result.screenshot = await capture(page)
        finally:
            with contextlib.suppress(Exception):
                await page.close()
    except BrowserUnavailable as exc:
        return CheckResult(retailer=RETAILER, url=url, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - any browser failure is a failed check
        return CheckResult(
            retailer=RETAILER, url=url, error=f"{type(exc).__name__}: {exc}"
        )

    return result
