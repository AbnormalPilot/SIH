#!/usr/bin/env bash
# Development stack: FastAPI with reload + Vite dev server, side by side.
#
#   API + dashboard   http://127.0.0.1:8000
#   Landing page      http://127.0.0.1:5173   (proxies /api and /dashboard to :8000)
#
# Ctrl-C stops both. The trap matters: without it the uvicorn child survives
# and port 8000 is still held the next time you run this.
set -euo pipefail
cd "$(dirname "$0")/.."

API_PORT="${PUSHPAK_API_PORT:-8000}"
WEB_PORT="${PUSHPAK_WEB_PORT:-5173}"

command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }

# Postgres is not started here. Either point PUSHPAK_DB_URL at your own server
# or bring up the compose one:  docker compose up -d db
echo "==> database        ${PUSHPAK_DB_URL:-postgresql://pushpak:pushpak@localhost:5432/pushpak}"

# Seed on first run so the dashboard is not empty. "Empty" is now a row count
# in Postgres rather than a missing file, and an unreachable database counts as
# empty so the seed attempt surfaces the real connection error.
if ! python3 -c "
from pushpak import storage
storage.init_db()
raise SystemExit(0 if storage.stats()['total_quotes'] else 1)
" 2>/dev/null; then
  echo "==> no data yet, seeding 30 days of labelled synthetic quotes"
  python3 -m pushpak.cli seed --days 30
  python3 -m pushpak.cli index --tail 3
fi

pids=()
cleanup() {
  echo ""
  echo "==> stopping dev servers"
  for pid in "${pids[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "==> API + dashboard  http://127.0.0.1:${API_PORT}  (reload on)"
python3 -m uvicorn pushpak.api:app --host 127.0.0.1 --port "${API_PORT}" --reload &
pids+=($!)

if [ -d web/node_modules ]; then
  echo "==> landing page     http://127.0.0.1:${WEB_PORT}"
  (cd web && npm run dev -- --port "${WEB_PORT}") &
  pids+=($!)
else
  echo "==> landing page skipped: run 'npm install' in web/ first"
fi

echo "==> Ctrl-C to stop both"
wait
