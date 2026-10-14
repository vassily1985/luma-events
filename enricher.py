"""
LLM enrichment (Moonshot Kimi, OpenAI-compatible) for summary + audiences + industries.

- Only events never LLM-enriched, or whose title/description changed, are sent.
- Batched; thinking disabled + JSON mode to keep tokens minimal.
- Any failure returns partial results; caller keeps rules_tagger tags for the rest.
- Taxonomy/prompt reused from the original enricher so tags match the page filters.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen

MOONSHOT_BASE = os.environ.get("MOONSHOT_BASE", "https://api.moonshot.ai/v1/chat/completions")
MOONSHOT_MODEL = os.environ.get("MOONSHOT_MODEL", "kimi-k2.6")
BATCH_SIZE = int(os.environ.get("LLM_BATCH_SIZE", "10"))
WORKERS = int(os.environ.get("LLM_WORKERS", "4"))
UA = "Mozilla/5.0 (compatible; luma-events-feed/2.0)"

AUDIENCE_OPTIONS = [
    "founders",         # founder/CEO type attendees
    "exited",           # founders who have exited (sold company / IPO)
    "investors",        # broad: angels, VCs, family offices
    "angels",           # angel investors specifically
    "GPs",              # general partners (VC partners)
    "LPs",              # limited partners in funds
    "operators",        # startup employees (eng/PM/GTM/design), NOT founders
    "students",         # university students / aspiring founders
    "female founders",  # ONLY when exclusively for female founders
    "LGBTQ+",           # ONLY when explicitly LGBTQ+ / queer-themed
    "parents",          # ONLY when explicitly for parents
    "moms",             # ONLY when explicitly for mothers
    "dads",             # ONLY when explicitly for fathers
]

# Derived from a frequency analysis of ~530 real Lu.ma UK events.
# Each tag folds adjacent micro-themes (see RULES below) to avoid sparse chips.
INDUSTRY_OPTIONS = [
    "ai",          # incl. gen AI, LLMs, agents, agentic, ML
    "fintech",     # incl. payments, banking, wealth, insurtech
    "crypto",      # incl. web3, blockchain, DeFi, tokenisation
    "healthtech",  # incl. biotech, pharma, longevity, mental health
    "climate",     # incl. sustainability, nature tech, energy transition
    "devtools",    # dev infra, coding tools, engineering platforms
    "creator",     # creator economy, content, influencer, podcasting
    "commerce",    # e-commerce, retail, D2C, fashion, marketplaces
    "security",    # cyber, infosec, AI security
    "gaming",
    "robotics",
    "deeptech",    # hardware, frontier tech, quantum, space
]


USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0, "failed_batches": 0}


def content_hash(e: dict) -> str:
    s = (e.get("title") or "") + "\n" + (e.get("description") or "")
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def _html_unescape(s: str) -> str:
    import html
    return html.unescape(s)


def _doc_text(node) -> str:
    if isinstance(node, dict):
        t = node.get("text", "")
        kids = " ".join(_doc_text(c) for c in node.get("content", []) or [])
        return (t + " " + kids).strip()
    if isinstance(node, list):
        return " ".join(_doc_text(c) for c in node)
    return ""


def fetch_event_description(event_id: str) -> str:
    """Plain-text description via Luma's public event API (with 429 backoff)."""
    if not event_id:
        return ""
    url = f"https://api.lu.ma/event/get?event_api_id={event_id}"
    for attempt in range(4):
        try:
            req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
            return re.sub(r"\s+", " ", _doc_text(data.get("description_mirror") or {})).strip()
        except Exception as e:
            if "429" in str(e) and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            print(f"  description fetch failed for {event_id}: {e}")
            return ""
    return ""


def fetch_descriptions(events: list[dict]) -> None:
    todo = [e for e in events if not e.get("description")]
    def one(e):
        e["description"] = fetch_event_description(e.get("event_id", ""))
        time.sleep(0.3)
    with ThreadPoolExecutor(max_workers=int(os.environ.get("DESC_WORKERS", "3"))) as ex:
        list(ex.map(one, todo))
    ok = sum(1 for e in todo if e.get("description"))
    print(f"Descriptions: fetched {ok}/{len(todo)}")


def call_moonshot(prompt: str, api_key: str) -> str:
    body = json.dumps({
        "model": MOONSHOT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 3000,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }).encode()
    req = Request(MOONSHOT_BASE, data=body, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }, method="POST")
    with urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
    u = data.get("usage") or {}
    USAGE["prompt_tokens"] += u.get("prompt_tokens", 0)
    USAGE["completion_tokens"] += u.get("completion_tokens", 0)
    USAGE["requests"] += 1
    return data["choices"][0]["message"]["content"]


def _normalize_audiences(raw) -> list[str]:
    if not isinstance(raw, list):
        return ["founders"]
    lower = {a.lower(): a for a in AUDIENCE_OPTIONS}
    out = []
    for a in raw:
        if isinstance(a, str) and a.strip().lower() in lower:
            v = lower[a.strip().lower()]
            if v not in out:
                out.append(v)
    return out or ["founders"]


def _normalize_industries(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = []
    for i in raw:
        if isinstance(i, str) and i.strip().lower() in INDUSTRY_OPTIONS and i.strip().lower() not in out:
            out.append(i.strip().lower())
    return out


def enrich_batch(events_with_desc: list[dict], api_key: str) -> dict[str, dict]:
    items = [
        {
            "id": e["event_id"],
            "title": e.get("title", ""),
            "host": e.get("host_name", ""),
            "calendar": e.get("calendar_name", ""),
            "description": (e.get("description") or "")[:1200],
        }
        for e in events_with_desc
    ]
    prompt = (
        "You are tagging UK startup/tech events for a founder community.\n"
        "For each event below, produce three fields.\n"
        "\n"
        "1) summary: a concise 1-2 sentence paraphrase (max ~200 characters), UK English, correct spelling. DO NOT copy verbatim.\n"
        "\n"
        f"2) audiences: a JSON array of tags from this exact list: {AUDIENCE_OPTIONS}.\n"
        "   Pick ALL that apply. ALWAYS apply broader categories alongside narrower\n"
        "   ones (e.g. a 'Female founders dinner' gets ['female founders','founders'];\n"
        "   a 'Startup moms brunch' gets ['moms','parents','founders']).\n"
        "   Rules per tag:\n"
        "   - 'founders'    = events for founders / CEOs / startup leaders.\n"
        "   - 'exited'      = founders who have exited (sold / IPO / second-time).\n"
        "                     Always also include 'founders'.\n"
        "   - 'investors'   = broad: angels, VCs, family offices.\n"
        "   - 'angels'      = angel investors specifically. Also include 'investors'.\n"
        "   - 'GPs'         = general partners / VC partners. Also include 'investors'.\n"
        "   - 'LPs'         = limited partners in funds. Also include 'investors'.\n"
        "   - 'operators'   = startup employees (eng/PM/GTM/design), NOT founders.\n"
        "   - 'students'    = university students / aspiring founders.\n"
        "   - 'female founders' = ONLY when EXCLUSIVELY/primarily for female founders\n"
        "       (e.g. 'Women Founders Club', 'female-only'). Also include 'founders'.\n"
        "   - 'LGBTQ+'      = ONLY when explicitly LGBTQ+/queer-themed.\n"
        "   - 'parents'     = ONLY when explicitly for parents (e.g. 'Founder Parents').\n"
        "   - 'moms'        = ONLY when explicitly for mothers. Also include 'parents'.\n"
        "   - 'dads'        = ONLY when explicitly for fathers. Also include 'parents'.\n"
        "   If you genuinely cannot tell, return ['founders'] as the default.\n"
        "\n"
        f"3) industries: a JSON array of tags from this exact list: {INDUSTRY_OPTIONS}.\n"
        "   ONLY tag a vertical if the event is SPECIFICALLY focused on it. Generic\n"
        "   events (founder dinners, demo nights, mixers, networking, pitch nights,\n"
        "   VC office hours) => return [].\n"
        "   Tag fold-ins (use the canonical tag, not the synonym):\n"
        "   - 'ai' covers gen AI, LLMs, agents, agentic, machine learning, ML ops.\n"
        "   - 'fintech' covers payments, banking, wealth, insurtech.\n"
        "   - 'crypto' covers web3, blockchain, DeFi, tokenisation, NFTs.\n"
        "   - 'healthtech' covers biotech, pharma, longevity, mental health.\n"
        "   - 'climate' covers sustainability, nature tech, energy transition.\n"
        "   - 'devtools' covers dev infra, coding tools, engineering platforms.\n"
        "   - 'creator' covers creator economy, content, influencer, podcasting.\n"
        "   - 'commerce' covers e-commerce, retail, D2C, fashion, marketplaces.\n"
        "   - 'security' covers cyber, infosec, AI security.\n"
        "   - 'deeptech' covers hardware, frontier tech, quantum, space.\n"
        "   Multiple industries are fine when an event sits at an intersection\n"
        "   (e.g. AI Security Summit => ['ai','security']).\n"
        "\n"
        "Output strict JSON, no markdown, no commentary:\n"
        "{\"results\": [{\"id\":..., \"summary\":..., \"audiences\":[...], "
        "\"industries\":[...]}, ...]}\n"
        "\n"
        f"Events:\n{json.dumps(items, ensure_ascii=False)}"
    )

    last_err = None
    for attempt in range(3):
        try:
            out_text = call_moonshot(prompt, api_key)
            out_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", out_text.strip(), flags=re.MULTILINE)
            results = json.loads(out_text).get("results", [])
            break
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    else:
        USAGE["failed_batches"] += 1
        print(f"  LLM batch failed after retries: {type(last_err).__name__}: {str(last_err)[:200]}")
        return {}

    out: dict[str, dict] = {}
    valid_ids = {e["event_id"] for e in events_with_desc}
    for r in results:
        eid = r.get("id")
        if eid in valid_ids:
            out[eid] = {
                "summary": (r.get("summary") or "").strip(),
                "audiences": _normalize_audiences(r.get("audiences")),
                "industries": _normalize_industries(r.get("industries")),
            }
    return out


def enrich_events(events: list[dict], api_key: str) -> dict[str, dict]:
    """events must already carry 'description'. Returns {event_id: enrichment}."""
    batches = [events[i:i + BATCH_SIZE] for i in range(0, len(events), BATCH_SIZE)]
    print(f"LLM: enriching {len(events)} events in {len(batches)} batches "
          f"(model={MOONSHOT_MODEL}, workers={WORKERS})")
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for r in ex.map(lambda b: enrich_batch(b, api_key), batches):
            results.update(r)
    print(f"LLM: got {len(results)}/{len(events)} | usage {USAGE}")
    return results
