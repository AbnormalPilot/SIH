#!/usr/bin/env bash
# Development stack, on one port.
#
#   http://127.0.0.1:3000            the whole prototype
#     /                              landing page
#     /basket /method /formula
#     /compliance /developers        documentation
#     /dashboard                     operations dashboard
#     /docs                          interactive API docs
#     /api/v1/...                    the API
#
# Two processes still run, but only the Next.js dev server is a URL you open.
# It proxies /api, /dashboard, /docs, /static and /openapi.json through to
# uvicorn (see web/next.config.mjs), which is exactly what nginx does in
# production — so development and the container behave the same and there is
# one address to remember.
#
# uvicorn binds 127.0.0.1:8000 as an implementation detail. Opening it directly
# gets you the operations dashboard on / and nothing else: it is not built or
# watched, so no edit under web/ will ever show up there.
#
# Ctrl-C stops both. The trap matters: without it the uvicorn child survives
# and port 8000 is still held the next time you run this.
set -euo pipefail
cd "$(dirname "$0")/.."

API_PORT="${PUSHPAK_API_PORT:-8000}"
WEB_PORT="${PUSHPAK_WEB_PORT:-3000}"

command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }
command -v npm >/dev/null || { echo "npm not found: install Node 20 or newer"; exit 1; }

# Next quietly moves to the next free port when this one is taken, and you can
# spend a while editing files while staring at a stale tab on the old one.
if command -v lsof >/dev/null && lsof -i :"${WEB_PORT}" -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "port ${WEB_PORT} is already in use; stop that process or set PUSHPAK_WEB_PORT"
  exit 1
fi

# A dev server that has wedged holds Next's project lock while listening on
# nothing, so the port check above passes and the new server is refused anyway.
# Name the process instead of letting that read as "the site is down".
if [ -f web/.next/dev/lock ]; then
  stale_pid=$(sed -n 's/.*"pid":\([0-9]*\).*/\1/p' web/.next/dev/lock)
  if [ -n "${stale_pid}" ] && kill -0 "${stale_pid}" 2>/dev/null; then
    echo "a next dev server for this project is already running (pid ${stale_pid})"
    echo "if http://127.0.0.1:${WEB_PORT} is not responding it has wedged: kill ${stale_pid}"
    exit 1
  fi
fi

# Seed on first run so the dashboard is not empty.
if [ ! -f data/pushpak.db ]; then
  echo "==> no database yet, seeding 30 days of labelled synthetic quotes"
  python3 -m pushpak.cli seed --days 30
  python3 -m pushpak.cli index --tail 3
fi

# Install rather than skip. Skipping used to leave the API running on its own,
# which looks like a working dev stack right up until you notice that editing
# the site changes nothing.
if [ ! -d web/node_modules ]; then
  echo "==> web/node_modules missing, installing (first run only)"
  (cd web && npm install)
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

echo "==> api        127.0.0.1:${API_PORT}  (internal, reload on)"
python3 -m uvicorn pushpak.api:app --host 127.0.0.1 --port "${API_PORT}" --reload &
pids+=($!)

echo "==> open       http://127.0.0.1:${WEB_PORT}"
echo "               site /  ·  dashboard /dashboard  ·  API docs /docs"
# PUSHPAK_API_ORIGIN is what next.config.mjs rewrites /api and /dashboard to,
# so the dev server reaches the uvicorn above on the same relative paths the
# production proxy uses.
(cd web && PUSHPAK_API_ORIGIN="http://127.0.0.1:${API_PORT}" \
  npm run dev -- --port "${WEB_PORT}") &
pids+=($!)

echo "==> Ctrl-C to stop both"
wait
