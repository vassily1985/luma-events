"""
Drops events whose Lu.ma page returns 404/410 (deleted/unpublished).

Runs after main.py writes events-<region>.json.

Env:
  SKIP_SWEEP=1     — exit 0 immediately (for fast CI / dry runs)
  SWEEP_DELAY_SEC  — seconds between HEAD requests (default 1.0; use 0.4 for
                     a lighter/faster sweep; too low risks 429s)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent

REQUEST_DELAY_SEC = float(os.environ.get("SWEEP_DELAY_SEC", "1.0"))
TIMEOUT_SEC = 12
UA = "luma-events-pipeline/2.0 (link-sweep)"


def is_dead(url: str) -> bool:
    """True only on clear 404/410. Keep event on any other status or error."""
    if not url:
        return True
    try:
        req = Request(url, method="HEAD", headers={"User-Agent": UA})
        with urlopen(req, timeout=TIMEOUT_SEC) as resp:
            return resp.status in (404, 410)
    except HTTPError as e:
        return e.code in (404, 410)
    except Exception:
        return False


def sweep_file(path: Path) -> int:
    data = json.loads(path.read_text())
    events = data.get("events", [])
    if not events:
        print(f"  [{path.name}] no events, skipping")
        return 0

    print(f"  [{path.name}] checking {len(events)} links "
          f"(delay={REQUEST_DELAY_SEC}s)...")
    kept = []
    dropped = []
    for i, e in enumerate(events, 1):
        link = e.get("event_link", "")
        if is_dead(link):
            dropped.append((e.get("title", "?")[:50], link))
        else:
            kept.append(e)
        if i % 50 == 0:
            print(f"  [{path.name}] ...{i}/{len(events)} "
                  f"({len(dropped)} dead so far)")
        if REQUEST_DELAY_SEC > 0:
            time.sleep(REQUEST_DELAY_SEC)

    if dropped:
        print(f"  [{path.name}] dropping {len(dropped)} dead event(s):")
        for title, link in dropped:
            print(f"    × {title}  ({link})")
        data["events"] = kept
        data["count"] = len(kept)
        path.write_text(json.dumps(data, indent=2, default=str))
        print(f"  [{path.name}] rewrote with {len(kept)} live events")
    else:
        print(f"  [{path.name}] all {len(events)} links live ✓")

    return len(dropped)


def main() -> int:
    if os.environ.get("SKIP_SWEEP", "").strip() in ("1", "true", "TRUE", "yes"):
        print("SKIP_SWEEP set — skipping dead-link sweep.")
        return 0

    files = sorted(HERE.glob("events-*.json"))
    if not files:
        print("No events-*.json files found.")
        return 0
    print(f"Sweeping {len(files)} file(s) for dead Lu.ma links...")
    total_dropped = 0
    for p in files:
        total_dropped += sweep_file(p)
    print(f"\n=== Sweep complete. {total_dropped} dead event(s) removed. ===")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
