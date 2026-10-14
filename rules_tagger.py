"""
Rule-based event tagging — replaces Moonshot/Anthropic enrichment.

Maps scraped_via_slug + title/host/calendar keywords → audiences + industries.
Zero cost, deterministic, fast. Cache still stores results so tags persist.
"""

from __future__ import annotations

import re
from typing import Iterable

# Keep vocab aligned with the Squarespace embed filter chips / former enricher.
AUDIENCE_OPTIONS = [
    "founders",
    "exited",
    "investors",
    "angels",
    "GPs",
    "LPs",
    "operators",
    "students",
    "female founders",
    "LGBTQ+",
    "parents",
    "moms",
    "dads",
]

INDUSTRY_OPTIONS = [
    "ai",
    "fintech",
    "crypto",
    "healthtech",
    "climate",
    "devtools",
    "creator",
    "commerce",
    "security",
    "gaming",
    "robotics",
    "deeptech",
]

# slug → default industry (discovery API category).
SLUG_INDUSTRY = {
    "ai": "ai",
    "crypto": "crypto",
    "tech": None,       # generic; rely on title keywords
    "calendar": None,   # partner calendar; rely on keywords
}

# industry → keyword patterns (case-insensitive substring / word-ish match).
INDUSTRY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("ai", [
        r"\bai\b", r"a\.i\.", r"artificial intelligence", r"\bllm\b",
        r"genai", r"gen ai", r"machine learning", r"\bml\b", r"agentic",
        r"\bagents?\b", r"openai", r"chatgpt", r"copilot",
    ]),
    ("crypto", [
        r"\bcrypto\b", r"web3", r"blockchain", r"\bdefi\b", r"\bnft\b",
        r"tokeni[sz]ation", r"bitcoin", r"ethereum", r"\bweb 3\b",
    ]),
    ("fintech", [
        r"fintech", r"\bpayments?\b", r"\bbanking\b", r"\binsurtech\b",
        r"wealthtech", r"open banking", r"\bneobank",
    ]),
    ("healthtech", [
        r"healthtech", r"health tech", r"\bbiotech\b", r"\bpharma\b",
        r"longevity", r"mental health", r"medtech", r"digital health",
    ]),
    ("climate", [
        r"\bclimate\b", r"sustainab", r"cleantech", r"clean tech",
        r"nature tech", r"energy transition", r"net[- ]zero", r"\besg\b",
    ]),
    ("devtools", [
        r"devtools", r"dev tools", r"developer", r"\bdevops\b",
        r"engineering", r"\binfra\b", r"platform eng", r"coding",
    ]),
    ("creator", [
        r"creator econom", r"\binfluencer\b", r"\bpodcast", r"content creat",
    ]),
    ("commerce", [
        r"e-?commerce", r"\bretail\b", r"\bd2c\b", r"\bfashion\b",
        r"marketplace", r"\bdirect.to.consumer\b",
    ]),
    ("security", [
        r"\bcyber\b", r"infosec", r"\bsecurity\b", r"\bsecops\b",
    ]),
    ("gaming", [r"\bgaming\b", r"\bgames?\b", r"\besports\b"]),
    ("robotics", [r"\brobotic", r"\bdrones?\b"]),
    ("deeptech", [
        r"deep.?tech", r"\bquantum\b", r"\bspace\b", r"hardware",
        r"frontier tech", r"semiconductor",
    ]),
]

# audience → keyword patterns. Order matters for additive tags.
AUDIENCE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("female founders", [
        r"female founder", r"women founder", r"woman founder",
        r"women.?in.?tech", r"women.?in.?startup", r"ladies who",
        r"\bwomen\b.*\bfounder", r"founder.*\bwomen\b",
    ]),
    ("LGBTQ+", [
        r"lgbtq", r"\bqueer\b", r"\bpride\b", r"gay founder",
    ]),
    ("moms", [r"\bmoms?\b", r"\bmothers?\b", r"mum(s|my)?\b"]),
    ("dads", [r"\bdads?\b", r"\bfathers?\b"]),
    ("parents", [r"\bparents?\b", r"founder parent"]),
    ("students", [
        r"\bstudents?\b", r"university", r"\bcampus\b", r"aspiring founder",
    ]),
    ("exited", [
        r"\bexited\b", r"exit(ed)? founder", r"second.?time founder",
        r"\bipo\b",
    ]),
    ("angels", [r"\bangel investor", r"\bangels?\b"]),
    ("GPs", [r"\bgp\b", r"general partner", r"\bvc partner"]),
    ("LPs", [r"\blp\b", r"limited partner"]),
    ("investors", [
        r"\binvestors?\b", r"\bvc\b", r"venture capital", r"family office",
        r"\bangels?\b", r"pitch.*investor", r"investor.*pitch",
    ]),
    ("operators", [
        r"\boperators?\b", r"\bengineers?\b", r"\bdevelopers?\b",
        r"product manager", r"\bpm\b", r"\bgtm\b", r"startup employee",
    ]),
]


def _blob(event: dict) -> str:
    parts = [
        event.get("title") or "",
        event.get("host_name") or "",
        event.get("calendar_name") or "",
        event.get("summary") or "",
    ]
    return " ".join(parts).lower()


def _match_any(text: str, patterns: Iterable[str]) -> bool:
    for pat in patterns:
        if re.search(pat, text, flags=re.IGNORECASE):
            return True
    return False


def tag_industries(event: dict) -> list[str]:
    industries: list[str] = []
    slug = (event.get("scraped_via_slug") or "").lower().strip()
    slug_ind = SLUG_INDUSTRY.get(slug)
    if slug_ind and slug_ind in INDUSTRY_OPTIONS:
        industries.append(slug_ind)

    text = _blob(event)
    for industry, patterns in INDUSTRY_KEYWORDS:
        if industry in industries:
            continue
        if _match_any(text, patterns):
            industries.append(industry)

    # Keep stable order matching INDUSTRY_OPTIONS.
    order = {name: i for i, name in enumerate(INDUSTRY_OPTIONS)}
    return sorted(set(industries), key=lambda x: order.get(x, 999))


def tag_audiences(event: dict) -> list[str]:
    text = _blob(event)
    found: list[str] = []

    for audience, patterns in AUDIENCE_KEYWORDS:
        if _match_any(text, patterns):
            found.append(audience)

    # Defaults + hierarchy.
    if "founders" not in found:
        found.insert(0, "founders")

    # Broader parents when moms/dads present.
    if ("moms" in found or "dads" in found) and "parents" not in found:
        found.append("parents")

    # Broader investors when angels/GPs/LPs present.
    if any(a in found for a in ("angels", "GPs", "LPs")) and "investors" not in found:
        found.append("investors")

    # female founders always also founders (already defaulted).
    order = {name: i for i, name in enumerate(AUDIENCE_OPTIONS)}
    # Dedupe preserving preference order from AUDIENCE_OPTIONS.
    uniq = []
    seen = set()
    for a in found:
        if a in AUDIENCE_OPTIONS and a not in seen:
            uniq.append(a)
            seen.add(a)
    return sorted(uniq, key=lambda x: order.get(x, 999))


def stub_summary(event: dict) -> str:
    """Short title-based stub. Prefer empty for generic networking titles."""
    title = (event.get("title") or "").strip()
    if not title:
        return ""
    host = (event.get("host_name") or "").strip()
    city = (event.get("city") or "").strip()
    mode = event.get("mode") or ""
    bits = [title]
    if host:
        bits.append(f"Hosted by {host}.")
    elif city and mode == "in_person":
        bits.append(f"In {city}.")
    elif mode == "online":
        bits.append("Online event.")
    # Keep stub short for the embed.
    out = " ".join(bits)
    return out[:180]


def tag_event(event: dict, *, write_summary: bool = True) -> dict:
    """Return {audiences, industries, summary} for one event."""
    audiences = tag_audiences(event)
    industries = tag_industries(event)
    summary = ""
    if write_summary:
        # Only stub when no prior summary exists.
        summary = (event.get("summary") or "").strip() or stub_summary(event)
    return {
        "audiences": audiences,
        "industries": industries,
        "summary": summary,
    }


if __name__ == "__main__":
    samples = [
        {"title": "Women Founders Club Dinner", "host_name": "X", "scraped_via_slug": "tech"},
        {"title": "AI Agents Build Night", "host_name": "Builders", "scraped_via_slug": "ai"},
        {"title": "Crypto & DeFi Meetup", "scraped_via_slug": "crypto", "city": "London", "mode": "in_person"},
        {"title": "Angel Investor Pitch Night", "scraped_via_slug": "tech"},
        {"title": "Founder Networking Drinks", "scraped_via_slug": "calendar", "calendar_name": "SeedLegals"},
    ]
    for s in samples:
        print(s["title"], "→", tag_event(s))
