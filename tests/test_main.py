"""Verify main.py config validation, target splitting, and message formatting."""

import json
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

stub = types.ModuleType("httpx")
for name in ("HTTPError", "TimeoutException"):
    setattr(stub, name, type(name, (Exception,), {}))
stub.HTTPStatusError = type("HTTPStatusError", (stub.HTTPError,), {})
stub.AsyncClient = object
stub.post = lambda *a, **k: None
sys.modules.setdefault("httpx", stub)

dotenv_stub = types.ModuleType("dotenv")
dotenv_stub.load_dotenv = lambda *a, **k: None
sys.modules.setdefault("dotenv", dotenv_stub)

import main
from checkers.common import CheckResult

failures = []


def check(label, got, want):
    if got == want:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}: got {got!r}, want {want!r}")
        failures.append(label)


def expect_exit(label, fn):
    try:
        fn()
    except SystemExit as exc:
        print(f"  PASS  {label} -> exits: {exc}")
        return
    print(f"  FAIL  {label}: did not exit")
    failures.append(label)


scratch = Path(__file__).resolve().parent / "tmp"
scratch.mkdir(exist_ok=True)


def with_config(data):
    path = scratch / "cfg.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    main.CONFIG_PATH = path
    return path


print("config validation:")
with_config({"pincode": "143001", "retailers": {"croma": ["https://x"]}})
check("valid config", main.load_config()["pincodes"], ["143001"])

with_config("{bad json")
expect_exit("invalid JSON", main.load_config)

with_config({"retailers": {}})
expect_exit("missing pincode", main.load_config)

with_config({"pincode": "143001", "retailers": {"blinkit": []}})
expect_exit("unknown retailer", main.load_config)

with_config({"pincodes": [143001], "retailers": {}})
check("numeric pincode coerced", main.load_config()["pincodes"], ["143001"])

print("per-retailer pincode overrides:")
with_config(
    {
        "pincodes": ["143001"],
        "pincode_overrides": {"croma": "141001"},
        "retailers": {"croma": ["https://c"], "flipkart": ["https://f"]},
    }
)
cfg_ov = main.load_config()
check("override applies to croma", main.pincode_for(cfg_ov, "croma"), "141001")
check("default applies elsewhere", main.pincode_for(cfg_ov, "flipkart"), "143001")

print("multiple pincodes:")
with_config({
    "pincodes": ["143001", "110001", "122001"],
    "retailers": {"flipkart": ["https://f"], "amazon": ["https://a"]},
})
cfg_mp = main.load_config()
check("all pincodes parsed", cfg_mp["pincodes"], ["143001", "110001", "122001"])
check(
    "pincode-aware retailer checks every pincode",
    main.pincodes_for(cfg_mp, "flipkart"),
    ["143001", "110001", "122001"],
)
check(
    "non-aware retailer checks only the first (rest would repeat)",
    main.pincodes_for(cfg_mp, "amazon"),
    ["143001"],
)
h, b = main.targets(cfg_mp, None)
check("flipkart fans out to 3 targets", len([1 for r, _, _ in b if r == "flipkart"]), 3)
check("amazon stays at 1 target", len([1 for r, _, _ in b if r == "amazon"]), 1)
check(
    "each flipkart target has a distinct pincode",
    sorted(p for r, _, p in b if r == "flipkart"),
    ["110001", "122001", "143001"],
)
check("duplicates removed", main.normalise_pincodes(["1", "1", " 1 ", "2"]), ["1", "2"])
check("single string accepted", main.normalise_pincodes("143001"), ["143001"])
check("int accepted", main.normalise_pincodes(143001), ["143001"])

print("per-pincode state keys:")
import state as st_mod
a = CheckResult("flipkart", "https://x", pincode="143001", in_stock=True, pincode_verified=True)
bb = CheckResult("flipkart", "https://x", pincode="110001", in_stock=True, pincode_verified=True)
stt = {}
check("same URL, pincode A alerts", st_mod.apply(stt, a), (True, False))
check("same URL, pincode B alerts independently", st_mod.apply(stt, bb), (True, False))
check("two separate state keys", len(st_mod.product_entries(stt)), 2)
check("A does not re-alert", st_mod.apply(stt, a), (False, False))

with_config({"pincodes": ["143001"], "retailers": {}})
check("no overrides key is fine", main.pincode_for(main.load_config(), "croma"), "143001")

with_config({"pincodes": ["143001"], "pincode_overrides": {"croma": "  "}, "retailers": {}})
check(
    "blank override falls back",
    main.pincode_for(main.load_config(), "croma"),
    "143001",
)

with_config({"pincodes": ["143001"], "pincode_overrides": {"blinkit": "1"}, "retailers": {}})
expect_exit("unknown retailer in overrides", main.load_config)

print("disabled retailers:")
with_config({
    "pincodes": ["143001"],
    "disabled": ["croma"],
    "retailers": {"croma": ["https://c1", "https://c2"], "vijaysales": ["https://v"]},
})
cfg_dis = main.load_config()
check("disabled parsed", cfg_dis["disabled"], {"croma"})
h, b = main.targets(cfg_dis, None)
check("disabled retailer not checked", [r for r, _, _ in h + b], ["vijaysales"])
check("its URLs are kept in config", len(cfg_dis["retailers"]["croma"]), 2)
h, b = main.targets(cfg_dis, "croma")
check("--check cannot re-enable a disabled retailer", h + b, [])

with_config({"pincodes": ["1"], "disabled": "croma", "retailers": {}})
check("bare string accepted", main.load_config()["disabled"], {"croma"})

with_config({"pincodes": ["1"], "disabled": ["blinkit"], "retailers": {}})
expect_exit("unknown retailer in disabled", main.load_config)

with_config({"pincodes": ["1"], "retailers": {}})
check("no disabled key is fine", main.load_config()["disabled"], set())

print("target splitting:")
# Derived from the registry rather than hardcoded, so moving a retailer between
# the httpx and browser paths does not invalidate these assertions.
from checkers import BROWSER_CHECKERS, HTTP_CHECKERS

A_BROWSER = sorted(BROWSER_CHECKERS)[0]
AN_HTTP = sorted(HTTP_CHECKERS)[0]

cfg = {
    "pincodes": ["143001"],
    "pincode_overrides": {},
    "retailers": {
        AN_HTTP: ["https://h/a", "  ", "https://h/b"],
        A_BROWSER: ["https://b/a"],
    },
}
http_t, browser_t = main.targets(cfg, None)
check("http targets skip blanks", len(http_t), 2)
check("http targets are the httpx sites", [r for r, _, _ in http_t], [AN_HTTP, AN_HTTP])
check("browser site routed to browser", browser_t, [(A_BROWSER, "https://b/a", "143001")])
check("no overlap between the two buckets", set(r for r, _, _ in http_t) & set(r for r, _, _ in browser_t), set())

http_t, browser_t = main.targets(cfg, AN_HTTP)
check(f"--check {AN_HTTP} filters http", len(http_t), 2)
check(f"--check {AN_HTTP} excludes browser sites", browser_t, [])

http_t, browser_t = main.targets(cfg, A_BROWSER)
check(f"--check {A_BROWSER} routes to browser only", (len(http_t), len(browser_t)), (0, 1))

check(
    "non-string url ignored",
    main.targets(
        {"pincodes": ["1"], "pincode_overrides": {}, "retailers": {AN_HTTP: [None, 42, "https://ok"]}},
        None,
    )[0],
    [(AN_HTTP, "https://ok", "1")],
)
check(
    "every configured retailer is routed somewhere",
    set(HTTP_CHECKERS) | set(BROWSER_CHECKERS),
    set(main.ALL_CHECKERS),
)

print("message formatting:")
r = CheckResult("croma", "https://x", in_stock=True, price="₹54,990", name="PS5")
msg = main.stock_message(r, "143001")
check("stock msg has retailer", "croma" in msg, True)
check("stock msg has price", "₹54,990" in msg, True)
check("stock msg has pincode", "143001" in msg, True)
check("stock msg has url", "https://x" in msg, True)

bare = main.stock_message(CheckResult("croma", "https://x", in_stock=True), "143001")
check("missing fields -> 'unknown'", bare.count("unknown"), 2)

broken = main.broken_message(CheckResult("croma", "https://x", error="timeout"))
check("broken msg warns not sold out", "not sold out" in broken, True)
check("broken msg has error", "timeout" in broken, True)

print("dry-run writes no state:")
main.LOG_PATH = scratch / "run.log"
main.setup_logging()
state_path = scratch / "drystate.json"
state_path.unlink(missing_ok=True)
import state as state_module

state_module.STATE_PATH = state_path
cfg_report = {"pincodes": ["143001"], "pincode_overrides": {}, "retailers": {}}
code = main.report(
    [CheckResult("croma", "https://x", in_stock=True, name="PS5")], cfg_report, True
)
check("dry-run exit 0", code, 0)
check("dry-run left no state file", state_path.exists(), False)

print("heartbeat wiring in report():")
sent = []
notifiers_mod = sys.modules["notifiers"]
original_broadcast = notifiers_mod.broadcast


def fake_broadcast(message, channels=notifiers_mod.STOCK_CHANNELS):
    sent.append((message, channels))


notifiers_mod.broadcast = fake_broadcast
HB = state_module.HEARTBEAT_SECONDS
cfg_hb = {"pincodes": ["143001"], "pincode_overrides": {}, "retailers": {}}
no_stock = CheckResult("flipkart", "https://x", in_stock=False, name="PS5")
in_stock = CheckResult(
    "flipkart", "https://y", in_stock=True, name="PS5", pincode_verified=True
)


def run(results, seed_state):
    """Run report() against a seeded state file and return messages sent."""
    sent.clear()
    state_module.save(seed_state, state_path)
    main.report(results, cfg_hb, False)
    return sent, state_module.load(state_path)


# First ever run: starts the clock, says nothing.
msgs, st = run([no_stock], {})
check("first run sends nothing", msgs, [])
check("first run starts the clock", "_meta" in st, True)

# Still silent, but not yet 12h.
recent = {"_meta": {"last_heartbeat": time.time() - (HB - 60)}}
msgs, _ = run([no_stock], recent)
check("silent but inside window -> nothing", msgs, [])

# Silent for over 12h -> heartbeat, on Telegram only.
stale = {"_meta": {"last_heartbeat": time.time() - (HB + 60)}}
msgs, st = run([no_stock], stale)
check("silent past window -> one heartbeat", len(msgs), 1)
check("heartbeat says it is alive", "Tracker is alive" in msgs[0][0], True)
check("heartbeat is telegram-only", msgs[0][1], notifiers_mod.HEARTBEAT_CHANNELS)
check("heartbeat resets the clock", st["_meta"]["last_heartbeat"] > time.time() - 10, True)

# In stock while a heartbeat is due: the stock alert wins, no heartbeat noise.
msgs, st = run([in_stock], stale)
check("stock alert fires", any("IN STOCK" in m for m, _ in msgs), True)
check("stock alert suppresses heartbeat", len(msgs), 1)
check("stock alert goes to all channels", msgs[0][1], notifiers_mod.STOCK_CHANNELS)
check("stock alert also resets clock", st["_meta"]["last_heartbeat"] > time.time() - 10, True)

# The reported case: national stock, pincode never confirmed -> no notification.
unverified = CheckResult("flipkart", "https://u", in_stock=True, name="PS5")
msgs, _ = run([unverified], {})
check("unverified national stock sends NOTHING", msgs, [])

verified = CheckResult(
    "flipkart", "https://v", in_stock=True, name="PS5", pincode_verified=True
)
msgs, _ = run([verified], {})
check("verified deliverable stock DOES alert", len(msgs), 1)
check("  and says deliverable", "deliverable to" in msgs[0][0], True)
check("  to all channels", msgs[0][1], notifiers_mod.STOCK_CHANNELS)

# Suppression must not poison state: once verified, it still alerts.
state_module.save({}, state_path)
main.report([unverified], cfg_hb, False)
sent.clear()
main.report(
    [CheckResult("flipkart", "https://u", in_stock=True, name="PS5", pincode_verified=True)],
    cfg_hb,
    False,
)
check("a later verified check still alerts for the same URL", len(sent), 1)

# Alert text must go out before any screenshot upload, and a failing upload
# must not affect the alert.
order = []
orig_photo = notifiers_mod.broadcast_photo


def fake_broadcast_ordered(message, channels=notifiers_mod.STOCK_CHANNELS):
    order.append(("text", message[:20]))
    sent.append((message, channels))


def fake_photo(image, caption="", channels=None):
    order.append(("photo", len(image)))


notifiers_mod.broadcast = fake_broadcast_ordered
notifiers_mod.broadcast_photo = fake_photo

shot = CheckResult(
    "croma", "https://s", pincode="201301", in_stock=True, name="PS5",
    pincode_verified=True, screenshot=b"PNGDATA", evidence="Delivery at: Noida, 201301",
)
order.clear(); sent.clear()
state_module.save({}, state_path)
main.report([shot], cfg_hb, False)
check("text is sent before the screenshot", [k for k, _ in order], ["text", "photo"])
check("screenshot bytes forwarded", order[1][1], 7)
check("evidence line included in the alert", "Delivery at: Noida, 201301" in sent[0][0], True)

# A screenshot upload that explodes must not break the run or the alert.
def exploding_photo(image, caption="", channels=None):
    raise RuntimeError("upload failed")


notifiers_mod.broadcast_photo = exploding_photo
order.clear(); sent.clear()
state_module.save({}, state_path)
try:
    main.report([shot], cfg_hb, False)
    check("failing screenshot upload still leaves the alert sent", len(sent), 1)
except Exception as exc:
    check(f"failing screenshot upload must not raise ({exc})", False, True)

notifiers_mod.broadcast_photo = orig_photo
notifiers_mod.broadcast = fake_broadcast

# No screenshot (e.g. capture failed) must still alert.
noshot = CheckResult(
    "croma", "https://n", pincode="201301", in_stock=True, name="PS5",
    pincode_verified=True,
)
sent.clear(); state_module.save({}, state_path)
main.report([noshot], cfg_hb, False)
check("missing screenshot still alerts", len(sent), 1)

# Heartbeat surfaces unreadable checks rather than calling them sold out.
err = CheckResult("croma", "https://z", error="timeout")
msgs, _ = run([no_stock, err], stale)
check("heartbeat reports unknowns", "UNKNOWN, not confirmed sold out" in msgs[0][0], True)

notifiers_mod.broadcast = original_broadcast
state_path.unlink(missing_ok=True)

print()
print(f"{'FAILURES: ' + ', '.join(failures) if failures else 'ALL PASSED'}")
sys.exit(1 if failures else 0)
