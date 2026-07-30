"""Retailer checkers, keyed by the retailer names used in config.json."""

from checkers import (
    amazon,
    croma,
    flipkart,
    reliancedigital,
    sonycenter,
    vijaysales,
)

# Amazon is listed separately in main.py because it needs a browser and runs
# sequentially; the rest are safe to run concurrently.
BROWSER_CHECKERS = {"amazon": amazon}

HTTP_CHECKERS = {
    "flipkart": flipkart,
    "croma": croma,
    "reliancedigital": reliancedigital,
    "vijaysales": vijaysales,
    "sonycenter": sonycenter,
}

ALL_CHECKERS = {**HTTP_CHECKERS, **BROWSER_CHECKERS}
