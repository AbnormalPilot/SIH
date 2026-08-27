#!/usr/bin/env sh
# Container entrypoint. Runs before whatever CMD the service declares.
#
# Seeding used to happen at image build time. With Postgres there is no
# database to talk to during a build, so it happens here — but only when the
# database is actually empty. seed and index are idempotent upserts, so
# re-running them is safe; it is just 216k rows of wasted work on every
# container restart, which on a VPS is every deploy.
set -e

python -c "from pushpak import storage; storage.init_db()"

if [ "${PUSHPAK_SEED_ON_START:-1}" = "1" ]; then
  if python -c "import sys; from pushpak import storage; sys.exit(0 if storage.stats()['total_quotes'] else 1)"; then
    echo "==> data already present, skipping seed"
  else
    echo "==> empty database, seeding 30 days of labelled synthetic history"
    python -m pushpak.cli seed --days 30
    python -m pushpak.cli index --tail 3
  fi
fi

exec "$@"
