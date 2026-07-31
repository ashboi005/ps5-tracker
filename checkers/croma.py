"""Croma checker — Firefox, because Chromium is fingerprint-blocked.

Measured on the same machine and IP, same URL, same minute:
  chromium -> 351 bytes, "Access Denied"
  firefox  -> 614,173 bytes, real product page
  webkit   -> 631,656 bytes, real product page

So Croma's Akamai rejects Chromium's TLS/HTTP2 fingerprint specifically. Header
and JavaScript-level stealth cannot help (all Chromium variants returned a
byte-identical block page); switching engine does.

Croma emits no schema.org markup, so stock comes from the buybox text: a
purchasable product shows "Buy Now"/"Add to Cart", an unavailable one shows
"Sold Out"/"Notify Me". Pincode serviceability is NOT verified here — its
"Enter Pincode For Delivery Estimates" widget needs an Apply click that has not
been pinned down — so Croma results are national-only and will not raise alerts
unless you opt in per retailer (see README).
"""

from __future__ import annotations

from checkers.browser import Session, rendered_check
from checkers.common import CheckResult

RETAILER = "croma"
ENGINE = "firefox"

WAIT_FOR = ".pdp-title, .cp-product__title, h1"


async def check(
    url: str,
    pincode: str,
    client=None,
    session_obj: Session | None = None,
) -> CheckResult:
    """Check one Croma product URL in Firefox."""
    return await rendered_check(
        RETAILER,
        url,
        wait_for=WAIT_FOR,
        wait_until="networkidle",
        # Croma's page carries generic delivery copy ("Standard Delivery
        # Available") rather than a pincode verdict, so the delivery heuristics
        # would only add noise.
        delivery_signals=False,
        session_obj=session_obj,
        engine=ENGINE,
    )
