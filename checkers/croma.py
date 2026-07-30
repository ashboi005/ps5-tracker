"""Croma checker — headless browser.

Verified: croma.com product pages and api.croma.com both return 403 to httpx
even with a full browser-like header set (Akamai). A real browser is required.
"""

from __future__ import annotations

from checkers.browser import rendered_check
from checkers.common import CheckResult

RETAILER = "croma"

# Croma renders the buybox client-side; wait for one of these before reading.
WAIT_FOR = "script[type='application/ld+json'], .pdp-add-to-cart, #buyNow"


async def check(url: str, pincode: str, client=None) -> CheckResult:
    """Check one Croma product URL in a headless browser.

    Waits for network idle: with domcontentloaded the JSON-LD had not been
    injected yet, so checks reported "no schema.org markup".
    """
    return await rendered_check(
        RETAILER, url, wait_for=WAIT_FOR, wait_until="networkidle"
    )
