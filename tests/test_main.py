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
check("valid config", main.load_config()["pincode"], "143001")

with_config("{bad json")
expect_exit("invalid JSON", main.load_config)

with_config({"retailers": {}})
expect_exit("missing pincode", main.load_config)

with_config({"pincode": "143001", "retailers": {"blinkit": []}})
expect_exit("unknown retailer", main.load_config)

with_config({"pincode": 143001, "retailers": {}})
check("numeric pincode coerced", main.load_config()["pincode"], "143001")

print("per-retailer pincode overrides:")
with_config(
    {
        "pincode": "143001",
        "pincode_overrides": {"croma": "141001"},
        "retailers": {"croma": ["https://c"], "flipkart": ["https://f"]},
    }
)
cfg_ov = main.load_config()
check("override applies to croma", main.pincode_for(cfg_ov, "croma"), "141001")
check("default applies elsewhere", main.pincode_for(cfg_ov, "flipkart"), "143001")

with_config({"pincode": "143001", "retailers": {}})
check("no overrides key is fine", main.pincode_for(main.load_config(), "croma"), "143001")

with_config({"pincode": "143001", "pincode_overrides": {"croma": "  "}, "retailers": {}})
check(
    "blank override falls back",
    main.pincode_for(main.load_config(), "croma"),
    "143001",
)

with_config({"pincode": "143001", "pincode_overrides": {"blinkit": "1"}, "retailers": {}})
expect_exit("unknown retailer in overrides", main.load_config)

print("disabled retailers:")
with_config({
    "pincode": "143001",
    "disabled": ["croma"],
    "retailers": {"croma": ["https://c1", "https://c2"], "vijaysales": ["https://v"]},
})
cfg_dis = main.load_config()
check("disabled parsed", cfg_dis["disabled"], {"croma"})
h, b = main.targets(cfg_dis, None)
check("disabled retailer not checked", [r for r, _ in h + b], ["vijaysales"])
check("its URLs are kept in config", len(cfg_dis["retailers"]["croma"]), 2)
h, b = main.targets(cfg_dis, "croma")
check("--check cannot re-enable a disabled retailer", h + b, [])

with_config({"pincode": "1", "disabled": "croma", "retailers": {}})
check("bare string accepted", main.load_config()["disabled"], {"croma"})

with_config({"pincode": "1", "disabled": ["blinkit"], "retailers": {}})
expect_exit("unknown retailer in disabled", main.load_config)

with_config({"pincode": "1", "retailers": {}})
check("no disabled key is fine", main.load_config()["disabled"], set())

print("target splitting:")
# Derived from the registry rather than hardcoded, so moving a retailer between
# the httpx and browser paths does not invalidate these assertions.
from checkers import BROWSER_CHECKERS, HTTP_CHECKERS

A_BROWSER = sorted(BROWSER_CHECKERS)[0]
AN_HTTP = sorted(HTTP_CHECKERS)[0]

cfg = {
    "pincode": "143001",
    "retailers": {
        AN_HTTP: ["https://h/a", "  ", "https://h/b"],
        A_BROWSER: ["https://b/a"],
    },
}
http_t, browser_t = main.targets(cfg, None)
check("http targets skip blanks", len(http_t), 2)
check("http targets are the httpx sites", [r for r, _ in http_t], [AN_HTTP, AN_HTTP])
check("browser site routed to browser", browser_t, [(A_BROWSER, "https://b/a")])
check("no overlap between the two buckets", set(r for r, _ in http_t) & set(r for r, _ in browser_t), set())

http_t, browser_t = main.targets(cfg, AN_HTTP)
check(f"--check {AN_HTTP} filters http", len(http_t), 2)
check(f"--check {AN_HTTP} excludes browser sites", browser_t, [])

http_t, browser_t = main.targets(cfg, A_BROWSER)
check(f"--check {A_BROWSER} routes to browser only", (len(http_t), len(browser_t)), (0, 1))

check(
    "non-string url ignored",
    main.targets({"pincode": "1", "retailers": {AN_HTTP: [None, 42, "https://ok"]}}, None)[0],
    [(AN_HTTP, "https://ok")],
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
cfg_report = {"pincode": "143001", "pincode_overrides": {}, "retailers": {}}
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
cfg_hb = {"pincode": "143001", "pincode_overrides": {}, "retailers": {}}
no_stock = CheckResult("flipkart", "https://x", in_stock=False, name="PS5")
in_stock = CheckResult("flipkart", "https://y", in_stock=True, name="PS5")


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

# Heartbeat surfaces unreadable checks rather than calling them sold out.
err = CheckResult("croma", "https://z", error="timeout")
msgs, _ = run([no_stock, err], stale)
check("heartbeat reports unknowns", "UNKNOWN, not confirmed sold out" in msgs[0][0], True)

notifiers_mod.broadcast = original_broadcast
state_path.unlink(missing_ok=True)

print()
print(f"{'FAILURES: ' + ', '.join(failures) if failures else 'ALL PASSED'}")
sys.exit(1 if failures else 0)
