"""Page-fetch stock check shared by the httpx-based retailer checkers.

Each retailer module wraps this, and can pass site-specific overrides or replace
`check` entirely once its pincode-serviceability endpoint has been verified.
"""

from __future__ import annotations

import httpx

from checkers.common import CheckResult, truncate
from checkers.http import (
    detect_block,
    fetch,
    parse_name,
    parse_price,
    parse_stock,
    signal_report,
)


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

    blocked = detect_block(html)
    if blocked:
        return CheckResult(
            retailer=retailer, url=url, error=blocked, debug=signal_report(html)
        )

    in_stock = parse_stock(html)
    if in_stock is None:
        # No authoritative signal and ambiguous text. Reporting "no stock" here
        # would be a silent false negative, so surface it as a failed check.
        return CheckResult(
            retailer=retailer,
            url=url,
            name=truncate(parse_name(html)),
            error="could not determine stock (no schema.org markup, ambiguous text)",
            debug=signal_report(html),
        )

    return CheckResult(
        retailer=retailer,
        url=url,
        in_stock=in_stock,
        price=parse_price(html),
        name=truncate(parse_name(html)),
    )
