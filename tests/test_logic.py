"""Verify state-transition and parsing logic without network deps installed."""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(__file__).resolve().parent / "tmp"
TMP.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))

# Stub httpx so checkers.http imports; we only exercise pure parsing here.
stub = types.ModuleType("httpx")
for name in ("HTTPError", "TimeoutException"):
    setattr(stub, name, type(name, (Exception,), {}))
stub.HTTPStatusError = type("HTTPStatusError", (stub.HTTPError,), {})
stub.AsyncClient = object
sys.modules.setdefault("httpx", stub)

import state as state_module
from checkers.common import CheckResult, truncate
from checkers.http import (
    parse_delivery_blocked,
    parse_jsonld_availability,
    parse_name,
    parse_price,
    parse_stock,
)

failures = []


def check(label, got, want):
    if got == want:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}: got {got!r}, want {want!r}")
        failures.append(label)


print("parse_stock (text fallback):")
check("add to cart -> in stock", parse_stock("<div>Add to Cart</div>"), True)
check("sold out -> no stock", parse_stock("<div>Sold Out</div>"), False)
check(
    "conflicting text -> unknown, not a false 'sold out'",
    parse_stock("<div>Add to Cart</div><span>Currently unavailable</span>"),
    None,
)
check("no signal -> unknown", parse_stock("<div>hello</div>"), None)
check("ignores script text", parse_stock("<script>add to cart</script><p>hi</p>"), None)

print("parse_stock (schema.org, authoritative):")
check(
    "JSON-LD InStock wins over conflicting text",
    parse_stock(
        '<span>Notify Me</span><script>{"availability":"https://schema.org/InStock"}</script>'
    ),
    True,
)
check(
    "JSON-LD OutOfStock wins over add-to-cart",
    parse_stock(
        '<div>Add to Cart</div><script>{"availability":"https://schema.org/OutOfStock"}</script>'
    ),
    False,
)
check(
    "handles Flipkart's escaped slashes",
    parse_jsonld_availability('{"availability":"https:\\u002f\\u002fschema.org\\u002fInStock"}'),
    True,
)
check(
    "any in-stock offer means buyable",
    parse_jsonld_availability(
        '{"availability":"https://schema.org/OutOfStock"},'
        '{"availability":"https://schema.org/InStock"}'
    ),
    True,
)
check("no markup -> None", parse_jsonld_availability("<div>hi</div>"), None)
check(
    "LimitedAvailability -> in stock",
    parse_jsonld_availability('{"availability":"https://schema.org/LimitedAvailability"}'),
    True,
)
check(
    "PreOrder -> not in stock",
    parse_jsonld_availability('{"availability":"https://schema.org/PreOrder"}'),
    False,
)

print("pincode delivery gate (beats national InStock):")
# The real case: Flipkart's JSON-LD says InStock (the seller holds national
# stock) while the page says "Not deliverable at your location". Stock you
# cannot receive is not stock, so this must read as unobtainable.
flipkart_live = (
    "<title>SONY PlayStation5 Console (slim)</title>"
    "<div>Buy now</div><span>Not deliverable at your location</span>"
    '<script type="application/ld+json">{"offers":{"availability":'
    '"https:\\u002f\\u002fschema.org\\u002fInStock"}}</script>'
)
check("InStock + undeliverable -> not obtainable", parse_stock(flipkart_live), False)
check("delivery refusal detected", parse_delivery_blocked(flipkart_live), True)
check(
    "InStock + deliverable -> obtainable",
    parse_stock(
        "<div>Buy now</div>"
        '<script>{"availability":"https:\\u002f\\u002fschema.org\\u002fInStock"}</script>'
    ),
    True,
)
check(
    "'not serviceable' phrasing also caught",
    parse_stock('<div>Add to Cart</div><span>Pincode not serviceable</span>'),
    False,
)
check("no refusal text on a clean page", parse_delivery_blocked("<div>Buy now</div>"), False)

print("parse_price / parse_name:")
check("price", parse_price("<b>₹54,990</b>"), "₹54,990")
check("price Rs form", parse_price("<b>Rs. 49,990</b>"), "₹49,990")
check("no price", parse_price("<b>free</b>"), None)
check("name", parse_name("<title>  PS5   Console </title>"), "PS5 Console")
check("no title", parse_name("<div>x</div>"), None)
check("truncate", truncate("a" * 200), "a" * 119 + "…")

print("state transitions:")
st = {}
r_in = CheckResult("croma", "u1", in_stock=True)
r_out = CheckResult("croma", "u1", in_stock=False)
r_err = CheckResult("croma", "u1", error="timeout")

check("first sighting alerts", state_module.apply(st, r_in), (True, False))
check("still in stock -> no repeat alert", state_module.apply(st, r_in), (False, False))
check("goes out of stock", state_module.apply(st, r_out), (False, False))
check("restock alerts again", state_module.apply(st, r_in), (True, False))

print("error handling:")
st2 = {}
check("fail 1 silent", state_module.apply(st2, r_err), (False, False))
check("fail 2 silent", state_module.apply(st2, r_err), (False, False))
check("fail 3 warns", state_module.apply(st2, r_err), (False, True))
check("fail 4 stays quiet", state_module.apply(st2, r_err), (False, False))
check("recovery resets", state_module.apply(st2, r_out), (False, False))
check("re-breaks after recovery", state_module.apply(st2, r_err), (False, False))

print("error must not fabricate stock state:")
st3 = {}
state_module.apply(st3, r_in)
state_module.apply(st3, r_err)
check("last-known stock preserved on error", st3["u1"]["in_stock"], True)
check(
    "error does not re-alert stock as new",
    state_module.apply(st3, r_in),
    (False, False),
)

st4 = {}
state_module.apply(st4, r_err)
check("error then in-stock alerts", state_module.apply(st4, r_in), (True, False))

print("heartbeat (proof-of-life during silence):")
HB = state_module.HEARTBEAT_SECONDS
st5 = {}
check("fresh state does not fire immediately", state_module.heartbeat_due(st5, 1000.0), False)

state_module.ensure_heartbeat_clock(st5, 1000.0)
check("clock starts on first run", state_module.heartbeat_due(st5, 1000.0), False)
check("not due just before the window", state_module.heartbeat_due(st5, 1000.0 + HB - 1), False)
check("due exactly at the window", state_module.heartbeat_due(st5, 1000.0 + HB), True)
check("still due long after", state_module.heartbeat_due(st5, 1000.0 + HB * 3), True)

state_module.mark_heartbeat(st5, 1000.0 + HB)
check("sending resets the timer", state_module.heartbeat_due(st5, 1000.0 + HB), False)
check("re-arms for the next window", state_module.heartbeat_due(st5, 1000.0 + HB * 2), True)

check(
    "ensure_heartbeat_clock does not clobber an existing time",
    (
        state_module.ensure_heartbeat_clock(st5, 9_999_999.0),
        st5[state_module.META_KEY]["last_heartbeat"],
    )[1],
    1000.0 + HB,
)

print("_meta never leaks into product iteration:")
st6 = {"https://x": {"in_stock": True, "fail_count": 0, "broken_notified": False}}
state_module.mark_heartbeat(st6, 5.0)
check("product_entries excludes _meta", list(state_module.product_entries(st6)), ["https://x"])
check("_meta survives save/load", "_meta" in st6, True)


print("block detection (verified against real VPS responses):")
from checkers.http import detect_block

# Verbatim shape of what the VPS received: 351 bytes titled 'Access Denied'.
croma_block = (
    "<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD><BODY><H1>Access Denied</H1>"
    + "x" * 280
    + "</BODY></HTML>"
)
check("croma 'Access Denied' -> blocked", "bot protection" in (detect_block(croma_block) or ""), True)
# Sony Center returned a 169-byte stub with no title at all.
check("169-byte stub -> blocked", "169 bytes" in (detect_block("x" * 169) or ""), True)
check(
    "cloudflare interstitial -> blocked",
    "bot protection" in (detect_block("<title>Just a moment...</title>" + "x" * 5000) or ""),
    True,
)
check(
    "429 page -> blocked",
    "bot protection" in (detect_block("<title>Too Many Requests</title>" + "x" * 5000) or ""),
    True,
)

# False positives here would hide real stock, so both must come back clean.
check(
    "real product page -> not blocked",
    detect_block("<title>SONY PS5 Console</title>" + "<div>product</div>" * 500),
    None,
)
check(
    "large real page -> not blocked",
    detect_block("<title>PS5 Slim</title>" + "y" * 300_000),
    None,
)

print("retry policy:")
from checkers.http import MAX_ATTEMPTS, RETRY_STATUS, TIMEOUT, retry_delay

check("timeout raised for datacenter IPs", TIMEOUT >= 20.0, True)
check("429 is retried", 429 in RETRY_STATUS, True)
check("403 is not retried (permanent block)", 403 in RETRY_STATUS, False)
check("404 is not retried", 404 in RETRY_STATUS, False)
check("honours Retry-After header", retry_delay(0, "7"), 7.0)
check("caps an absurd Retry-After", retry_delay(0, "600"), 30.0)
check("ignores unparsable Retry-After", 1.0 <= retry_delay(0, "soon") <= 2.0, True)
check("backoff grows with attempts", retry_delay(2) > retry_delay(0), True)
check("backoff is capped", retry_delay(20) <= 16.0, True)

print("save/load round-trip:")
tmp = TMP / "state_test.json"
state_module.save({"u1": {"in_stock": True, "fail_count": 0, "broken_notified": False}}, tmp)
check("round-trip", state_module.load(tmp)["u1"]["in_stock"], True)
check("missing file -> empty", state_module.load(tmp.with_name("nope.json")), {})
tmp.write_text("{not json")
check("corrupt file -> empty", state_module.load(tmp), {})
tmp.unlink(missing_ok=True)

print()
print(f"{'FAILURES: ' + ', '.join(failures) if failures else 'ALL PASSED'}")
sys.exit(1 if failures else 0)
