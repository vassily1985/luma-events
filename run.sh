#!/usr/bin/env bash
# Local / manual entry point (GitHub Actions uses the workflow directly).
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  # shellcheck source=/dev/null
  set -a; . ./.env; set +a
fi

export ENABLED_REGIONS="${ENABLED_REGIONS:-uk}"
export MIN_EVENTS="${MIN_EVENTS:-100}"

echo "=== scrape + tag ==="
python3 main.py

echo "=== dead-link sweep (SKIP_SWEEP=${SKIP_SWEEP:-0}) ==="
python3 sweep_dead_links.py

echo "Done. events-uk.json ready."
