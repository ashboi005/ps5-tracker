"""Headless-Chromium page rendering, for sites that block plain HTTP.

Used only where necessary — Croma and its API return 403 to httpx regardless of
headers (Akamai), and Amazon's session flow resists replication. Callers run
sequentially so only one browser exists at a time; it closes after each check.
"""

from __future__ import annotations

from checkers.common import CheckResult, truncate
from checkers.http import parse_name, parse_price, parse_stock

TIMEOUT_MS = 25_000

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class BrowserUnavailable(RuntimeError):
    """Playwright or its Chromium build is not installed."""


async def render(url: str, wait_for: str | None = None) -> str:
    """Load a page in headless Chromium and return its rendered HTML.

    Raises BrowserUnavailable if Playwright is missing, so callers can report a
    setup problem distinctly from a site problem.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise BrowserUnavailable(
            "playwright not installed (pip install playwright "
            "&& playwright install chromium)"
        ) from exc

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=["--no-sandbox"])
        try:
            page = await browser.new_page(locale="en-IN", user_agent=USER_AGENT)
            page.set_default_timeout(TIMEOUT_MS)
            await page.goto(url, wait_until="domcontentloaded")
            if wait_for:
                try:
                    await page.wait_for_selector(wait_for, timeout=TIMEOUT_MS)
                except Exception:  # noqa: BLE001 - absent selector is not fatal
                    pass
            return await page.content()
        finally:
            await browser.close()


async def rendered_check(
    retailer: str, url: str, wait_for: str | None = None
) -> CheckResult:
    """Render a page, then read stock from it with the shared parsers."""
    try:
        html = await render(url, wait_for=wait_for)
    except BrowserUnavailable as exc:
        return CheckResult(retailer=retailer, url=url, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - any browser failure is a failed check
        return CheckResult(
            retailer=retailer, url=url, error=f"{type(exc).__name__}: {exc}"
        )

    in_stock = parse_stock(html)
    name = truncate(parse_name(html))
    if in_stock is None:
        return CheckResult(
            retailer=retailer,
            url=url,
            name=name,
            error="could not determine stock (no schema.org markup, ambiguous text)",
        )

    return CheckResult(
        retailer=retailer,
        url=url,
        in_stock=in_stock,
        price=parse_price(html),
        name=name,
    )
