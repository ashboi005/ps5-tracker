"""Discord webhook notifier."""

import os

import httpx

TIMEOUT = 10.0


def configured() -> bool:
    return bool(os.getenv("DISCORD_WEBHOOK_URL"))


def send(message: str) -> None:
    """POST a message to the configured Discord webhook."""
    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        raise RuntimeError("DISCORD_WEBHOOK_URL not set")

    response = httpx.post(webhook, json={"content": message}, timeout=TIMEOUT)
    response.raise_for_status()
