"""
Google Sheet loader — pulls promoted events from a published-as-CSV sheet.

The sheet must be set up via:
  File → Share → Publish to web → CSV → Entire document
Then copy the URL it gives you and put it in PROMOTED_SHEET_URL or pass via
the env var GSHEET_CSV_URL.

Expected columns (header row required, exact names):
  cover_url, title, event_link, start_at, end_at, address, host_name,
  audience, sector, status, summary, promoted

`promoted` should be TRUE for everything in this sheet (used for badge styling
on the website).
"""

import csv
import io
import os
from typing import Optional
from urllib.request import Request, urlopen

UA = "luma-london-scraper/1.0"


def fetch_promoted(sheet_url: Optional[str] = None) -> list[dict]:
    url = sheet_url or os.environ.get("GSHEET_CSV_URL", "")
    if not url:
        print("(no GSHEET_CSV_URL set; skipping promoted events)")
        return []

    try:
        req = Request(url, headers={"User-Agent": UA})
        with urlopen(req, timeout=20) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"Could not fetch Google Sheet: {e}")
        return []

    rows = list(csv.DictReader(io.StringIO(text)))
    out = []
    for i, r in enumerate(rows):
        if not r.get("title"):
            continue
        # Generate a stable id for promoted events.
        evt_id = f"promoted-{i}-{(r.get('event_link') or r['title'])[:40]}"
        out.append({
            "event_id":       evt_id,
            "title":          r.get("title", "").strip(),
            "event_link":     r.get("event_link", "").strip(),
            "cover_link":     r.get("cover_url", r.get("cover_link", "")).strip(),
            "start_at":       r.get("start_at", "").strip(),
            "end_at":         r.get("end_at", "").strip(),
            "timezone":       "Europe/London",
            "location_type":  "offline",
            "address":        r.get("address", "").strip(),
            "host_name":      r.get("host_name", "").strip(),
            "summary":        r.get("summary", "").strip(),
            "audience":       r.get("audience", "general").strip(),
            "sector":         r.get("sector", "general tech").strip(),
            "status":         r.get("status", "open").strip(),
            "promoted":       True,
            "discovered_via": "gsheet",
        })
    print(f"Loaded {len(out)} promoted events from Google Sheet.")
    return out


if __name__ == "__main__":
    print(fetch_promoted())
