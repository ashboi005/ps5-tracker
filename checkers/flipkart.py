"""Flipkart checker — headless browser.

Verified from a Coolify VPS: plain httpx requests never complete at all (no
response, connection held until timeout across 3 attempts), while the same
requests succeed from a residential IP. A real browser gets through.
"""

from __future__ import annotations

from checkers.browser import Session, rendered_check
from checkers.common import CheckResult

RETAILER = "flipkart"

# Flipkart hydrates the buybox client-side; JSON-LD arrives with it.
WAIT_FOR = "script[type='application/ld+json'], ._2KpZ6l, button"


async def check(
    url: str,
    pincode: str,
    client=None,
    session_obj: Session | None = None,
) -> CheckResult:
    """Check one Flipkart product URL in a headless browser.

    Delivery signals are trusted here: "Not deliverable at your location" on a
    Flipkart product page is a real verdict, observed alongside JSON-LD InStock.
    """
    return await rendered_check(
        RETAILER,
        url,
        wait_for=WAIT_FOR,
        wait_until="networkidle",
        delivery_signals=True,
        session_obj=session_obj,
    )
