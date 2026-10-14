# Migration: Mac Studio → GitHub Actions

## What changed

| Before (Mac Studio) | After (this package) |
|---------------------|----------------------|
| LaunchAgent `com.user.luma-pipeline` @ 06:00 UK | GitHub Actions cron `15 5 * * *` UTC |
| Moonshot/Kimi enrichment (broken since ~5 Oct 2026) | `rules_tagger.py` (slug + keywords) |
| Enrichment exception → **no** `save_output` → stale feed | Hard-fail only on bad scrape; always save on success |
| Publish by copying into `~/luma-events` + git push from Mac | Action commits + pushes in-repo |
| Playwright `luma_pipeline.js` cron every 6h (unused leftover) | Removed / disable on Mac |
| API keys in launchctl / `.env` | **None required** |

GitHub Pages already serves `events-uk.json` from
`https://vassily1985.github.io/luma-events/events-uk.json` — no Pages
reconfiguration needed once this code lives in `vassily1985/luma-events`.

`cache-uk.json` is seeded from the last good Pages feed so prior LLM tags
survive the first Action run; new events get rule tags only.

## Cutover steps

1. **Push this package** into `vassily1985/luma-events` (or replace its
   contents). Keep existing `events-uk.json` until the first green run.
2. Confirm Actions is enabled for the repo; run **Update UK events feed**
   via *Run workflow* once (optionally with skip sweep).
3. Verify Pages updated (`generated_at` fresh, count ≳ 100).
4. **Disable Mac Studio jobs** (below).
5. Optional: delete `~/.openclaw/workspace/luma-pipeline` after a few green days.

## Disable Mac Studio jobs

On the Mac Studio (machine that currently runs the pipeline):

```bash
# 1) Stop the LaunchAgent
launchctl unload ~/Library/LaunchAgents/com.user.luma-pipeline.plist

# Confirm it is gone
launchctl list | grep luma || echo "no luma LaunchAgent"

# Optional: prevent reload after reboot
mv ~/Library/LaunchAgents/com.user.luma-pipeline.plist \
   ~/Library/LaunchAgents/com.user.luma-pipeline.plist.disabled
```

```bash
# 2) Remove the Playwright / openclaw-tools crontab line
crontab -l
# Look for anything referencing luma_pipeline.js or openclaw-tools
crontab -e
# Delete that line, save
```

Typical leftover path (do **not** keep running):

- `~/openclaw-tools/luma_pipeline.js` (or similar under `openclaw-tools`)
- Cron every 6 hours — leftover, does not feed GitHub Pages

## Rollback

If Actions misbehaves:

1. Re-enable the LaunchAgent plist (`launchctl load …`).
2. Revert `main` on GitHub to the last known-good `events-uk.json` commit.
3. File stays live on Pages either way — a failed Action does not wipe it.

## Follow-ups (out of scope here)

- RF Rare Founders calendar → events **sheet** sync (separate from this UK feed).
- Squarespace date-parser hardening for ordinals / bad expiry strings.
- Move partner promo intake to partnerships@ email.
