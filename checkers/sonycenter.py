"""Sony Center (shopatsc.com) checker — Shopify JSON first, browser fallback.

shopatsc.com is Shopify, whose `<product>.js` endpoint is authoritative and
cheap: it reports `available` and price directly. From a residential IP it
returns 200; from a datacenter IP it returns 429 on every attempt. When that
happens, fall back to rendering the page, which carries the same answer in
schema.org JSON-LD.

Delivery signals are DISABLED here: every product page ships a hidden
"this pin code is not serviceable!" node regardless of actual serviceability,
so honouring it would report permanent false sell-outs.
"""

from __future__ import annotations

import httpx

from checkers.browser import Session, rendered_check
from checkers.common import CheckResult, truncate
from checkers.http import request

RETAILER = "sonycenter"

WAIT_FOR = "script[type='application/ld+json'], form[action*='/cart/add']"


def product_json_url(url: str) -> str:
    """Shopify serves product JSON at the product path with a .js suffix."""
    base = url.split("?", 1)[0].rstrip("/")
    return base + ".js"


async def check(
    url: str,
    pincode: str,
    client: httpx.AsyncClient | None = None,
    session_obj: Session | None = None,
) -> CheckResult:
    """Check one Sony Center product, preferring the authoritative JSON."""
    data = None
    try:
        response = await request(product_json_url(url), client=client)
        if response.status_code == 200:
            data = response.json()
    except (httpx.HTTPError, ValueError):
        data = None

    if data is None:
        if session_obj is None:
            return CheckResult(
                retailer=RETAILER,
                url=url,
                error="product JSON unavailable (rate limited or blocked) and no browser",
            )
        return await rendered_check(
            RETAILER,
            url,
            wait_for=WAIT_FOR,
            wait_until="networkidle",
            delivery_signals=False,
            session_obj=session_obj,
        )

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
