"""Notification channels.

Every channel exposes `configured() -> bool` and `send(message: str) -> None`.
"""

import logging

from notifiers import discord, email, telegram

log = logging.getLogger(__name__)

# Stock alerts go everywhere.
STOCK_CHANNELS = (discord, telegram, email)

# Checker-broken alerts go to one channel only, so maintenance noise does not
# hit every inbox.
BREAKAGE_CHANNELS = STOCK_CHANNELS

# Proof-of-life goes everywhere too. It was Telegram-only at first, on the theory
# that routine traffic in the inbox trains you to ignore it — but a heartbeat you
# only see on one channel cannot tell you that channel has broken, which is half
# of what it is for.
HEARTBEAT_CHANNELS = STOCK_CHANNELS


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
