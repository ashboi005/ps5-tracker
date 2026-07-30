"""Shared HTTP fetching and stock-signal parsing for the httpx-based checkers.

Design note: rather than guessing at each retailer's internal JSON shapes, the
default path fetches the product page and looks for stock signals in the text.
Where a site's pincode-serviceability endpoint has been verified by hand, its
checker overrides `check()` to call that endpoint directly (much faster and
pincode-accurate). See README "Verifying a checker".
"""

from __future__ import annotations

import asyncio
import os
import random
import re

import httpx

# Datacenter IPs get throttled and tarpitted far more than home connections, so
# a VPS needs a longer patience than a laptop. Flipkart timed out at 10s from
# Coolify while working fine locally.
TIMEOUT = float(os.getenv("HTTP_TIMEOUT_SECONDS") or 25.0)

# Transient failures worth retrying: rate limits and upstream/proxy errors.
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = int(os.getenv("HTTP_MAX_ATTEMPTS") or 3)

# Several retailers block datacenter IPs outright — verified: Flipkart and Sony
# Center serve a home IP fine and refuse a VPS. No header or browser tweak fixes
# that, so the only code-level remedy is egressing through a different IP.
# Format: http://user:pass@host:port
PROXY_URL = os.getenv("PROXY_URL") or None

# A full browser-like header set. Verified necessary: with a bare User-Agent,
# Flipkart returns 403; with these it returns 200.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Ordered most-specific first: an out-of-stock phrase must win over a generic
# "add to cart" that many pages render even when unbuyable.
OUT_OF_STOCK_SIGNALS = [
    "out of stock",
    "sold out",
    "currently unavailable",
    "notify me",
    "coming soon",
    "temporarily unavailable",
    "not available",
    "unavailable",
]

IN_STOCK_SIGNALS = [
    "add to cart",
    "buy now",
    "add to bag",
    "in stock",
    "buy it now",
]

PRICE_RE = re.compile(r"(?:₹|Rs\.?\s?)\s?([\d,]{3,})")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


def retry_delay(attempt: int, retry_after: str | None = None) -> float:
    """Backoff before the next attempt, honouring Retry-After when given."""
    if retry_after:
        try:
            # Cap it: some sites answer with minutes, and the whole pass has to
            # finish well inside the check interval.
            return min(float(retry_after), 30.0)
        except ValueError:
            pass
    # Exponential, with jitter so repeated runs don't align into a thundering herd.
    return min(2.0**attempt, 15.0) + random.uniform(0, 1.0)


async def request(
    url: str, client: httpx.AsyncClient | None = None
) -> httpx.Response:
    """GET with retries on transient failures. Returns the final response.

    Non-retryable statuses (403, 404) come back as-is for the caller to read;
    only rate limits and server errors are retried.
    """
    last_exc: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        is_last = attempt == MAX_ATTEMPTS - 1
        try:
            if client is not None:
                response = await client.get(url, headers=HEADERS, timeout=TIMEOUT)
            else:
                async with httpx.AsyncClient(
                    follow_redirects=True, proxy=PROXY_URL
                ) as owned:
                    response = await owned.get(url, headers=HEADERS, timeout=TIMEOUT)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if is_last:
                break
            await asyncio.sleep(retry_delay(attempt))
            continue

        if response.status_code in RETRY_STATUS and not is_last:
            await asyncio.sleep(
                retry_delay(attempt, response.headers.get("Retry-After"))
            )
            continue
        return response

    raise last_exc if last_exc else httpx.HTTPError("request failed")


async def fetch(url: str, client: httpx.AsyncClient | None = None) -> str:
    """GET a page and return its text, following redirects."""
    response = await request(url, client=client)
    response.raise_for_status()
    return response.text


def strip_tags(html: str) -> str:
    """Crude tag strip — enough to search visible text for stock signals."""
    without_scripts = re.sub(
        r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I
    )
    return re.sub(r"<[^>]+>", " ", without_scripts)


# schema.org availability values, as they appear in JSON-LD product markup.
# Both Flipkart and Sony Center expose this, and it is authoritative — unlike
# page text, which is littered with JS template strings and unrelated copy.
AVAILABILITY_RE = re.compile(r'"availability"\s*:\s*"([^"]+)"', re.I)

IN_STOCK_TERMS = ("instock", "limitedavailability", "onlineonly", "instoreonly")
OUT_OF_STOCK_TERMS = (
    "outofstock",
    "soldout",
    "discontinued",
    "presale",
    "preorder",
    "backorder",
)


def parse_jsonld_availability(html: str) -> bool | None:
    """Read schema.org availability. Returns None when the markup is absent.

    A page may carry several offers; any in-stock offer means buyable.
    """
    values = [
        # Flipkart emits the URL both plainly and with escaped slashes.
        value.replace("\\u002f", "/").rsplit("/", 1)[-1].strip().lower()
        for value in AVAILABILITY_RE.findall(html)
    ]
    if not values:
        return None
    if any(v in IN_STOCK_TERMS for v in values):
        return True
    if any(v in OUT_OF_STOCK_TERMS for v in values):
        return False
    return None


def parse_stock_from_text(html: str) -> bool | None:
    """Fallback text heuristic. Returns None when the page is ambiguous.

    Deliberately refuses to guess: a page containing both "add to cart" and an
    out-of-stock phrase (common, thanks to JS templates) returns None so the
    caller can report "unknown" instead of a false "sold out".
    """
    text = strip_tags(html).lower()
    out_hits = [s for s in OUT_OF_STOCK_SIGNALS if s in text]
    in_hits = [s for s in IN_STOCK_SIGNALS if s in text]
    if out_hits and in_hits:
        return None
    if out_hits:
        return False
    if in_hits:
        return True
    return None


# Pincode-level refusals. These are stronger than schema.org availability: a
# seller can hold national stock (JSON-LD "InStock") while refusing to deliver
# to your pincode. Since only buyable-here stock matters, these win.
DELIVERY_BLOCKED_SIGNALS = (
    "not deliverable at your location",
    "not deliverable to your location",
    "delivery not available",
    "not serviceable",
    "no longer serviceable",
    "cannot be delivered to",
    "currently not serviceable",
    "not available at your location",
    "not deliverable",
)


def parse_delivery_blocked(html: str) -> bool:
    """True if the page explicitly refuses delivery to the current location."""
    text = strip_tags(html).lower()
    return any(signal in text for signal in DELIVERY_BLOCKED_SIGNALS)


def parse_stock(html: str, delivery_signals: bool = True) -> bool | None:
    """Is this buyable *and* deliverable here? None if the page is unclear.

    Precedence, strongest first:
      1. An explicit pincode-level delivery refusal -> not obtainable.
      2. schema.org availability -> authoritative national stock.
      3. Page text heuristics -> ambiguous pages return None, never a guess.

    `delivery_signals` is per-site because their reliability is per-site. Sony
    Center ships a hidden "this pin code is not serviceable!" node on every
    product page, so trusting it there would report false sell-outs; Flipkart's
    equivalent message is real. Sites opt out rather than in, so an unaudited
    site errs toward "cannot buy it" instead of over-promising.
    """
    if delivery_signals and parse_delivery_blocked(html):
        return False

    availability = parse_jsonld_availability(html)
    if availability is not None:
        return availability
    return parse_stock_from_text(html)


# Bot-protection interstitials. Verified from a Coolify VPS: Croma serves a
# 351-byte "Access Denied" page and Sony Center a 169-byte stub, where a home IP
# gets the real product page. Detected explicitly so the error says "blocked"
# rather than the misleading "could not determine stock".
BLOCK_TITLE_SIGNALS = (
    "access denied",
    "attention required",
    "just a moment",
    "are you a robot",
    "pardon our interruption",
    "request blocked",
    "forbidden",
    "too many requests",
    "service unavailable",
)

# A real product page is never this small. Sony Center's block stub was 169 bytes.
MIN_PRODUCT_HTML = 2_000


def detect_block(html: str) -> str | None:
    """Identify a bot-protection page. Returns a reason, or None if it looks real."""
    title = (parse_name(html) or "").lower()
    for signal in BLOCK_TITLE_SIGNALS:
        if signal in title:
            return f"blocked by site bot protection (page title: {title!r})"

    if len(html) < MIN_PRODUCT_HTML:
        return (
            f"blocked or empty response ({len(html)} bytes, no product markup) — "
            "usually this IP is refused; a real product page is far larger"
        )
    return None


def signal_report(html: str, limit: int = 3) -> str:
    """Compact account of what the parsers saw, for logging an UNKNOWN result.

    Includes surrounding context per hit, which is what distinguishes a real
    "Sold Out" from a JS template string like "default title - sold out".
    """
    text = strip_tags(html).lower()

    def hits(signals) -> list[str]:
        found = []
        for signal in signals:
            index = text.find(signal)
            if index == -1:
                continue
            start = max(0, index - 40)
            snippet = " ".join(text[start : index + len(signal) + 40].split())
            found.append(f"{signal!r} -> ...{snippet}...")
        return found[:limit]

    availability = AVAILABILITY_RE.findall(html)
    parts = [
        f"len={len(html)}",
        f"jsonld={availability[:limit] or 'none'}",
        f"title={parse_name(html)!r}",
    ]
    for label, signals in (
        ("delivery_blocked", DELIVERY_BLOCKED_SIGNALS),
        ("out_of_stock", OUT_OF_STOCK_SIGNALS),
        ("in_stock", IN_STOCK_SIGNALS),
    ):
        found = hits(signals)
        parts.append(f"{label}={found if found else 'none'}")
    return " | ".join(parts)


def parse_price(html: str) -> str | None:
    match = PRICE_RE.search(strip_tags(html))
    return f"₹{match.group(1)}" if match else None


def parse_name(html: str) -> str | None:
    match = TITLE_RE.search(html)
    if not match:
        return None
    name = " ".join(match.group(1).split())
    return name or None
