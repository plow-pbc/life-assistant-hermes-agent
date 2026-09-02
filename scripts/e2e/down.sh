#!/usr/bin/env bash
# Stop the agent. The Plow stack and the twin are NOT this loop's to stop --
# they are shared, brought up elsewhere, and other work may be using them.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
docker rm -f "$CONTAINER" >/dev/null 2>&1 && echo "removed $CONTAINER" || echo "$CONTAINER was not running"
# The home volume is deliberately left behind: it holds the conversation this
# loop is usually in the middle of. `run-agent.sh --fresh` is how you drop it.
echo "kept $HOME_VOLUME (run-agent.sh --fresh drops it)"
