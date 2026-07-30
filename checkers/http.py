"""Shared HTTP fetching and stock-signal parsing for the httpx-based checkers.

Design note: rather than guessing at each retailer's internal JSON shapes, the
default path fetches the product page and looks for stock signals in the text.
Where a site's pincode-serviceability endpoint has been verified by hand, its
checker overrides `check()` to call that endpoint directly (much faster and
pincode-accurate). See README "Verifying a checker".
"""

import re

import httpx

TIMEOUT = 10.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
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


async def fetch(url: str, client: httpx.AsyncClient | None = None) -> str:
    """GET a page and return its text, following redirects."""
    if client is not None:
        response = await client.get(url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        return response.text

    async with httpx.AsyncClient(follow_redirects=True) as owned:
        response = await owned.get(url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        return response.text


def strip_tags(html: str) -> str:
    """Crude tag strip — enough to search visible text for stock signals."""
    without_scripts = re.sub(
        r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I
    )
    return re.sub(r"<[^>]+>", " ", without_scripts)


def parse_stock(html: str) -> bool:
    """Infer stock from page text. Out-of-stock signals take precedence."""
    text = strip_tags(html).lower()
    for signal in OUT_OF_STOCK_SIGNALS:
        if signal in text:
            return False
    return any(signal in text for signal in IN_STOCK_SIGNALS)


def parse_price(html: str) -> str | None:
    match = PRICE_RE.search(strip_tags(html))
    return f"₹{match.group(1)}" if match else None


def parse_name(html: str) -> str | None:
    match = TITLE_RE.search(html)
    if not match:
        return None
    name = " ".join(match.group(1).split())
    return name or None
