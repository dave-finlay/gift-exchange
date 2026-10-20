#!/bin/sh
# The app container's command: bring the database up to date, then serve.
# Gunicorn keeps no access log, since participants' links carry tokens
# (Caddy logs requests, without them), and has no control socket.
set -e
django-admin migrate --noinput
exec gunicorn giftexchange.wsgi \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_WORKERS:-3}" \
    --worker-tmp-dir "${WORKER_TMP_DIR:-/dev/shm}" \
    --no-control-socket
