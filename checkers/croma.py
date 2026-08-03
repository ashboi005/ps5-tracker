"""Croma checker — Firefox, with real pincode serviceability.

Engine matters: measured on the same machine, IP, URL and minute,
  chromium -> 351 bytes, "Access Denied"
  firefox  -> 614,173 bytes, real product page
  webkit   -> 631,656 bytes, real product page
Croma's Akamai rejects Chromium's TLS/HTTP2 fingerprint, before any JavaScript
runs — which is why every Chromium stealth variant returned a byte-identical
block page, and why switching engine is the fix rather than spoofing.

Serviceability is real here, and it matters: Croma's buybox shows "Buy Now /
Add to Cart" even where it cannot deliver. The verdict lives in a separate block
that appears only after a location is set, and reads either an expected delivery
or "Not Available for your pincode".

Flow, established by probing the live site:
  1. type the pincode into the site-wide "Select your Location" modal
     (input.pinElem)
  2. click its "Continue" button (button.sign-in-pincode-continue)
  3. wait for the "Delivery at: <city>, <pin>" block to render — it is injected
     asynchronously, and reading the page too early was why this looked broken

Croma emits no schema.org markup, so buybox text decides whether it is buyable
at all once delivery is confirmed possible.
"""

from __future__ import annotations

import contextlib
import logging

from checkers.browser import (
    SETTLE_MS,
    TIMEOUT_MS,
    BrowserUnavailable,
    Session,
    capture,
    settle,
)
from checkers.common import CheckResult, truncate
from checkers.http import detect_block, parse_name, parse_price, signal_report, strip_tags

RETAILER = "croma"
ENGINE = "firefox"

WAIT_FOR = ".pdp-title, h1"

PIN_INPUT = "input.pinElem"
PIN_SUBMIT = "button.sign-in-pincode-continue"
DELIVERY_BLOCK = "text=Delivery at"

UNDELIVERABLE = "not available for your pincode"
# Phrases that mean the verdict line itself has rendered. "Delivery at: <city>"
# appears first and the availability line lands a moment later, so waiting only
# for the block read every pincode as deliverable.
DELIVERED_PHRASES = ("expected delivery", "delivery by", "get it by", "standard delivery by")
# Proof the delivery verdict actually rendered. Without this marker we know
# nothing about serviceability — and must not read that silence as "deliverable".
DELIVERY_MARKER = "delivery at"
BUYABLE = ("add to cart", "buy now")

log = logging.getLogger("ps5")

# Whether priming succeeded, per pincode, for the current pass.
_applied_pins: dict[str, bool] = {}


async def prime_pincode(page, url: str, pincode: str) -> bool:
    """Set the site-wide delivery pincode. True if the verdict block confirms it."""
    await page.goto(url, wait_until="domcontentloaded")
    await settle(page, WAIT_FOR, "networkidle")

    field = page.locator(PIN_INPUT).first
    if not await field.count():
        return False
    with contextlib.suppress(Exception):
        await field.fill(pincode)
    await page.wait_for_timeout(2000)

    submit = page.locator(PIN_SUBMIT).first
    if not await submit.count():
        return False
    with contextlib.suppress(Exception):
        await submit.click(timeout=SETTLE_MS)
    await page.wait_for_timeout(5000)

    # The delivery block is injected async; reading before it lands is what made
    # earlier attempts look like the pincode had not applied at all.
    with contextlib.suppress(Exception):
        await page.wait_for_selector(DELIVERY_BLOCK, timeout=SETTLE_MS * 2)
    await page.wait_for_timeout(2000)

    # Confirm by the pincode actually appearing in that block.
    return pincode in await page.content()


def delivery_evidence(html: str) -> str | None:
    """The "Delivery at: ..." line, so an alert can be judged on its own."""
    text = strip_tags(html)
    marker = text.lower().find(DELIVERY_MARKER)
    if marker == -1:
        return None
    return truncate(text[marker : marker + 140], 140)


def read_verdict(html: str, url: str, pincode: str, applied: bool) -> CheckResult:
    """Turn a pincode-primed Croma page into a result."""
    text = strip_tags(html).lower()
    name = truncate(parse_name(html))
    common = {
        "retailer": RETAILER,
        "url": url,
        "pincode": pincode,
        "name": name,
        "evidence": delivery_evidence(html),
    }

    if UNDELIVERABLE in text:
        # Buybox may still say "Add to Cart" — this block overrides it.
        return CheckResult(**common, in_stock=False, pincode_verified=applied)

    # The verdict block must be present to claim anything about delivery. If it
    # never rendered, treat the result as unverified so it cannot raise an alert;
    # assuming "deliverable" here reported Croma as buyable at every pincode,
    # including ones the site explicitly refuses.
    verified = applied and (
        DELIVERY_MARKER in text and any(p in text for p in DELIVERED_PHRASES)
    )

    if any(signal in text for signal in BUYABLE):
        return CheckResult(
            **common,
            in_stock=True,
            price=parse_price(html),
            pincode_verified=verified,
        )

    if "sold out" in text or "notify me" in text or "out of stock" in text:
        return CheckResult(**common, in_stock=False, pincode_verified=verified)

    return CheckResult(
        **common,
        error="could not determine stock (no buybox signal, no delivery verdict)",
        debug=signal_report(html),
    )


async def await_verdict(page, timeout_ms: int = 20_000) -> str:
    """Poll until the delivery verdict line renders, then return the HTML.

    Croma injects "Delivery at: <city>, <pin>" before the availability line, so a
    read that stops at the block sees neither refusal nor estimate — and treating
    that silence as deliverable reported stock at pincodes Croma refuses.
    """
    waited = 0
    step = 1000
    html = await page.content()
    while waited < timeout_ms:
        text = strip_tags(html).lower()
        if UNDELIVERABLE in text or any(p in text for p in DELIVERED_PHRASES):
            return html
        await page.wait_for_timeout(step)
        waited += step
        html = await page.content()
    return html


async def check(
    url: str,
    pincode: str,
    client=None,
    session_obj: Session | None = None,
) -> CheckResult:
    """Check one Croma product URL against the given pincode."""
    if session_obj is None:
        return CheckResult(
            retailer=RETAILER,
            url=url,
            pincode=pincode,
            error="croma needs a browser (Firefox); none available",
        )

    try:
        # The location is context state, so one primed context per pincode — and
        # only the active one is kept, since many open contexts exhausted the
        # container.
        key = f"croma:{pincode}"
        await session_obj.close_contexts("croma:", keep=key)
        context, created = await session_obj.keyed_context(key, engine=ENGINE)
        page = await context.new_page()
        page.set_default_timeout(TIMEOUT_MS)
        try:
            if created:
                applied = await prime_pincode(page, url, pincode)
                if not applied:
                    log.warning(
                        "croma: could not apply pincode %s — results are "
                        "national-only and will not raise alerts",
                        pincode,
                    )
                _applied_pins[pincode] = applied
            else:
                applied = _applied_pins.get(pincode, False)

            await page.goto(url, wait_until="domcontentloaded")
            await settle(page, WAIT_FOR, "networkidle")
            with contextlib.suppress(Exception):
                await page.wait_for_selector(DELIVERY_BLOCK, timeout=SETTLE_MS * 2)
            html = await await_verdict(page)

            blocked = detect_block(html)
            result = (
                CheckResult(
                    retailer=RETAILER,
                    url=url,
                    pincode=pincode,
                    error=blocked,
                    debug=signal_report(html),
                )
                if blocked
                else read_verdict(html, url, pincode, applied)
            )
            # Capture only on a hit, while the page is still open.
            if result.in_stock:
                result.screenshot = await capture(page)
        finally:
            with contextlib.suppress(Exception):
                await page.close()
    except BrowserUnavailable as exc:
        return CheckResult(retailer=RETAILER, url=url, pincode=pincode, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - any browser failure is a failed check
        return CheckResult(
            retailer=RETAILER,
            url=url,
            pincode=pincode,
            error=f"{type(exc).__name__}: {exc}",
        )

    return result
