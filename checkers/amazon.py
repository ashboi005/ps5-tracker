"""Amazon.in checker — headless browser.

Amazon's session/cookie flow resists plain HTTP replication, so this is the one
site that gets a real browser. Only one Chromium instance exists at a time and
it is closed as soon as the check finishes.
"""

from __future__ import annotations

from checkers.common import CheckResult, truncate

RETAILER = "amazon"
TIMEOUT_MS = 20_000

OUT_OF_STOCK_SIGNALS = ("currently unavailable", "out of stock", "unavailable")


async def check(url: str, pincode: str, client=None, session_obj=None) -> CheckResult:
    """Load an Amazon.in product page in Chromium and read its buybox."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return CheckResult(
            retailer=RETAILER,
            url=url,
            error="playwright not installed (pip install playwright && playwright install chromium)",
        )

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(args=["--no-sandbox"])
            try:
                page = await browser.new_page(
                    locale="en-IN",
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                )
                page.set_default_timeout(TIMEOUT_MS)
                await page.goto(url, wait_until="domcontentloaded")

                name = await _text(page, "#productTitle")
                price = await _text(page, ".a-price .a-offscreen")
                availability = (await _text(page, "#availability")) or ""
                has_buy_button = await page.locator("#add-to-cart-button").count() > 0

                lowered = availability.lower()
                unavailable = any(s in lowered for s in OUT_OF_STOCK_SIGNALS)
                in_stock = has_buy_button and not unavailable

                # Amazon.in URLs are required; amazon.com reflects US stock and
                # would silently track the wrong store.
                host = (await page.evaluate("location.hostname")) or ""
                if not host.endswith("amazon.in"):
                    return CheckResult(
                        retailer=RETAILER,
                        url=url,
                        name=truncate(name),
                        error=f"resolved to {host}, not amazon.in — use an amazon.in URL",
                    )

                return CheckResult(
                    retailer=RETAILER,
                    url=url,
                    in_stock=in_stock,
                    price=truncate(price, 40),
                    name=truncate(name),
                )
            finally:
                await browser.close()
    except Exception as exc:  # noqa: BLE001 - any browser failure is a failed check
        return CheckResult(
            retailer=RETAILER, url=url, error=f"{type(exc).__name__}: {exc}"
        )


async def _text(page, selector: str) -> str | None:
    """First match's text, or None if the selector is absent."""
    locator = page.locator(selector).first
    if await locator.count() == 0:
        return None
    try:
        return (await locator.inner_text()).strip() or None
    except Exception:  # noqa: BLE001 - detached/hidden node is just "no value"
        return None
