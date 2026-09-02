#!/usr/bin/env bash
# The gateway's own log, which is where everything after the startup banner
# goes -- `docker logs` shows only the banner and is misleading on its own.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
docker exec "$CONTAINER" tail -n "${1:-60}" /var/lib/hermes/logs/gateway.log
