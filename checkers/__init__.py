"""Retailer checkers, keyed by the retailer names used in config.json."""

from checkers import (
    amazon,
    croma,
    flipkart,
    reliancedigital,
    sonycenter,
    vijaysales,
)

# Browser-based checkers run sequentially so only one Chromium exists at a time.
# Amazon's session flow resists HTTP replication; Croma (and its API) return 403
# to httpx regardless of headers.
BROWSER_CHECKERS = {"amazon": amazon, "croma": croma}

HTTP_CHECKERS = {
    "flipkart": flipkart,
    "reliancedigital": reliancedigital,
    "vijaysales": vijaysales,
    "sonycenter": sonycenter,
}

ALL_CHECKERS = {**HTTP_CHECKERS, **BROWSER_CHECKERS}
