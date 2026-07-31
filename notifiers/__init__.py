"""Notification channels.

Every channel exposes `configured() -> bool` and `send(message: str) -> None`.
"""

import logging

from notifiers import discord, email, telegram

log = logging.getLogger(__name__)

# Stock alerts go everywhere.
STOCK_CHANNELS = (discord, telegram, email)

# Routine traffic goes to the chat channels, not email. Two channels is enough to
# tell you one of them has broken, while the inbox stays reserved for the thing
# worth acting on: actual stock.
BREAKAGE_CHANNELS = (discord, telegram)
HEARTBEAT_CHANNELS = (discord, telegram)


def broadcast(message: str, channels=STOCK_CHANNELS) -> None:
    """Send to every configured channel. One channel failing never blocks the rest."""
    for channel in channels:
        name = channel.__name__.rsplit(".", 1)[-1]
        if not channel.configured():
            log.warning("%s not configured, skipping", name)
            continue
        try:
            channel.send(message)
            log.info("notified via %s", name)
        except Exception as exc:  # noqa: BLE001 - a dead channel must not stop others
            log.error("%s notification failed: %s", name, exc)
