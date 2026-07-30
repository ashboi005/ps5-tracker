"""Flipkart checker — httpx first, browser fallback.

From a residential IP plain httpx returns the full product page (200, ~1.7MB),
which is far cheaper than a browser render. From a datacenter IP the connection
is dropped entirely. So try HTTP, and fall back to the browser only when that
fails or comes back blocked.

Delivery signals are trusted here: "Not deliverable at your location" on a
Flipkart product page is a real verdict, observed alongside JSON-LD InStock.
"""

from __future__ import annotations

import httpx

from checkers.browser import Session, rendered_check
from checkers.common import CheckResult
from checkers.generic import page_check

RETAILER = "flipkart"

# Flipkart hydrates the buybox client-side; JSON-LD arrives with it.
WAIT_FOR = "script[type='application/ld+json'], button"


async def check(
    url: str,
    pincode: str,
    client: httpx.AsyncClient | None = None,
    session_obj: Session | None = None,
) -> CheckResult:
    """Check one Flipkart product URL, cheapest viable transport first."""
    result = await page_check(RETAILER, url, client=client)
    if result.ok or session_obj is None:
        return result

    # HTTP failed or was blocked — retry through the browser.
    return await rendered_check(
        RETAILER,
        url,
        wait_for=WAIT_FOR,
        wait_until="networkidle",
        delivery_signals=True,
        session_obj=session_obj,
    )
