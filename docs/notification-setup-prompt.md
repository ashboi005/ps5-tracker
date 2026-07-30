# Prompt for getting Discord + Telegram credentials

Paste everything below into another AI assistant.

---

I'm setting up notifications for a self-hosted Python stock-tracker script. The
script reads credentials from a `.env` file and sends alerts via HTTP. I need
step-by-step instructions to obtain two sets of credentials. I'm on a Mac and
have Discord and Telegram installed. Assume I'm technical but have never created
a Discord webhook or a Telegram bot before.

**1. Discord webhook URL** — for the env var `DISCORD_WEBHOOK_URL`.

I need the exact clicks to create a webhook that posts into a channel I own. Please cover:
- Creating a personal/private Discord server if I don't already have one
- Creating a text channel for the alerts
- Navigating to Channel Settings → Integrations → Webhooks → New Webhook
- Copying the webhook URL, and what it should look like (format/prefix)
- A `curl` command to test the webhook posts a message successfully
- How to make sure Discord mobile push notifications actually fire for this
  channel (notification settings / not muted), since these alerts are
  time-critical — a PS5 restock sells out in minutes

**2. Telegram bot token + chat ID** — for the env vars `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

Please cover:
- Creating a bot via @BotFather, including the exact commands to send and the
  name/username prompts
- Where the bot token appears and its format
- How to get **my own** chat ID so the bot DMs me (e.g. via @userinfobot, or by
  messaging my bot then calling the `getUpdates` API — please give both and say
  which is more reliable)
- The critical gotcha: I must send `/start` to my bot first, or it cannot message
  me — please confirm and explain where this bites
- A `curl` command against the `sendMessage` API to verify end to end
- How to confirm Telegram push notifications are enabled for this chat

For both, please tell me:
- Which values are secrets I must never commit to git
- How to revoke/rotate each one if it leaks
- Any rate limits I should know about (the script runs every 30 minutes but only
  sends on a state change, so volume is low)

Format the answer as two numbered walkthroughs, then a final block showing the
exact `.env` lines filled in with placeholder values, like:

```
DISCORD_WEBHOOK_URL=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```
