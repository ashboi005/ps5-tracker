# PS5 Availability Tracker

Checks PS5 stock for one pincode across Amazon.in, Flipkart, Sony Center, Croma,
Reliance Digital and Vijay Sales. Runs from cron every 30 minutes; alerts via
Discord, Telegram and email the moment stock appears.

No LLM runs in the loop — checks are deterministic, so a failure is loud (logged
error + breakage alert) rather than a silent "sold out".

## Setup

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/playwright install chromium      # only needed for the Amazon checker

cp .env.example .env                      # fill in your credentials
```

Add your product URLs to `config.json` (already set to pincode `143001`):

```json
{
  "pincode": "143001",
  "retailers": {
    "amazon": ["https://www.amazon.in/dp/..."],
    "croma": ["https://www.croma.com/..."]
  }
}
```

Each retailer takes a **list**, so you can track several SKUs (disc, digital,
bundles) per site. Use `amazon.in` URLs, not `amazon.com`. Leave a retailer's
list empty to skip it.

## Verify before trusting it

```bash
venv/bin/python main.py --test-notify     # confirm all 3 channels deliver
venv/bin/python main.py --check croma     # run one retailer
venv/bin/python main.py --dry-run         # full pass, sends nothing, writes nothing
venv/bin/python tests/test_logic.py       # state-transition + parsing tests
venv/bin/python tests/test_main.py        # config + formatting tests
```

Check each retailer against a URL you know is in stock **and** one you know is
out of stock. A checker that reports "no stock" for everything is
indistinguishable from a working one during a drought — this is the only way to
tell them apart.

## Run it

```bash
venv/bin/python main.py                   # once, to seed state.json
crontab -e
```

```cron
*/30 * * * * cd /opt/ps5-tracker && venv/bin/python main.py >> logs/run.log 2>&1
```

## How stock detection works

The five non-Amazon checkers fetch the product page and read stock signals from
its text (`checkers/http.py`), with out-of-stock phrases taking precedence over
a generic "add to cart". Amazon gets a real headless Chromium
(`checkers/amazon.py`) because its session flow resists plain HTTP replication;
only one browser exists at a time and it closes immediately after each check.

**This means stock detection is currently page-level, not pincode-level.** The
pincode is plumbed through to every checker but the page-text path does not yet
use it — see below.

### Making a checker pincode-accurate

Each retailer's pincode widget calls an internal JSON endpoint. To use it:

1. Open the product page in a browser, DevTools → Network, filter XHR.
2. Enter your pincode in the site's delivery-check field.
3. Find the request that fires and note its URL, method, headers and payload.
4. Replace that retailer's `check()` body in `checkers/<retailer>.py` with an
   `httpx` call to it, returning a `CheckResult`.
5. Verify with `--check <retailer>` against known in-stock and out-of-stock URLs.

Only that one file changes; the other checkers are unaffected.

## Alerts

- **Stock found** → Discord + Telegram + email. Fires only on a transition into
  stock, so you are not paged every 30 minutes while stock holds.
- **Checker broken** → Telegram only, once, after 3 consecutive failures
  (~90 min blind). Deliberately separate from stock alerts so a broken scraper
  is never mistaken for a confirmed sell-out. Resets when the checker recovers.

A dead notification channel is logged and skipped; it never blocks the others.

## Layout

```
config.json          pincode + product URLs (yours to edit)
.env                 secrets (gitignored)
state.json           last-known status per URL (gitignored)
main.py              orchestrator + CLI
state.py             transition detection, fail counting
checkers/            one module per retailer, isolated
notifiers/           discord, telegram, email
tests/               logic tests, no network needed
```
