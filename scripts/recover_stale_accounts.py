#!/usr/bin/env python3
import json, subprocess, sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

CAIRO_TZ = ZoneInfo("Africa/Cairo")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CALLER_SCRIPT = PROJECT_ROOT / "scripts" / "caller_script.py"
DSL_DATA_FILE = PROJECT_ROOT / "output" / "dsl_data.json"
STALE_AFTER = timedelta(hours=6)

DEFAULT_DELAY_SECONDS = 5

def log(message):
    timestamp = datetime.now(CAIRO_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"{timestamp} - {message}", flush=True)

def parse_ts(value):
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)

def load_accounts():
    if not DSL_DATA_FILE.exists():
        raise SystemExit(f"Dashboard data not found: {DSL_DATA_FILE}")
    with DSL_DATA_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and isinstance(data.get("accounts"), list):
        return data["accounts"]
    if isinstance(data, list):
        return data
    raise SystemExit(f"Unexpected structure in {DSL_DATA_FILE}")

def main():
    now = datetime.now(timezone.utc)
    cutoff = now - STALE_AFTER
    accounts = load_accounts()
    stale = []

    for account in accounts:
        number = str(account.get("lnd_number", "")).strip()
        label = account.get("ApartmentNumber") or account.get("apartment") or number
        if not number:
            log(f"WARNING: skipping dataset entry without lnd_number: {label}")
            continue
        last = parse_ts(account.get("lastUpdated"))
        if last is None or last < cutoff:
            stale.append((number, label, last))

    log(f"Recovery check: {len(stale)} of {len(accounts)} account(s) older than 6 hours.")
    if not stale:
        log("No stale accounts found; nothing to do.")
        return 0

    for number, label, last in stale:
        if last is None:
            age = "missing/invalid lastUpdated"
        else:
            mins = int((now - last).total_seconds() // 60)
            h, m = divmod(mins, 60)
            age = f"{h}h {m}m old"
        log(f"STALE {number} ({label}): {age}")

    selector = ",".join(x[0] for x in stale)
    log(f"Running caller_script.py for: {selector}")

    rc = subprocess.run(
        [sys.executable, "-u", str(CALLER_SCRIPT), selector, "--delay", str(DEFAULT_DELAY_SECONDS)],
        cwd=PROJECT_ROOT,
        check=False,
    ).returncode

    if rc == 0:
        log("Recovery run completed successfully.")
    else:
        log(f"Recovery run exited {rc}; failed accounts remain stale for the next recovery check.")
    return rc

if __name__ == "__main__":
    raise SystemExit(main())
