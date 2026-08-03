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
from checkers.http import (
    PROXY_URL,
    detect_block,
    parse_name,
    parse_price,
    parse_stock,
    signal_report,
)

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
    """Live browsers, reused for many page loads within one pass.

    Engines are launched lazily and cached, because they are not
    interchangeable: Croma serves Chromium a 351-byte "Access Denied" while
    returning the real 614KB page to Firefox and WebKit. The block is at the
    TLS/HTTP2 layer, so the engine choice — not the user agent — is what matters.
    """

    def __init__(self, playwright) -> None:
        self._playwright = playwright
        self._browsers: dict[str, object] = {}
        self._contexts: dict[str, object] = {}

    async def browser(self, engine: str = "chromium"):
        cached = self._browsers.get(engine)
        if cached is not None:
            return cached
        launcher = getattr(self._playwright, engine)
        kwargs: dict = {}
        if engine == "chromium":
            kwargs["args"] = ["--no-sandbox"]
        if PROXY_URL:
            kwargs["proxy"] = {"server": PROXY_URL}
        browser = await launcher.launch(**kwargs)
        self._browsers[engine] = browser
        return browser

    async def _new_context(self, engine: str = "chromium"):
        browser = await self.browser(engine)
        return await browser.new_context(
            locale="en-IN",
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 900},
            timezone_id="Asia/Kolkata",
        )

    async def keyed_context(self, key: str, engine: str = "chromium"):
        """A context that persists across URLs, keyed by retailer.

        Needed where site state must be established once and then reused —
        Flipkart's delivery pincode is set through its UI and then applies to
        every subsequent product page in the same context. Returns
        (context, created_now) so callers can prime a fresh one.
        """
        existing = self._contexts.get(key)
        if existing is not None:
            return existing, False
        context = await self._new_context(engine)
        self._contexts[key] = context
        return context, True

    async def close_contexts(self, prefix: str, keep: str | None = None) -> None:
        """Close cached contexts under `prefix`, optionally keeping one.

        Holding many browser contexts open at once exhausted the container:
        six concurrent Chromium contexts produced ERR_NAME_NOT_RESOLVED and
        navigation timeouts. Callers that iterate over primed contexts (one per
        pincode) use this to keep only the active one alive.
        """
        for key in [k for k in self._contexts if k.startswith(prefix) and k != keep]:
            with contextlib.suppress(Exception):
                await self._contexts[key].close()
            del self._contexts[key]

    async def close(self) -> None:
        for context in self._contexts.values():
            with contextlib.suppress(Exception):
                await context.close()
        self._contexts.clear()
        for browser in self._browsers.values():
            with contextlib.suppress(Exception):
                await browser.close()
        self._browsers.clear()

    async def render(
        self,
        url: str,
        wait_for: str | None = None,
        wait_until: str = "domcontentloaded",
        engine: str = "chromium",
    ) -> str:
        # A fresh context per page keeps cookies from one retailer out of the
        # next, while still avoiding a full browser relaunch.
        context = await self._new_context(engine)
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
        active = Session(playwright)
        try:
            yield active
        finally:
            await active.close()


async def capture(page) -> bytes | None:
    """Screenshot the open page. Viewport only — a full-page shot of these
    product pages runs to megabytes and chat uploads are size-capped.

    Returns None on failure: a missing screenshot must never cost you the alert.
    """
    try:
        return await page.screenshot(full_page=False, type="png")
    except Exception:  # noqa: BLE001 - the alert matters more than the image
        return None


async def settle(page, wait_for: str | None, wait_until: str) -> None:
    """Best-effort waits after navigation. Never fatal — see Session.render."""
    if wait_until == "networkidle":
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
    if wait_for:
        with contextlib.suppress(Exception):
            await page.wait_for_selector(wait_for, timeout=SETTLE_MS)


async def render(
    url: str,
    wait_for: str | None = None,
    wait_until: str = "domcontentloaded",
    engine: str = "chromium",
) -> str:
    """One-shot render, for tools that check a single page."""
    async with session() as active:
        return await active.render(
            url, wait_for=wait_for, wait_until=wait_until, engine=engine
        )


async def rendered_check(
    retailer: str,
    url: str,
    wait_for: str | None = None,
    wait_until: str = "domcontentloaded",
    delivery_signals: bool = True,
    session_obj: Session | None = None,
    engine: str = "chromium",
) -> CheckResult:
    """Render a page, then read stock from it with the shared parsers."""
    try:
        if session_obj is not None:
            html = await session_obj.render(
                url, wait_for=wait_for, wait_until=wait_until, engine=engine
            )
        else:
            html = await render(
                url, wait_for=wait_for, wait_until=wait_until, engine=engine
            )
    except BrowserUnavailable as exc:
        return CheckResult(retailer=retailer, url=url, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - any browser failure is a failed check
        return CheckResult(
            retailer=retailer, url=url, error=f"{type(exc).__name__}: {exc}"
        )

    blocked = detect_block(html)
    if blocked:
        return CheckResult(
            retailer=retailer, url=url, error=blocked, debug=signal_report(html)
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
