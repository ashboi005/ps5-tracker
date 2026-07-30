"""Telegram bot notifier."""

import os

import httpx

TIMEOUT = 10.0


def configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def send(message: str) -> None:
    """Send a message via the Telegram Bot API."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        raise RuntimeError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")

    response = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": True,
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
