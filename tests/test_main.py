"""Verify main.py config validation, target splitting, and message formatting."""

import json
import sys
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

print("target splitting:")
cfg = {
    "pincode": "143001",
    "retailers": {
        "flipkart": ["https://fk/a", "  ", "https://fk/b"],
        "amazon": ["https://amazon/a"],
        "croma": ["https://croma/a"],
        "sonycenter": [],
    },
}
http_t, browser_t = main.targets(cfg, None)
check("http targets skip blanks", len(http_t), 2)
check("http targets are the httpx sites", [r for r, _ in http_t], ["flipkart", "flipkart"])
check(
    "amazon and croma both routed to browser",
    sorted(r for r, _ in browser_t),
    ["amazon", "croma"],
)

http_t, browser_t = main.targets(cfg, "flipkart")
check("--check flipkart filters http", len(http_t), 2)
check("--check flipkart excludes browser sites", browser_t, [])

http_t, browser_t = main.targets(cfg, "croma")
check("--check croma routes to browser only", (len(http_t), len(browser_t)), (0, 1))

http_t, browser_t = main.targets(cfg, "amazon")
check("--check amazon filters", (len(http_t), len(browser_t)), (0, 1))

check(
    "non-string url ignored",
    main.targets({"pincode": "1", "retailers": {"flipkart": [None, 42, "https://ok"]}}, None)[0],
    [("flipkart", "https://ok")],
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

print()
print(f"{'FAILURES: ' + ', '.join(failures) if failures else 'ALL PASSED'}")
sys.exit(1 if failures else 0)
