"""Page-fetch stock check shared by the httpx-based retailer checkers.

Each retailer module wraps this, and can pass site-specific overrides or replace
`check` entirely once its pincode-serviceability endpoint has been verified.
"""

import httpx

from checkers.common import CheckResult, truncate
from checkers.http import fetch, parse_name, parse_price, parse_stock


async def page_check(
    retailer: str,
    url: str,
    client: httpx.AsyncClient | None = None,
) -> CheckResult:
    """Fetch a product page and infer stock from its visible text."""
    try:
        html = await fetch(url, client=client)
    except httpx.HTTPStatusError as exc:
        return CheckResult(
            retailer=retailer, url=url, error=f"HTTP {exc.response.status_code}"
        )
    except httpx.TimeoutException:
        return CheckResult(retailer=retailer, url=url, error="timeout")
    except httpx.HTTPError as exc:
        return CheckResult(retailer=retailer, url=url, error=f"request failed: {exc}")

    return CheckResult(
        retailer=retailer,
        url=url,
        in_stock=parse_stock(html),
        price=parse_price(html),
        name=truncate(parse_name(html)),
    )
