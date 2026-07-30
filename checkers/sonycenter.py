"""Sony Center (shopatsc.com) checker.

shopatsc.com runs on Shopify, which exposes an authoritative product JSON at
`<product-url>.js` — verified to report `available` and price directly. That is
more reliable than the page HTML, whose text contains JS template strings like
"default title - sold out" that fool naive parsing.
"""

from __future__ import annotations

import httpx

from checkers.common import CheckResult, truncate
from checkers.generic import page_check
from checkers.http import request

RETAILER = "sonycenter"


def product_json_url(url: str) -> str:
    """Shopify serves product JSON at the product path with a .js suffix."""
    base = url.split("?", 1)[0].rstrip("/")
    return base + ".js"


async def check(
    url: str, pincode: str, client: httpx.AsyncClient | None = None
) -> CheckResult:
    """Check one Sony Center product via Shopify's product JSON.

    Falls back to the shared page check if the JSON endpoint is unavailable.
    """
    try:
        response = await request(product_json_url(url), client=client)

        if response.status_code != 200:
            # Never fall back on a rate limit — a second request against a site
            # already refusing us doubles the request rate and deepens the block.
            if response.status_code == 429:
                return CheckResult(
                    retailer=RETAILER,
                    url=url,
                    error="HTTP 429 rate limited (retries exhausted)",
                )
            return await page_check(RETAILER, url, client=client)

        data = response.json()
    except httpx.HTTPError as exc:
        return CheckResult(
            retailer=RETAILER, url=url, error=f"request failed: {type(exc).__name__}"
        )
    except ValueError:
        return await page_check(RETAILER, url, client=client)

    variants = data.get("variants") or []
    # Any purchasable variant means the product is buyable.
    available = bool(data.get("available")) or any(v.get("available") for v in variants)

    # Shopify quotes prices in paise.
    price = data.get("price")
    if price is None and variants:
        price = variants[0].get("price")

    return CheckResult(
        retailer=RETAILER,
        url=url,
        in_stock=available,
        price=f"₹{int(price) // 100:,}" if price else None,
        name=truncate(data.get("title")),
    )
