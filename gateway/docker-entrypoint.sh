#!/bin/sh
# Apply database migrations, then start the API and participant web app.
set -eu

alembic upgrade head

# One process: rate limits use in-memory storage, which is per process.
# FORWARDED_ALLOW_IPS lists the proxies whose X-Forwarded-For header is trusted
# (used for per-client rate limits). Only widen it when the gateway port is not
# reachable from anywhere except the reverse proxy.
exec uvicorn --factory gateway.main:app_factory \
  --host 0.0.0.0 --port 8000 \
  --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
  --no-server-header --no-access-log
