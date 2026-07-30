"""Sony Center (shopatsc.com) checker — headless browser.

shopatsc.com is Shopify. Its `<product>.js` endpoint is authoritative and was
used originally, but from a datacenter IP it returns 429 on every attempt even
with backoff. The rendered product page carries the same answer in schema.org
JSON-LD, so the browser path replaces it.
"""

from __future__ import annotations

from checkers.browser import Session, rendered_check
from checkers.common import CheckResult

RETAILER = "sonycenter"

WAIT_FOR = "script[type='application/ld+json'], form[action*='/cart/add']"


async def check(
    url: str,
    pincode: str,
    client=None,
    session_obj: Session | None = None,
) -> CheckResult:
    """Check one Sony Center product URL in a headless browser.

    Delivery signals are DISABLED here: every product page ships a hidden
    "this pin code is not serviceable!" node regardless of actual
    serviceability, so honouring it would report permanent false sell-outs.
    """
    return await rendered_check(
        RETAILER,
        url,
        wait_for=WAIT_FOR,
        wait_until="networkidle",
        delivery_signals=False,
        session_obj=session_obj,
    )
