"""
Lu.ma scraper — pulls events from Lu.ma's discovery API.

Hits the public-looking API at api2.luma.com/discover/get-paginated-events
for each category slug + city bounding box, paginates with the cursor, and
returns a deduplicated list of raw events.

No browser, no LLM — just HTTP. ~2 seconds for a typical pull.
"""

import json
import re
import time
from typing import Iterator, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

class ScrapeError(Exception):
    """Raised when a Lu.ma discovery request fails hard (network / HTTP).

    Soft empty pages (0 events for a small city) are NOT errors. A first-page
    failure for a slug in a city IS an error — the orchestrator treats that as
    a failed scrape and refuses to publish.
    """


API_BASE = "https://api2.luma.com/discover/get-paginated-events"
CALENDAR_GET_ITEMS = "https://api.lu.ma/calendar/get-items"
URL_RESOLVER = "https://api.lu.ma/url"

# Bounding boxes for cities you want to scrape. Add/remove freely.
# Coordinates are roughly the metro/Greater area of each city.
# Use https://bboxfinder.com or Google Maps to find bounds for new cities.
CITIES = {
    # United Kingdom
    "london":     {"north": 51.69, "south": 51.28, "east":   0.33, "west":  -0.51},
    "manchester": {"north": 53.65, "south": 53.35, "east":  -2.00, "west":  -2.50},
    "edinburgh":  {"north": 55.99, "south": 55.88, "east":  -3.05, "west":  -3.30},
    "glasgow":    {"north": 55.93, "south": 55.78, "east":  -4.05, "west":  -4.40},
    "birmingham": {"north": 52.55, "south": 52.40, "east":  -1.75, "west":  -2.00},
    "bristol":    {"north": 51.52, "south": 51.40, "east":  -2.45, "west":  -2.70},
    "cambridge":  {"north": 52.25, "south": 52.15, "east":   0.20, "west":   0.05},
    "oxford":     {"north": 51.80, "south": 51.70, "east":  -1.18, "west":  -1.32},
    "leeds":      {"north": 53.86, "south": 53.75, "east":  -1.45, "west":  -1.65},
    "brighton":   {"north": 50.86, "south": 50.80, "east":  -0.10, "west":  -0.20},
    "belfast":    {"north": 54.65, "south": 54.55, "east":  -5.85, "west":  -6.00},

    # International tech hubs
    "newyork":    {"north": 40.92, "south": 40.49, "east": -73.70, "west": -74.27},
    "sanfrancisco": {"north": 37.95, "south": 37.20, "east": -122.00, "west": -122.55},
    "berlin":     {"north": 52.68, "south": 52.34, "east":  13.76, "west":  13.08},
    "singapore":  {"north":  1.48, "south":  1.16, "east": 104.10, "west": 103.60},
    "lisbon":     {"north": 38.85, "south": 38.65, "east":  -9.05, "west":  -9.30},
    "dubai":      {"north": 25.40, "south": 24.85, "east":  55.55, "west":  54.85},
    "paris":      {"north": 48.95, "south": 48.74, "east":   2.55, "west":   2.20},
}

# Default city if none specified — keeps legacy behavior.
LONDON_BOUNDS = CITIES["london"]

# Categories to scrape. Add/remove freely. Common Lu.ma slugs:
#   tech, ai, crypto, startup, design, climate, fintech, web3
DEFAULT_SLUGS = ["tech", "ai", "crypto"]

# How many events per page. Lu.ma seems to use 10 in their UI.
PAGE_LIMIT = 50

# Politeness: small delay between paginated calls so we don't hammer the API.
PAGE_DELAY_SEC = 0.3

# User agent — be honest about who we are. Lu.ma seems to allow it; if they
# ever start blocking, we'd need to revisit.
UA = (
    "luma-events-pipeline/2.0 "
    "(Rare Founders UK events feed; contact: partnerships@rarefounders.com)"
)


def _get_json(url: str, timeout: int = 20) -> dict:
    req = Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
    })
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_paginated(slug: str, bounds: dict = LONDON_BOUNDS) -> Iterator[dict]:
    """Yield raw event entries for one category slug, all pages."""
    cursor = None
    page = 0
    while True:
        page += 1
        params = {
            "slug": slug,
            "north": bounds["north"],
            "south": bounds["south"],
            "east":  bounds["east"],
            "west":  bounds["west"],
            "pagination_limit": PAGE_LIMIT,
        }
        if cursor:
            params["pagination_cursor"] = cursor

        url = f"{API_BASE}?{urlencode(params)}"
        try:
            data = _get_json(url)
        except Exception as e:
            msg = f"[{slug}] page {page}: error {e}"
            print(f"  {msg}")
            # First page failure = hard fail (API down / blocked). Later-page
            # failure = stop pagination but keep what we have.
            if page == 1:
                raise ScrapeError(msg) from e
            return

        entries = data.get("entries", [])
        for entry in entries:
            yield entry

        has_more = data.get("has_more", False)
        cursor = data.get("next_cursor")
        print(f"  [{slug}] page {page}: {len(entries)} events; more={has_more}")

        if not has_more or not cursor:
            return

        time.sleep(PAGE_DELAY_SEC)


def fetch_all(
    slugs: list[str] = None,
    bounds: dict = LONDON_BOUNDS,
    city_name: str = "london",
) -> dict[str, dict]:
    """Fetch events for all slugs and dedupe on event_api_id.

    Args:
        slugs: list of Lu.ma category slugs (e.g. ["tech", "ai"])
        bounds: bounding box dict with north/south/east/west keys
        city_name: tag to attach to each event for debugging/filtering

    Returns: dict keyed by event_api_id → raw API entry.
    """
    if slugs is None:
        slugs = DEFAULT_SLUGS

    out: dict[str, dict] = {}
    for slug in slugs:
        print(f"[{city_name}] Fetching slug={slug}...")
        for entry in fetch_paginated(slug, bounds=bounds):
            event_id = entry.get("api_id") or entry.get("event", {}).get("api_id")
            if not event_id:
                continue
            if event_id not in out:
                entry["_discovered_via_slug"] = slug
                entry["_discovered_via_city"] = city_name
                out[event_id] = entry
    print(f"[{city_name}] Total unique events: {len(out)}")
    return out


def normalize(entry: dict) -> dict:
    """Flatten a Lu.ma API entry into the final schema for the website.

    All fields are website-ready: clean names, normalized values, no Lu.ma
    internals. The orchestrator (main.py) refreshes these on every run so
    status/guest_count/etc. stay current for the lifetime of the event.
    """
    event = entry.get("event", {}) or {}
    cal = entry.get("calendar", {}) or {}
    hosts = entry.get("hosts", []) or []
    ticket = entry.get("ticket_info", {}) or {}
    geo = event.get("geo_address_info", {}) or {}
    coord = event.get("coordinate") or {}

    first_host = hosts[0] if hosts else {}

    # Calendar-sourced events have no hosts list, so we fall back to calendar
    # info as a "host" proxy. SeedLegals events that arrive via calendar scrape
    # don't surface individual hosts — but the calendar IS the org.
    host_name = (
        first_host.get("name")
        or first_host.get("first_name")
        or _clean_calendar_name(cal.get("name", ""))
    )
    host_avatar = first_host.get("avatar_url") or cal.get("avatar_url", "")
    host_username = first_host.get("username", "") or cal.get("slug", "")

    # Status: a single string the website can switch on.
    if ticket.get("is_sold_out"):
        status = "sold_out"
    elif entry.get("waitlist_active"):
        status = "waitlist"
    elif ticket.get("require_approval"):
        status = "approval_required"
    elif ticket.get("is_near_capacity"):
        status = "near_capacity"
    else:
        status = "open"

    # Mode: in_person vs online. Lu.ma uses "offline" for IRL events and one of
    # several values for virtual events ("online", "zoom", "google_meet", etc.).
    # We treat anything that isn't a real-world venue type as "online".
    ONLINE_LOC_TYPES = {
        "online", "zoom", "google_meet", "webinar",
        "video", "virtual", "stream", "youtube",
    }
    raw_loc_type = (event.get("location_type") or "offline").lower().strip()
    if raw_loc_type in ONLINE_LOC_TYPES:
        mode = "online"
    elif event.get("virtual_info") and not event.get("coordinate"):
        # Belt-and-braces: if there's virtual_info AND no real coordinates,
        # the event is online regardless of label.
        mode = "online"
    else:
        mode = "in_person"

    # Address breakdown. Lu.ma hides exact street until RSVP for most events,
    # so we surface what's available: area (e.g., "Fitzrovia") + city + country.
    area     = geo.get("sublocality") or ""
    city     = geo.get("city") or ""
    country  = geo.get("country") or ""
    full_addr = ", ".join(p for p in [
        geo.get("address"),  # only set when host made it public
        area, city, country,
    ] if p)

    return {
        # === Core fields (per Vasily's spec) ===
        "event_id":        entry.get("api_id"),
        "title":           event.get("name", ""),
        "event_link":      f"https://lu.ma/{event.get('url', '')}",
        "cover_link":      event.get("cover_url", ""),
        "start_at":        entry.get("start_at") or event.get("start_at"),  # ISO 8601
        "end_at":          event.get("end_at"),
        "timezone":        event.get("timezone", "Europe/London"),
        "city":            city,
        "country":         country,
        "mode":            mode,                    # "in_person" or "online"
        "status":          status,                  # see above
        "guest_count":     entry.get("guest_count", 0),
        "host_name":       host_name,

        # === Useful extras for free (no extra API call needed) ===
        "area":            area,                    # neighborhood, e.g. "Fitzrovia"
        "address":         full_addr,               # full display string
        "lat":             coord.get("latitude"),
        "lng":             coord.get("longitude"),
        "is_free":         ticket.get("is_free", False),
        "spots_remaining": ticket.get("spots_remaining"),
        "host_avatar":     host_avatar,
        "host_username":   host_username,
        "calendar_name":   cal.get("name", ""),

        # === Pipeline metadata ===
        "scraped_for_city": entry.get("_discovered_via_city", ""),
        "scraped_via_slug": entry.get("_discovered_via_slug", ""),
    }


def fetch_normalized(
    slugs: list[str] = None,
    city: str = "london",
) -> list[dict]:
    """Convenience: fetch + normalize for one city in one call.

    Args:
        slugs: Lu.ma category slugs to scrape (defaults to tech+ai+crypto)
        city: key from CITIES dict (defaults to london)
    """
    if city not in CITIES:
        raise ValueError(
            f"Unknown city '{city}'. Available: {list(CITIES.keys())}. "
            "Add new cities to the CITIES dict in scraper.py."
        )
    raw = fetch_all(slugs, bounds=CITIES[city], city_name=city)
    return [normalize(e) for e in raw.values()]


# =============================================================================
# Calendar-based scraping (catches webinars + non-geocoded events that the
# bbox-based discovery API misses).
# =============================================================================

# Calendars (e.g. SeedLegals) often append " Events Calendar" or " Calendar" to
# their display name. Strip those so the host badge reads cleanly.
_CALENDAR_SUFFIXES = [
    " Events Calendar", " events calendar",
    " Calendar", " calendar",
    " Events", " events",
]
def _clean_calendar_name(name: str) -> str:
    if not name:
        return ""
    n = name.strip()
    for suf in _CALENDAR_SUFFIXES:
        if n.endswith(suf):
            n = n[: -len(suf)].strip()
            break
    return n


def resolve_calendar_id(url: str) -> Optional[str]:
    """Map a Lu.ma URL → calendar_api_id (cal-XXXXXXX).

    Two paths:
      1. URL already contains "cal-XXX" → extract directly (cheap).
      2. URL is a friendly slug like https://lu.ma/seedlegals → fetch the page,
         pull api_id from the embedded JSON. (One HTTP call per calendar
         per cron run.)
    """
    if not url:
        return None

    # Path 1
    m = re.search(r"(cal-[a-zA-Z0-9]+)", url)
    if m:
        return m.group(1)

    # Path 2: normalise host then fetch
    normalised = url.replace("https://luma.com/", "https://lu.ma/")
    try:
        req = Request(normalised, headers={"User-Agent": UA})
        with urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  resolve_calendar_id({url}) HTTP error: {e}")
        return None

    m = re.search(r'"api_id":"(cal-[a-zA-Z0-9]+)"', html)
    return m.group(1) if m else None


def fetch_calendar_meta(calendar_api_id: str) -> dict:
    """Fetch a calendar's name + avatar by inspecting its first upcoming event.

    Lu.ma's calendar/get-items endpoint doesn't include calendar metadata, but
    /url?url=<event-slug> returns the full nested calendar object. One call
    per calendar per cron run.
    """
    items = _fetch_calendar_items(calendar_api_id, limit=1)
    if not items:
        return {"name": "", "avatar_url": ""}
    first_event_slug = items[0].get("event", {}).get("url", "")
    if not first_event_slug:
        return {"name": "", "avatar_url": ""}
    try:
        url = f"{URL_RESOLVER}?{urlencode({'url': first_event_slug})}"
        data = _get_json(url)
        cal = (data.get("data", {}) or {}).get("calendar", {}) or {}
        return {
            "name": cal.get("name", ""),
            "avatar_url": cal.get("avatar_url", ""),
        }
    except Exception as e:
        print(f"  fetch_calendar_meta({calendar_api_id}) error: {e}")
        return {"name": "", "avatar_url": ""}


def _fetch_calendar_items(calendar_api_id: str, limit: int = 100) -> list[dict]:
    """Raw calendar/get-items call, returns entries list."""
    params = {
        "calendar_api_id": calendar_api_id,
        "period": "future",
        "pagination_limit": limit,
    }
    url = f"{CALENDAR_GET_ITEMS}?{urlencode(params)}"
    try:
        data = _get_json(url)
        return data.get("entries", []) or []
    except Exception as e:
        print(f"  _fetch_calendar_items({calendar_api_id}) error: {e}")
        return []


def fetch_from_calendar(
    calendar_api_id: str,
    display_name: str,
    display_avatar: str = "",
) -> list[dict]:
    """Fetch all upcoming events from one calendar, return discovery-API-shaped
    entries so they can flow through the existing normalize() pipeline.

    Calendar entries are slimmer than discovery entries (no hosts/ticket_info
    at the top level). We synthesise the missing fields:
      - "calendar" → populated with the calendar's display name + avatar so
        normalize()'s fallback uses them as the host badge.
      - "hosts" → empty list (normalize falls back to calendar info).
      - "ticket_info" → empty (status falls through to "open").
    """
    items = _fetch_calendar_items(calendar_api_id)
    if not items:
        return []

    out: list[dict] = []
    for entry in items:
        event = entry.get("event", {}) or {}
        if event.get("visibility") and event["visibility"] != "public":
            continue  # skip private / unlisted events
        synth = {
            "api_id":       event.get("api_id"),  # event id, not calev id
            "event":        event,
            "calendar": {
                "api_id":     calendar_api_id,
                "name":       display_name,
                "avatar_url": display_avatar,
            },
            "hosts":        [],
            "ticket_info":  {},
            "guest_count":  entry.get("guest_count", 0),
            "start_at":     event.get("start_at"),
            "_discovered_via_slug": "calendar",
            "_discovered_via_city": f"calendar:{display_name}",
        }
        out.append(synth)
    return out


def fetch_from_calendars(calendars: list[dict]) -> list[dict]:
    """Fetch + normalise events from a list of trusted calendars.

    Input: [{url, name}, ...] from gsheet_calendars.fetch_trusted_calendars().
    Output: list of normalized event dicts (same shape as fetch_normalized).
    """
    if not calendars:
        return []

    all_events: list[dict] = []
    for cal in calendars:
        url = cal.get("url", "")
        sheet_name = cal.get("name", "")
        print(f"  [calendar] resolving {url}...")

        cal_id = resolve_calendar_id(url)
        if not cal_id:
            print(f"  [calendar] {url} — couldn't resolve to cal-XXX; skip")
            continue

        # Get calendar avatar (one extra HTTP call per calendar).
        meta = fetch_calendar_meta(cal_id)
        # User's friendly name from sheet wins over Lu.ma's verbose calendar name.
        display_name = sheet_name or _clean_calendar_name(meta.get("name", ""))
        display_avatar = meta.get("avatar_url", "")

        entries = fetch_from_calendar(cal_id, display_name, display_avatar)
        print(f"  [calendar] {display_name}: {len(entries)} upcoming events")

        for entry in entries:
            all_events.append(normalize(entry))

        time.sleep(PAGE_DELAY_SEC)  # politeness between calendars

    return all_events


if __name__ == "__main__":
    import sys
    city = sys.argv[1] if len(sys.argv) > 1 else "london"
    events = fetch_normalized(city=city)
    print(json.dumps(events[:3], indent=2))
    print(f"\n{len(events)} total events for {city}.")
