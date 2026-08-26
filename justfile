# Rowan's life + family Hermes agent -- what only this agent has.
#
# Deployment lives in plow-pbc/agent-mgr, which owns the compose service, the
# bring-up, the pins and the contract tests for every agent on this host:
#
#   agent-mgr restore rowan     # config, the Plow Chat plugin, and skills.tsv
#   agent-mgr activate rowan    # prints a code; ROWAN texts it, from his phone
#   agent-mgr up rowan          # down / restart / logs
#   agent-mgr sign-in rowan     # device-code OAuth; hand the URL to Rowan
#   agent-mgr check-connectors rowan
#   agent-mgr agent rowan "what's on today?"
#
# Eleven recipes here re-implemented those. What is left is one recipe that does
# something agent-mgr does not yet do -- see below -- and `test`.

test:
    uv run --no-project --python 3.13 --with pytest==8.4.2 --with pyyaml==6.0.2 pytest -q

# Did Rowan's Mac answer with tools? Kept rather than replaced by
# `agent-mgr check-latch`, which classifies the same response into a verdict.
#
# Classification is what this recipe already failed at: an earlier version
# reported a two-frame SSE answer as "unparseable", valid JSON with no response
# frame as "unparseable", and a doubled 000 as HTTP 000000 -- four review rounds,
# one new mislabelled shape each time. A cause taxonomy has to enumerate the
# whole input space correctly or it lies. So this asks one question and prints
# the raw response as the evidence; a 401 body already says the token is bad and
# a 406 body already names the Accept header.
#
# Graduating that shape into agent-mgr is plow-pbc/agent-mgr#9. Until then this
# stays, and it reaches the container through `agent-mgr compose` -- the
# documented escape hatch for an agent's own recipes -- rather than a compose
# file this repo no longer owns.
check-latch:
    #!/usr/bin/env bash
    set -euo pipefail
    agent-mgr compose rowan ps --status running --quiet hermes | grep -q . \
      || { echo "the gateway is not running -- start it: agent-mgr up rowan" >&2; exit 1; }
    raw="$(mktemp)"; trap 'rm -f "$raw"' EXIT
    # Status on line one, body after. No `|| printf 000`: curl already writes
    # 000 via -w on a failed transfer AND exits non-zero, so the fallback
    # appended a second one and the verdict saw "000000" -- missing the very
    # branch that explains a transport failure. Measured in the container.
    agent-mgr compose rowan exec -T --user "$(id -u):$(id -g)" hermes sh -c '
      set -a; . /opt/data/.env; set +a
      : "${DOMO_DEVICE_UID:?empty in the dotenv -- mint a credential on Rowans Mac}"
      : "${DOMO_MCP_TOKEN:?empty in the dotenv -- mint a credential on Rowans Mac}"
      code=$(curl -sS --max-time 30 -o /tmp/latch-body -w "%{http_code}" \
        -X POST "https://api.plow.co/v1/relay/devices/$DOMO_DEVICE_UID/mcp" \
        -H "Authorization: Bearer $DOMO_MCP_TOKEN" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\",\"params\":{}}") || true
      [ -n "$code" ] || code=000
      printf "%s\n" "$code"
      cat /tmp/latch-body 2>/dev/null || true
      rm -f /tmp/latch-body' > "$raw"
    {{justfile_directory()}}/scripts/latch-verdict.py "$raw"
