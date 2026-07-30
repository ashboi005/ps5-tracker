"""Retailer checkers, keyed by the retailer names used in config.json."""

from checkers import (
    amazon,
    croma,
    flipkart,
    reliancedigital,
    sonycenter,
    vijaysales,
)

# Browser-based checkers share one Chromium per pass (see browser.session).
# All four were moved here after live testing from a VPS: Amazon's session flow
# resists HTTP replication, Croma returns 403, Sony Center returns 429 on every
# attempt, and Flipkart drops the connection outright.
BROWSER_CHECKERS = {
    "amazon": amazon,
    "croma": croma,
    "flipkart": flipkart,
    "sonycenter": sonycenter,
}

HTTP_CHECKERS = {
    "reliancedigital": reliancedigital,
    "vijaysales": vijaysales,
}

ALL_CHECKERS = {**HTTP_CHECKERS, **BROWSER_CHECKERS}
