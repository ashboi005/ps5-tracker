"""Headless-Chromium page rendering, for sites that block plain HTTP.

Most retailers here need this: from a datacenter IP, Croma returns 403, Sony
Center returns 429 on every attempt, and Flipkart drops the connection entirely.
A real browser's TLS fingerprint and header ordering get through where httpx
does not.

One browser is launched per pass and reused across every check (see `session`),
rather than one launch per URL — with ~9 URLs that difference is minutes.
"""

from __future__ import annotations

import contextlib

from checkers.common import CheckResult, truncate
from checkers.http import parse_name, parse_price, parse_stock, signal_report

TIMEOUT_MS = 30_000

# Shorter budget for the optional "let the page settle" waits. These are
# best-effort, and with several URLs per pass a long one each would blow past
# the check interval.
SETTLE_MS = 10_000

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class BrowserUnavailable(RuntimeError):
    """Playwright or its Chromium build is not installed."""


class Session:
    """A live browser, reused for many page loads within one pass."""

    def __init__(self, browser) -> None:
        self._browser = browser

    async def render(
        self,
        url: str,
        wait_for: str | None = None,
        wait_until: str = "domcontentloaded",
    ) -> str:
        # A fresh context per page keeps cookies from one retailer out of the
        # next, while still avoiding a full browser relaunch.
        context = await self._browser.new_context(
            locale="en-IN", user_agent=USER_AGENT
        )
        try:
            page = await context.new_page()
            page.set_default_timeout(TIMEOUT_MS)

            # Always navigate on domcontentloaded, which reliably fires. Pages
            # carrying ad or analytics sockets may never reach networkidle, and
            # waiting for it in goto() would fail the whole check.
            await page.goto(url, wait_until="domcontentloaded")

            if wait_until == "networkidle":
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state(
                        "networkidle", timeout=SETTLE_MS
                    )
            if wait_for:
                with contextlib.suppress(Exception):
                    await page.wait_for_selector(wait_for, timeout=SETTLE_MS)
            return await page.content()
        finally:
            await context.close()


@contextlib.asynccontextmanager
async def session():
    """Launch one browser for the duration of a pass.

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
            yield Session(browser)
        finally:
            await browser.close()


async def render(
    url: str,
    wait_for: str | None = None,
    wait_until: str = "domcontentloaded",
) -> str:
    """One-shot render, for tools that check a single page."""
    async with session() as active:
        return await active.render(url, wait_for=wait_for, wait_until=wait_until)


async def rendered_check(
    retailer: str,
    url: str,
    wait_for: str | None = None,
    wait_until: str = "domcontentloaded",
    delivery_signals: bool = True,
    session_obj: Session | None = None,
) -> CheckResult:
    """Render a page, then read stock from it with the shared parsers."""
    try:
        if session_obj is not None:
            html = await session_obj.render(
                url, wait_for=wait_for, wait_until=wait_until
            )
        else:
            html = await render(url, wait_for=wait_for, wait_until=wait_until)
    except BrowserUnavailable as exc:
        return CheckResult(retailer=retailer, url=url, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - any browser failure is a failed check
        return CheckResult(
            retailer=retailer, url=url, error=f"{type(exc).__name__}: {exc}"
        )

    in_stock = parse_stock(html, delivery_signals=delivery_signals)
    name = truncate(parse_name(html))
    if in_stock is None:
        return CheckResult(
            retailer=retailer,
            url=url,
            name=name,
            error="could not determine stock (no schema.org markup, ambiguous text)",
            debug=signal_report(html),
        )

    return CheckResult(
        retailer=retailer,
        url=url,
        in_stock=in_stock,
        price=parse_price(html),
        name=name,
    )
