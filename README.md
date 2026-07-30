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

Containerised (recommended — see Coolify section below):

```bash
docker compose up --build
```

Or bare-metal with cron:

```bash
venv/bin/python main.py                   # once, to seed state.json
crontab -e
```

```cron
*/30 * * * * cd /opt/ps5-tracker && venv/bin/python main.py >> logs/run.log 2>&1
```

## How stock detection works

Stock is read with this precedence (`checkers/http.py`):

1. **An explicit pincode delivery refusal** ("not deliverable at your location",
   "not serviceable") — strongest signal. A seller can hold national stock while
   refusing your pincode, and stock you cannot receive is not stock.
2. **schema.org `availability` in JSON-LD** — authoritative national stock.
   Verified present on Flipkart and Sony Center.
3. **Page-text heuristics** — last resort. If a page contains *both* in- and
   out-of-stock phrases (common, thanks to JS templates), this returns
   "unknown" rather than guessing.

"Unknown" is reported as a **failed check**, never as "no stock". A silent false
negative is the one failure that defeats the tracker, so ambiguity is always
surfaced instead of resolved by guessing.

### Which checker uses what — all verified against live sites

| Retailer | Method | Why |
|---|---|---|
| Croma | headless Chromium | 403 to httpx even with full browser headers; `api.croma.com` too (Akamai). |
| Flipkart | headless Chromium | Works from a home IP, but from a VPS the connection is dropped — no response at all across 3 attempts. |
| Sony Center | headless Chromium | Shopify's `<product>.js` is authoritative but returns 429 to datacenter IPs on every attempt. The rendered page carries the same answer in JSON-LD. |
| Amazon | headless Chromium | Session/cookie flow resists HTTP replication. Rejects non-`amazon.in` hosts, since `amazon.com` reflects US stock. |
| Vijay Sales | httpx + JSON-LD | Works directly. Needs **product** URLs — a category page reads AMBIGUOUS forever. |
| Reliance Digital | httpx + JSON-LD | Not yet verified against a live page. |

All four browser checkers share **one** Chromium per pass (`checkers/browser.session`),
not one launch per URL — with 9 URLs that is the difference between seconds and
minutes. Worst case is ~450s per pass, inside the 600s interval, and the loop
sleeps *after* a pass so runs cannot overlap.

Delivery-signal trust is per-site, because reliability is per-site: Sony Center
ships a hidden "this pin code is not serviceable!" node on every product page, so
honouring it there would report permanent false sell-outs. Flipkart's equivalent
message is real. Sites opt **out**, so an unaudited site errs toward "cannot buy"
rather than over-promising.

### The pincode limitation — read this

Most of these sites render delivery serviceability **client-side**, after
resolving your location. An anonymous page fetch therefore sees *national*
stock, not your-pincode stock.

The tracker is explicit about this rather than pretending otherwise:

- `pincode_verified=True` → serviceability for your pincode was genuinely
  confirmed. Alert reads **"IN STOCK — deliverable to <pincode>"**.
- `pincode_verified=False` → national stock only. Alert reads **"IN STOCK
  (national) — pincode NOT verified"** and tells you to confirm on the site.

Right now **every checker reports `False`** — treat those alerts as "worth
looking at now", not "confirmed buyable". A real observed case: a Flipkart PS5
listing whose JSON-LD says `InStock` while the page shows "Not deliverable at
your location".

### Making a checker pincode-accurate

To upgrade a checker to real pincode verification:

1. Open the product page in a browser, DevTools → Network, filter XHR.
2. Enter your pincode in the site's delivery-check field.
3. Find the request that fires; note its URL, method, headers and payload.
4. Replace that retailer's `check()` body in `checkers/<retailer>.py` with an
   `httpx` call to it, and set `pincode_verified=True` on the returned
   `CheckResult` — only once it genuinely reflects that pincode.
5. Verify with `--check <retailer>` against known in-stock and out-of-stock URLs.

Only that one file changes; the other checkers are unaffected.

## Deploying on Coolify

The image is based on `mcr.microsoft.com/playwright/python`, which ships Chromium
and its system libraries — installing those onto a plain Python base is the usual
source of pain.

`entrypoint.sh` runs one pass every `CHECK_INTERVAL_SECONDS` (default 3600) as a
long-running service, rather than cron inside a container, which needs extra
plumbing to forward env vars and logs to stdout.

In Coolify:

1. New resource → Docker Compose (or Dockerfile) from this repo.
2. Set the env vars from `.env.example` in Coolify's UI — **not** in a committed
   file.
3. Keep the `ps5-data:/data` volume. It holds `state.json`; without it every
   redeploy forgets what was in stock and re-alerts on everything.
4. `config.json` is mounted read-only, so you can change product URLs without
   rebuilding.

Locally: `docker compose up --build`.

## Alerts — when you actually get pinged

**Nothing is sent while a PS5 stays out of stock.** Alerts fire on *transitions*,
so a permanent sell-out produces zero messages regardless of how often it runs.
That is why the interval can be 10 minutes without creating noise: a tighter
interval costs HTTP requests, not notifications.

| Event | Channels | Frequency |
|---|---|---|
| Stock appears | Discord + Telegram + email | Instantly, once per restock (not repeated while stock holds) |
| Checker broken | Telegram | Once, after 3 consecutive failures (~30 min at a 10-min interval) |
| Proof-of-life | Telegram | Only after `HEARTBEAT_SECONDS` (12h) of total silence |

The heartbeat exists because a silent tracker and a dead tracker look identical.
It lists every URL's current status, and any alert resets its timer — so you get
it *only* during genuine quiet, at most twice a day. It is Telegram-only on
purpose: routine traffic in the email inbox would train you to ignore the inbox
that carries real stock alerts.

A dead notification channel is logged and skipped; it never blocks the others.

### Telegram gotcha

A bot cannot message you until **you** message it first. If you see
`400 Bad Request: chat not found`, open your bot in Telegram and send `/start`.
Bot tokens are redacted from all log output (`notifiers/telegram.py`), since
httpx otherwise embeds the token in exception text.

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

## Troubleshooting a checker

An UNKNOWN result now **logs its own diagnostic automatically** — no shell
access needed. Look for the `diagnostic |` line right under the failing check:

```
croma [141001] | ERROR (could not determine stock ...) | https://...
  croma diagnostic | len=482913 | jsonld=none | title='...' | out_of_stock=["'sold out' -> ...context..."] | in_stock=[...]
```

Each hit carries surrounding context, which is how you tell a real "Sold Out"
from a JS template string like `default title - sold out`.

For deeper inspection with the full HTML, if you do have shell access:

```bash
docker compose exec ps5-tracker python diagnose.py croma --save /data/croma.html
```

If it reports `AMBIGUOUS: both kinds present`, the usual cause is a
related-products carousel: other products' buttons on the same page. The fix is
a scoped selector for that retailer, not another global signal.

### Known site behaviour from a VPS

| Symptom | Cause | Handling |
|---|---|---|
| `HTTP 429` (Sony Center) | Shopify rate-limits datacenter IPs | 3 attempts with backoff, honouring `Retry-After`. No page fallback on 429 — a second request would deepen the block. |
| `timeout` (Flipkart) | Slow/tarpitted for datacenter IPs | Timeout raised to 25s (`HTTP_TIMEOUT_SECONDS`), 3 attempts. |
| `403` | Permanent bot block | Not retried; that retailer needs the browser path. |
| `resolved to www.amazon.com` | `a.co` short links point at the US store | Use `amazon.in` or `amzn.in` links. |

## IP blocking — the real limitation

Three of six retailers **block datacenter IPs outright**. Measured the same
minute, same URLs, from a home connection versus a Coolify VPS:

| Site | Home IP | VPS |
|---|---|---|
| Flipkart | 200, 1.7 MB | connection dropped (httpx *and* Chromium both time out) |
| Sony Center | 200 | 169-byte stub, no title |
| Croma | 403 | `Access Denied` (351 bytes) — blocked in the browser too |

This is IP/ASN reputation, not scraping technique. Header tuning, retries and
headless Chromium were all tried and all fail: Croma's browser render returns
the same `Access Denied` page. Those checks are reported as
`blocked by site bot protection`, distinct from "no stock", so a block can never
be mistaken for a sell-out.

**Options, best first:**

1. **Run it on a home machine** (old laptop, Raspberry Pi, home server). A
   residential IP is what these sites accept, and everything already works
   there. Free, and best for a personal tracker.
2. **Set `PROXY_URL`** to a residential/mobile proxy, ideally India-based. Both
   httpx and Chromium honour it. Costs money per GB, and product pages are large.
3. **Accept partial coverage** on the VPS. Amazon and Vijay Sales work fine from
   a datacenter IP; empty out the blocked retailers in `config.json` to stop
   wasting ~450s per pass on requests that cannot succeed.

Option 3 is worth doing regardless while you decide, since a blocked retailer
still costs a full browser render and timeout on every pass.

## Running it at home (recommended)

A residential IP is what the blocked retailers accept. Measured from a home
connection with the hybrid checkers, a full pass takes **~3 seconds** instead of
the ~450s the VPS spent on renders that could never succeed.

### With Docker (simplest — Chromium included)

```bash
cp .env .env.local          # or keep using .env
docker compose up -d --build
docker compose logs -f
```

That's the same compose file as the VPS. Chromium comes in the image, so all six
retailers work.

### Without Docker (native)

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/playwright install chromium     # needed for Amazon and Croma only
venv/bin/python main.py --dry-run        # verify before going live
```

Then schedule it. On macOS, cron works but `launchd` survives reboots properly:

```bash
crontab -e
```
```cron
*/10 * * * * cd /path/to/ps5-tracker && venv/bin/python main.py >> logs/run.log 2>&1
```

**Skipping Playwright is a valid choice.** Without it, Flipkart, Sony Center and
Vijay Sales all still work over HTTP from a residential IP — you lose only Amazon
and Croma, and those report a clear "playwright not installed" error rather than
failing silently.

### The one catch: sleep

A sleeping laptop runs no checks. Either:

- keep it awake while tracking: `caffeinate -s docker compose up`
- or run it on something always-on (Raspberry Pi, mini PC, home server)

The 12h heartbeat is what tells you it stopped — if Telegram goes quiet past a
day, the machine slept.

## Why a domain or Cloudflare proxy will not help

Attaching a domain and putting Cloudflare in front of the VPS solves the
opposite problem. Cloudflare proxies **inbound** traffic *to* your server; this
tracker makes **outbound** requests *from* it. Flipkart and Croma never see your
domain — they see your server's egress IP, which is unchanged by any DNS or
inbound-proxy setup.

Cloudflare WARP or Workers do alter egress, but both exit from Cloudflare's own
datacenter ranges, which these retailers block for the same reason they block
your VPS. To change the outcome you need a *residential* egress: run at home, or
set `PROXY_URL` to a residential proxy.

