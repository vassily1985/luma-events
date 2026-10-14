"""
Loads the trusted-Lu.ma-calendars list from Vasily's "Luma event calendars"
tab in the Events Displayed Google Sheet.

Each row: Calendar URL, Name, Active.
Only rows with Active=TRUE are returned.

The pipeline calls fetch_trusted_calendars() once per run; failures degrade
gracefully (returns []) so a broken sheet never breaks the cron.
"""

import csv
import io
from urllib.request import Request, urlopen

# Published CSV URL for the "Luma event calendars" tab in Events Displayed.
CALENDARS_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vRgBnOdC0765qudSXA4qyIccS5qXCF-RgUC2cApxm_3GNtpRboID3aG-MIYwI8ypVXZuFJcZ0wkJzgZ/"
    "pub?gid=788362596&single=true&output=csv"
)

UA = "luma-london-scraper/1.0 (calendar-list-loader)"


def fetch_trusted_calendars() -> list[dict]:
    """Return [{url, name}, ...] for every row where Active=TRUE.

    Robust to:
      - Header capitalisation differences (e.g. "Active" vs "Active (TRUE/FALSE)")
      - Trailing whitespace
      - Empty rows
    """
    try:
        req = Request(CALENDARS_CSV_URL, headers={"User-Agent": UA})
        with urlopen(req, timeout=20) as resp:
            text = resp.read().decode("utf-8")
    except Exception as e:
        print(f"WARN: couldn't fetch trusted calendars sheet: {e}")
        return []

    reader = csv.DictReader(io.StringIO(text))
    # Normalise header names so a "Active (TRUE/FALSE)" rename doesn't break us.
    def get_col(row, *names):
        for k in row.keys():
            if k.strip().lower() in {n.lower() for n in names}:
                return row[k].strip()
        return ""

    out: list[dict] = []
    for row in reader:
        active = get_col(row, "Active", "Active (TRUE/FALSE)").upper()
        if active not in ("TRUE", "YES", "1"):
            continue
        url = get_col(row, "Calendar URL", "URL")
        name = get_col(row, "Name", "Calendar Name")
        if url:
            out.append({"url": url, "name": name or url})
    return out


if __name__ == "__main__":
    import json
    cals = fetch_trusted_calendars()
    print(json.dumps(cals, indent=2))
    print(f"\n{len(cals)} active calendars.")
