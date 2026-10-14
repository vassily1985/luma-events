"""
Orchestrator — UK (and optional multi-region) Lu.ma scrape → events-<region>.json.

Rules tagging (rules_tagger.py) for every event, then optional LLM enrichment
(enricher.py, Moonshot Kimi) when MOONSHOT_API_KEY is set. LLM failures fall
back to rules tags and never block publishing.
Hard-fails (non-zero exit) if a region scrape yields fewer than MIN_EVENTS
or if the discovery API throws — so CI never publishes an empty/stale file.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import scraper
import gsheet_loader
import gsheet_calendars
import rules_tagger
import enricher

HERE = Path(__file__).resolve().parent

REGIONS = {
    "uk": [
        "london", "oxford", "manchester", "edinburgh", "cambridge",
        "brighton", "glasgow", "birmingham", "bristol", "belfast", "leeds",
    ],
    "us": ["newyork", "sanfrancisco"],
    "eu": ["berlin", "paris", "lisbon"],
}

DEFAULT_ENABLED_REGIONS = ["uk"]
LOOKAHEAD_DAYS = 30
CATEGORIES = ["tech", "ai", "crypto"]

# Refuse to publish below this count for the UK region (tunable via env).
DEFAULT_MIN_EVENTS = 100


def cache_path(region: str) -> Path:
    return HERE / f"cache-{region}.json"


def output_path(region: str) -> Path:
    return HERE / f"events-{region}.json"


def load_cache(region: str) -> dict:
    p = cache_path(region)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception as e:
            print(f"Warning: cache for {region} unreadable ({e}); starting fresh")
    return {"events": {}}


def save_cache(region: str, cache: dict) -> None:
    cache_path(region).write_text(json.dumps(cache, indent=2, default=str))


def save_output(region: str, events: list[dict], cities: list[str]) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "region": region,
        "cities": cities,
        "count": len(events),
        "events": events,
    }
    output_path(region).write_text(json.dumps(payload, indent=2, default=str))


def is_in_window(event: dict) -> bool:
    start_at = event.get("start_at") or ""
    if not start_at:
        return True
    try:
        dt = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
    except Exception:
        return True
    now = datetime.now(timezone.utc)
    if dt < now:
        return False
    return (dt - now).days <= LOOKAHEAD_DAYS


def _normalize_promoted(e: dict) -> dict:
    """Align gsheet_loader fields with the website schema (cover_link, audiences)."""
    out = dict(e)
    if "cover_link" not in out and out.get("cover_url"):
        out["cover_link"] = out.pop("cover_url")
    # Old sheet used singular audience/sector; embed expects lists.
    if "audiences" not in out:
        aud = (out.get("audience") or "founders").strip()
        out["audiences"] = [aud] if aud else ["founders"]
    if "industries" not in out:
        sec = (out.get("sector") or "").strip()
        out["industries"] = [sec] if sec and sec != "general tech" else []
    out.setdefault("summary", "")
    out["promoted"] = True
    return out


def scrape_region(
    region: str,
    cities: list[str],
    trusted_calendars: list[dict],
) -> dict[str, dict]:
    """Fetch + dedupe. Raises scraper.ScrapeError on hard discovery failures."""
    luma_by_id: dict[str, dict] = {}
    for city in cities:
        events = scraper.fetch_normalized(slugs=CATEGORIES, city=city)
        added = 0
        for e in events:
            eid = e.get("event_id")
            if not eid or eid in luma_by_id:
                continue
            luma_by_id[eid] = e
            added += 1
        print(f"  [{city}] +{added} new (after region dedupe)")

    print(f"[{region}] {len(luma_by_id)} events via discovery API.")

    if trusted_calendars:
        print(f"[{region}] Pulling from {len(trusted_calendars)} trusted calendars...")
        cal_events = scraper.fetch_from_calendars(trusted_calendars)
        kept = 0
        skipped_non_uk = 0
        for e in cal_events:
            eid = e.get("event_id")
            if not eid or eid in luma_by_id:
                continue
            country = (e.get("country") or "").strip().upper()
            mode = e.get("mode", "")
            if mode == "in_person" and country and country not in (
                "GB", "UK", "UNITED KINGDOM", "",
            ):
                skipped_non_uk += 1
                continue
            luma_by_id[eid] = e
            kept += 1
        print(f"[{region}] +{kept} new events from calendars "
              f"(skipped {skipped_non_uk} non-UK).")

    print(f"[{region}] {len(luma_by_id)} unique events total.")
    return luma_by_id


def llm_enrich(merged: dict, luma_by_id: dict, now_iso: str) -> None:
    """Upgrade rules tags with LLM tags for new/changed events in this scrape."""
    api_key = os.environ.get("MOONSHOT_API_KEY", "").strip()
    if not api_key:
        print("(no MOONSHOT_API_KEY; keeping rules tags)")
        return
    try:
        todo = [merged[eid] for eid in luma_by_id
                if merged[eid].get("_classified_via") != "llm"
                or merged[eid].get("_llm_title") != merged[eid].get("title")
                or not merged[eid].get("_llm_desc")]
        todo = [r for r in todo if is_in_window(r)]
        cap = int(os.environ.get("LLM_MAX_EVENTS", "800"))
        todo = todo[:cap]
        if not todo:
            print("LLM: nothing new to enrich")
            return
        work = [dict(r) for r in todo]
        enricher.fetch_descriptions(work)
        # Events previously enriched without a description: only redo if we now have one.
        work = [w for w in work if w.get("_classified_via") != "llm"
                or w.get("_llm_title") != w.get("title") or w.get("description")]
        results = enricher.enrich_events(work, api_key)
        applied = 0
        for w in work:
            r = results.get(w["event_id"])
            if not r:
                continue  # keep rules tags; retried next run
            rec = merged[w["event_id"]]
            rec["audiences"] = r["audiences"]
            rec["industries"] = r["industries"]
            if r["summary"]:
                rec["summary"] = r["summary"]
            rec["_classified_via"] = "llm"
            rec["_classified_at"] = now_iso
            rec["_llm_title"] = rec.get("title")
            rec["_llm_hash"] = enricher.content_hash(w)
            rec["_llm_desc"] = bool(w.get("description"))
            applied += 1
        u = enricher.USAGE
        cost = u["prompt_tokens"] / 1e6 * 0.95 + u["completion_tokens"] / 1e6 * 4.00
        print(f"LLM: applied {applied}/{len(work)}; tokens in={u['prompt_tokens']} "
              f"out={u['completion_tokens']} est_cost=${cost:.4f}")
    except Exception as e:
        print(f"LLM enrichment error (keeping rules tags): {type(e).__name__}: {e}")


def run_for_region(
    region: str,
    cities: list[str],
    promoted: list[dict],
    trusted_calendars: list[dict],
    min_events: int,
) -> int:
    """Returns visible event count. Raises on scrape failure / below min_events."""
    print(f"\n=== REGION: {region.upper()} ({len(cities)} cities) ===")

    try:
        luma_by_id = scrape_region(region, cities, trusted_calendars)
    except scraper.ScrapeError as e:
        print(f"ERROR: scrape failed for {region}: {e}")
        raise

    if len(luma_by_id) < min_events:
        raise RuntimeError(
            f"[{region}] scrape yielded only {len(luma_by_id)} events "
            f"(min={min_events}). Refusing to publish — likely API issue."
        )

    cache = load_cache(region)
    cached_events = cache.get("events", {})
    now_iso = datetime.now(timezone.utc).isoformat()

    # Merge: fresh scrape fields + preserved classifier tags from cache.
    merged: dict[str, dict] = {eid: dict(e) for eid, e in cached_events.items()}
    newly_tagged = 0
    for eid, e in luma_by_id.items():
        record = dict(merged.get(eid, {}))
        preserved = {
            k: record[k]
            for k in ("audiences", "industries", "summary",
                      "_classified", "_classified_at", "_classified_via",
                      "_llm_hash", "_llm_title", "_llm_desc")
            if k in record
        }
        record.update(e)          # fresh volatile fields
        record.update(preserved)  # restore prior tags

        if not record.get("_classified"):
            tags = rules_tagger.tag_event(record, write_summary=True)
            record["audiences"] = tags["audiences"]
            record["industries"] = tags["industries"]
            if not (record.get("summary") or "").strip():
                record["summary"] = tags["summary"]
            record["_classified"] = True
            record["_classified_at"] = now_iso
            record["_classified_via"] = "rules"
            newly_tagged += 1
        else:
            record.setdefault("audiences", ["founders"])
            record.setdefault("industries", [])
            record.setdefault("summary", "")

        record["promoted"] = False
        merged[eid] = record

    print(f"[{region}] newly rule-tagged: {newly_tagged}")

    llm_enrich(merged, luma_by_id, now_iso)

    # Promoted overlay (sheet), scoped by city when set.
    region_cities_lower = {c.lower() for c in cities}
    for e in promoted:
        pe = _normalize_promoted(e)
        gs_city = (pe.get("city") or "").strip().lower()
        if gs_city and gs_city not in region_cities_lower:
            continue
        merged[pe["event_id"]] = pe

    visible = []
    dropped = {"past": 0, "no_date": 0}
    for e in merged.values():
        # Only include events still present in this scrape OR promoted.
        # Drop stale cache-only entries that are outside the window anyway.
        eid = e.get("event_id")
        if not e.get("promoted") and eid not in luma_by_id:
            # Keep in cache for tag reuse, but don't show if not in scrape
            # unless still in window from a prior scrape that somehow missed —
            # safer: hide anything not in this scrape and not promoted.
            continue
        if not is_in_window(e):
            if not e.get("start_at"):
                dropped["no_date"] += 1
            else:
                dropped["past"] += 1
            continue
        # Strip internal classifier flags from website payload.
        public = {k: v for k, v in e.items() if not k.startswith("_")}
        visible.append(public)

    visible.sort(key=lambda ev: (0 if ev.get("promoted") else 1, ev.get("start_at", "")))

    print(f"[{region}] visible: {len(visible)} "
          f"(dropped {dropped['past']} past, {dropped['no_date']} no-date)")

    if len(visible) < min_events:
        raise RuntimeError(
            f"[{region}] only {len(visible)} visible events after window filter "
            f"(min={min_events}). Refusing to publish."
        )

    save_output(region, visible, cities)
    cache["events"] = merged
    cache["last_run_at"] = now_iso
    save_cache(region, cache)
    print(f"[{region}] wrote {output_path(region).name} ({len(visible)} events)")
    return len(visible)


def main() -> int:
    print(f"=== Lu.ma pipeline (rules tagger) at {datetime.now().isoformat()} ===")

    env_regions = os.environ.get("ENABLED_REGIONS", "").strip()
    regions = (
        [r.strip() for r in env_regions.split(",") if r.strip()]
        if env_regions else DEFAULT_ENABLED_REGIONS
    )
    print(f"Regions to scrape: {regions}")

    min_events = int(os.environ.get("MIN_EVENTS", str(DEFAULT_MIN_EVENTS)))
    print(f"MIN_EVENTS={min_events}")

    for region in regions:
        if region not in REGIONS:
            print(f"ERROR: region '{region}' not in REGIONS. "
                  f"Available: {list(REGIONS.keys())}")
            return 1
        for city in REGIONS[region]:
            if city not in scraper.CITIES:
                print(f"ERROR: city '{city}' not in scraper.CITIES.")
                return 1

    promoted = gsheet_loader.fetch_promoted()
    trusted_calendars = gsheet_calendars.fetch_trusted_calendars()
    print(f"Trusted calendars: {len(trusted_calendars)} active")

    failures = 0
    for region in regions:
        try:
            run_for_region(
                region, REGIONS[region], promoted, trusted_calendars, min_events,
            )
        except Exception as e:
            failures += 1
            print(f"ERROR running region {region}: {e}")
            import traceback
            traceback.print_exc()

    if failures:
        print(f"\n=== Pipeline FAILED ({failures} region error(s)) ===")
        return 1

    print("\n=== Pipeline complete ===")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
