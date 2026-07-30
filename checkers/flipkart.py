"""flipkart checker."""

import httpx

from checkers.common import CheckResult
from checkers.generic import page_check

RETAILER = "flipkart"


async def check(
    url: str, pincode: str, client: httpx.AsyncClient | None = None
) -> CheckResult:
    """Check one flipkart product URL.

    Currently infers stock from the product page text. To make this
    pincode-accurate, replace the body with a direct call to the site's
    serviceability endpoint (see README "Verifying a checker").
    """
    return await page_check(RETAILER, url, client=client)
