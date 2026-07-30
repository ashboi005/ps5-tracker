# PS5 Availability Tracker — Design & Implementation Plan

## 1. Goal

A lightweight, self-hosted script that checks PS5 availability for a specific pincode across Amazon.in, Flipkart, Sony Center, Croma, Reliance Digital, and Vijay Sales, run via cron every 30 minutes on the user's own VPS (2-4GB RAM). On finding new stock, it sends an instant alert via Discord, Telegram, and email.

Explicitly out of scope: quick-commerce (Blinkit etc.) — skipped due to app-first/geo-based serviceability and heavier bot protection making it the most brittle checker to build and maintain.

## 2. Approach

**No LLM/agent in the runtime loop.** Site navigation is deterministic (site-specific code), not agent-driven. Reasons:
- Cost: 6 sites × 48 cycles/day adds up even on cheap models, for zero benefit here.
- Reliability: an agent that misreads a page produces a silent false negative — a missed restock — which defeats the entire point of the tracker. Deterministic parsing fails loudly (exception/log) instead of failing silently.
- LLM use is still valuable *offline*, as a dev aid when a site's markup changes and a checker needs updating — just not wired into the cron job.

**Hybrid scraping, per site:**
- **Direct API calls** (Flipkart, Sony Center, Croma, Reliance Digital, Vijay Sales): each site's pincode-serviceability widget calls an internal JSON endpoint under the hood. Find it once via browser devtools → Network tab, replicate with `httpx`. Sub-second, negligible resource cost, no browser needed.
- **Headless browser fallback** (Amazon.in only): Amazon's Akamai-protected session/cookie flow resists plain HTTP reverse-engineering cleanly enough that a headless Playwright/Chromium check is the pragmatic choice. Only one Chromium instance ever runs at a time, for a few seconds, once per 30-minute cycle — a non-issue on 2-4GB RAM.

Each retailer is an isolated "checker" module behind one common interface, so fixing/adding a site never touches the others.

## 3. Config format

User-editable, no code changes needed to add/remove product URLs:

```json
{
  "pincode": "560001",
  "retailers": {
    "amazon": ["https://www.amazon.in/dp/XXXX"],
    "flipkart": ["https://www.flipkart.com/..."],
    "sonycenter": ["..."],
    "croma": ["..."],
    "reliancedigital": ["..."],
    "vijaysales": ["..."]
  }
}
```
Each key holds a list — supports multiple SKUs (disc/digital edition, bundles) per site.

## 4. Project layout

```
ps5-tracker/
  config.json            # pincode + product URLs (user edits)
  .env                    # secrets: discord webhook, telegram token+chat id, smtp creds
  .env.example
  .gitignore               # excludes .env, state.json, logs/, venv/
  state.json               # last-known status per URL (transition detection)
  requirements.txt
  main.py                  # orchestrator, invoked by cron
  checkers/
    __init__.py
    common.py              # CheckResult dataclass, shared types
    amazon.py               # Playwright-based
    flipkart.py             # httpx + reverse-engineered JSON API
    sonycenter.py             "
    croma.py                   "
    reliancedigital.py          "
    vijaysales.py                "
  notifiers/
    __init__.py
    discord.py              # webhook POST
    telegram.py              # bot sendMessage
    email.py                  # smtplib
  logs/
  README.md                 # setup: venv, cron entry, how to get webhook/bot/smtp creds
```

## 5. Data flow (one cron run)

1. Load `config.json` + `.env`.
2. Run the 5 httpx-based checkers concurrently via `asyncio.gather`; run the Amazon Playwright checker separately (sequential, so only one browser exists at a time).
3. Each checker call is wrapped in try/except with a timeout (10s httpx, 20s Playwright). One site failing never blocks the others. A checker returns `CheckResult(retailer, url, in_stock, price, name, error)` — `error` is set on a failed check, distinct from a confirmed "not in stock."
4. For each result, diff against the corresponding entry in `state.json`:
   - Transition from not-in-stock (or unknown) → in-stock: send a notification to **all three** channels (Discord + Telegram + email) with retailer, product name, price, URL, pincode.
   - `error` set: increment that URL's `fail_count`. On `fail_count == 3` (i.e., ~90 minutes with no successful check), send one "checker broken" alert via **Telegram only** — kept separate from stock alerts so a broken scraper is never confused with "confirmed sold out." Stays silent on repeat failures until it recovers, then resets the counter.
5. Write updated `state.json`, append a summary log line, exit.

## 6. Notifications

- **Discord**: webhook URL from `.env`, single `POST` with a JSON `content` field.
- **Telegram**: bot token + chat ID from `.env`, `POST` to `https://api.telegram.org/bot<token>/sendMessage`.
- **Email**: SMTP via `smtplib` (Gmail app password or any SMTP relay credentials in `.env`).
- Stock-found alerts go to all three. Checker-broken alerts go to Telegram only (kept to one channel to avoid maintenance noise across every inbox).

## 7. Error handling

- Per-checker try/except + timeout as above — no single site can crash the whole run.
- `error` (check failed) is tracked separately from `in_stock: false` (checked successfully, confirmed no stock) at every layer, so a broken scraper never masquerades as "sold out."
- Uncaught exceptions in `main.py` itself are caught at the top level, logged, and the process exits non-zero (visible in cron's mail/log output) rather than corrupting `state.json`.

## 8. Testing / maintenance hooks

- `python main.py --dry-run` — runs every checker, prints what it *would* notify, does not touch `state.json` or send anything. Safe for iterating on a checker without spamming yourself.
- `python main.py --check <retailer>` — runs a single checker in isolation for faster debugging.
- Before relying on any checker, manually verify it against one known in-stock URL and one known unserviceable/out-of-stock URL.

## 9. Deployment

- Python 3.11+, project-local venv on the VPS.
- `crontab -e`:
  ```
  */30 * * * * cd /opt/ps5-tracker && venv/bin/python main.py >> logs/run.log 2>&1
  ```
- Secrets live only in `.env` (gitignored), never committed.

## 10. Implementation plan (step by step)

1. **Scaffold project**: directory layout above, `requirements.txt` (`httpx`, `playwright`, `python-dotenv`), venv, `.gitignore`, git init + first commit.
2. **Shared types**: `checkers/common.py` — `CheckResult` dataclass; `notifiers/` interface (`send(message: str) -> None`).
3. **Notifiers first** (small, independent, easy to verify standalone): implement Discord, Telegram, email senders; manually trigger each once to confirm creds work before writing any checker.
4. **State + diff logic**: `state.json` read/write helpers, transition detection, fail-count tracking — unit-testable in isolation with fake `CheckResult`s, no network needed.
5. **Reverse-engineer + implement httpx checkers**, one at a time, easiest first: Flipkart → Croma → Reliance Digital → Vijay Sales → Sony Center. For each: inspect Network tab for the pincode-check request, replicate with `httpx`, verify against one in-stock and one out-of-stock URL via `--check <retailer>`.
6. **Amazon Playwright checker**: implement, verify with `--check amazon` against a real product URL, confirm timeout/error path by testing against a deliberately bad URL.
7. **Orchestrator** (`main.py`): wire checkers + notifiers + state diffing + `--dry-run`/`--check` flags, concurrent execution for httpx checkers.
8. **End-to-end dry run**: populate real `config.json` with your pincode + actual PS5 product URLs, run `--dry-run` a few times, confirm output looks right.
9. **Live run + cron**: remove `--dry-run`, run once manually to seed `state.json`, install the crontab entry, confirm a log line appears after the next scheduled run.
10. **README**: how to get a Discord webhook URL, a Telegram bot token + chat ID, and SMTP creds — so the setup is reproducible if you ever redeploy.

## 11. Open items for you to fill in before/at implementation time

- Actual PS5 product URLs per retailer (for `config.json`) — you mentioned wanting to hand these over; drop them in once the scaffold exists.
- Discord webhook URL, Telegram bot token + chat ID, SMTP credentials (all go in `.env`, never committed).
- Your pincode.
