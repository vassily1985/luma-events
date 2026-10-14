#!/usr/bin/env python3
"""Build rf-events.json: public, not-yet-ended events hosted on the Rare Founders Luma calendar only."""
import json, os, sys, urllib.request, urllib.parse
from datetime import datetime, timezone

KEY = os.environ["LUMA_API_KEY"]
CAL = os.environ.get("RF_CALENDAR_ID", "cal-ABeCUrnGg1dt37l")
BASE = "https://public-api.luma.com/v1/calendar/list-events"
SQ = "https://images.lumacdn.com/cdn-cgi/image/format=auto,fit=cover,dpr=2,background=white,quality=75,width=400,height=400/"

def get(params):
    req = urllib.request.Request(BASE + "?" + urllib.parse.urlencode(params),
                                 headers={"x-luma-api-key": KEY, "accept": "application/json", "user-agent": "rf-events/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

now = datetime.now(timezone.utc)
params = {"after": now.strftime("%Y-%m-%dT00:00:00Z"), "pagination_limit": 50}
out, cursor = [], None
for _ in range(50):
    if cursor: params["pagination_cursor"] = cursor
    d = get(params)
    for e in d.get("entries", []):
        ev = e.get("event", e)
        cal = ev.get("calendar_id") or ev.get("calendar_api_id")
        if cal != CAL: continue                      # added from another host's calendar
        if ev.get("visibility") != "public": continue
        end = ev.get("end_at") or ev.get("start_at")
        if datetime.fromisoformat(end.replace("Z", "+00:00")) < now: continue
        cover = ev.get("cover_url") or ""
        path = cover.split("images.lumacdn.com/", 1)[1] if "images.lumacdn.com/" in cover else ""
        url = ev.get("url") or ""
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        out.append({"name": ev["name"], "start_at": ev["start_at"], "end_at": ev.get("end_at"),
                    "timezone": ev.get("timezone"), "url": f"https://luma.com/{slug}",
                    "image": (SQ + path) if path else cover})
    cursor = d.get("next_cursor")
    if not d.get("has_more") or not cursor: break

out.sort(key=lambda x: x["start_at"])
new = {"calendar_id": CAL, "count": len(out), "events": out}
try:
    old = json.load(open("rf-events.json"))
    if old.get("events") == out: print("unchanged", len(out)); sys.exit(0)
except Exception: pass
new["generated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
json.dump(new, open("rf-events.json", "w"), indent=2, ensure_ascii=False)
print("wrote", len(out)); [print(" ", e["start_at"], e["name"]) for e in out]
