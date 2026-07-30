"""Retailer checkers, keyed by the retailer names used in config.json."""

from checkers import (
    amazon,
    croma,
    flipkart,
    reliancedigital,
    sonycenter,
    vijaysales,
)

# Checkers that may need a browser. They share one Chromium per pass (see
# browser.session) and receive both transports, because Flipkart and Sony Center
# are hybrids: they try HTTP first (which works from a residential IP) and reach
# for the browser only when blocked. Amazon always needs it; Croma returns 403 to
# httpx from every IP tested.
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
