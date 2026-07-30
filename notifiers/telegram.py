"""Telegram bot notifier."""

from __future__ import annotations

import os
import re

import httpx

TIMEOUT = 10.0

# httpx puts the failing URL into its exception text, and for Telegram that URL
# contains the bot token. Unredacted, one failure writes the token into run.log
# (and into anything shipping those logs elsewhere).
TOKEN_IN_URL_RE = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")


def redact(text: str) -> str:
    """Strip bot tokens out of anything headed for a log."""
    return TOKEN_IN_URL_RE.sub("/bot<redacted>", text)


def configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def send(message: str) -> None:
    """Send a message via the Telegram Bot API.

    Raises with a redacted, actionable message — Telegram's most common failure
    is a 400 "chat not found", which means you have not sent /start to the bot.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        raise RuntimeError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")

    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(redact(str(exc))) from None

    if response.status_code == 200:
        return

    description = ""
    try:
        description = response.json().get("description", "")
    except ValueError:
        description = response.text[:200]

    hint = ""
    if "chat not found" in description.lower():
        hint = (
            " — open the bot in Telegram and send it /start; bots cannot message "
            "a user who has not messaged them first"
        )

    # Deliberately does not include the URL, so the token cannot reach the log.
    raise RuntimeError(
        f"Telegram API {response.status_code}: {description}{hint}"
    )
