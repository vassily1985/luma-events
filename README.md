# Lu.ma UK events feed (GitHub Actions)

Scrapes UK tech/AI/crypto events from Lu.ma (city discovery + 16 partner
calendars), tags them with **rule-based** keywords (no LLM), and publishes
`events-uk.json` for the Squarespace embed on rarefounders.com.

Served via GitHub Pages:
`https://vassily1985.github.io/luma-events/events-uk.json`

## Why this exists

The previous Mac Studio pipeline (`~/.openclaw/workspace/luma-pipeline`)
scraped correctly but **failed to publish** whenever Moonshot/Kimi enrichment
404’d — it threw before `save_output`, then only dead-link-swept the old file.
This package:

- drops Moonshot/Anthropic entirely
- always writes after a successful scrape
- **hard-fails** (non-zero exit, no commit) if the scrape is empty/broken
- runs on GitHub Actions so neither Mac needs to stay awake

## Layout

| File | Role |
|------|------|
| `main.py` | Orchestrator: scrape → rule-tag → write `events-uk.json` + `cache-uk.json` |
| `scraper.py` | Lu.ma discovery API + partner calendar pulls |
| `rules_tagger.py` | Keyword / slug tagging (audiences + industries) |
| `gsheet_calendars.py` | Loads active partner calendars from published sheet CSV |
| `gsheet_loader.py` | Optional promoted-events overlay (`GSHEET_CSV_URL`) |
| `sweep_dead_links.py` | HEAD-check event links; skippable via `SKIP_SWEEP=1` |
| `cache-uk.json` | Persists tags across runs (committed in repo) |
| `events-uk.json` | Website feed |
| `.github/workflows/update-events.yml` | Daily 05:15 UTC + manual dispatch |

## Tagging rules (summary)

- Default `audiences = ["founders"]`
- `scraped_via_slug=ai` → industry `ai`; `crypto` → `crypto`
- Title / host / calendar keywords add industries (fintech, climate, …) and
  audiences (`female founders`, `investors`, `students`, …)
- Summary: keep prior cache summary if present; else a short title-based stub

Vocab matches the Squarespace filter chips from the old enricher.

## Local run

```bash
cd luma-events   # or this package folder
python3 main.py
SKIP_SWEEP=1 python3 sweep_dead_links.py   # or omit SKIP_SWEEP for full ~8 min sweep
```

Env tunables:

| Var | Default | Meaning |
|-----|---------|---------|
| `ENABLED_REGIONS` | `uk` | Comma list of regions |
| `MIN_EVENTS` | `100` | Abort if scrape/visible count below this |
| `SKIP_SWEEP` | unset | `1` to skip dead-link HEAD checks |
| `SWEEP_DELAY_SEC` | `1.0` | Delay between HEAD requests |
| `GSHEET_CSV_URL` | unset | Optional promoted events CSV |

No API keys required.

## GitHub Action

Workflow: `.github/workflows/update-events.yml`

1. Checkout + Python 3.12  
2. `python3 main.py` then `sweep_dead_links.py`  
3. Commit `events-uk.json` + `cache-uk.json` only if changed  
4. Push to `main` with `contents: write` and `github.token`  
5. Commit message: `auto: YYYY-MM-DDTHH:MMZ (N events)`

Schedule: `15 5 * * *` UTC. Also `workflow_dispatch` (optional skip sweep).

If the scrape fails or yields &lt; `MIN_EVENTS`, the job exits non-zero and
**does not** overwrite the published feed.

## Disable the Mac Studio jobs (after Actions is green)

```bash
# LaunchAgent (6am pipeline)
launchctl unload ~/Library/LaunchAgents/com.user.luma-pipeline.plist
# optional: move/remove the plist so it doesn't reload

# Old Playwright cron (every 6h) — edit crontab and remove the luma_pipeline.js line
crontab -l
crontab -e
```

See `MIGRATION.md` for the full cutover checklist.

## Schema

Each event keeps the fields the Squarespace embed already reads, including:

`event_id`, `title`, `start_at`, `end_at`, `event_link`, `cover_link`,
`city`, `country`, `mode`, `status`, `guest_count`, `host_name`,
`audiences`, `industries`, `summary`, `promoted`, `scraped_via_slug`, …

## Cost

£0 — public Lu.ma HTTP + free GitHub Actions minutes + GitHub Pages.

## LLM enrichment (optional)
If the `MOONSHOT_API_KEY` repo secret is set, `enricher.py` sends new or retitled events to Moonshot Kimi (`kimi-k2.6` by default; override with the `MOONSHOT_MODEL` repo variable). It sends them in batches of 10, with thinking off and JSON output. The result is a summary plus audiences and industries from the fixed taxonomy. Results are cached per event in `cache-uk.json`. If any API call fails, those events keep their rules_tagger tags and are retried on the next run, so publishing is never blocked.
