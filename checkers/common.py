"""Shared types for retailer checkers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CheckResult:
    """Outcome of checking one product URL at one pincode.

    `error` set means the check itself failed — this is deliberately distinct
    from `in_stock=False`, which means we successfully checked and there is no
    stock. Conflating the two would turn a broken scraper into a silent
    "sold out", which is the one failure mode that defeats the tracker.
    """

    retailer: str
    url: str
    in_stock: bool = False
    price: str | None = None
    name: str | None = None
    error: str | None = None

    # True only when the check actually confirmed serviceability for the
    # configured pincode. Most sites render that verdict client-side after
    # resolving your location, so an anonymous page fetch sees national stock
    # only. Alerts say which kind of hit they are, so a "national stock,
    # pincode unverified" result is never mistaken for a confirmed buy.
    pincode_verified: bool = False

    # Populated when a check comes back UNKNOWN: what the parsers actually saw.
    # Logged so an unclear page can be diagnosed from the deploy logs alone,
    # without shell access to the container.
    debug: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def truncate(text: str | None, limit: int = 120) -> str | None:
    """Trim scraped text so log lines and alerts stay readable."""
    if not text:
        return None
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
