# life-assistant -- what only this agent has.
#
# Deployment lives in plow-pbc/agent-mgr, which owns the compose service, the
# bring-up, the pins and the contract tests for every agent on this host.
# <agent> below is the registry name of a registered instance. `life` is the
# only one that may be registered today -- see README "Migrating rowan":
#
#   agent-mgr restore <agent>     # config and the Plow Chat plugin (skills.tsv is empty)
#   agent-mgr activate <agent>    # prints a code; its OWNER texts it, from their phone
#   agent-mgr up <agent>          # down / restart / logs
#   agent-mgr sign-in <agent>     # device-code OAuth; hand the URL to its owner
#   agent-mgr agent <agent> "what's the weather?"
#
#   # The dashboard crons -- NOT replayed by restore, so bring-up is not done
#   # without this. `hermes cron` persists to /opt/data/cron/jobs.json, which
#   # agent-mgr does not touch. Create-if-missing, safe to re-run.
#   # --user because `compose exec` lands as ROOT (measured), and on a fresh
#   # instance jobs.json does not exist yet -- an unpinned exec creates the
#   # schedule root-owned and the gateway can never touch it again.
#   agent-mgr compose <agent> exec -T --user "$(id -u):$(id -g)" hermes \
#     /opt/data/skills/ld-dashboard/scripts/register_crons.py
#
# No check-connectors: this instance installs no plow-connectors. See README
# "No connectors, and what that costs".
#
# Eleven recipes here re-implemented those. What is left is one recipe that does
# something agent-mgr does not yet do -- see below -- and `test`.
#
# Requires just >= 1.27, for the [doc("...")] attributes below. An older just
# does not degrade to a missing description -- it fails to parse the whole file,
# so EVERY recipe stops working, `check-latch` included. This repo is shared by
# every instance, so that lands on an owner's Mac whose just nobody checked.

# `pytest -q tests/`, never a bare `pytest`. The vendored ld- suites under
# ld-shared/ and test_wrappers.py are named test_*.py and define test_* functions,
# so an unscoped run collects them -- but they report through a counter instead of
# raising, so every one of them passes even when it fails. They run as subprocesses
# from tests/test_vendored_suites.py instead, where the exit code is the verdict.
[doc("Run the whole suite: this repo's contracts plus the vendored ld- suites.")]
test:
    uv run --no-project --python 3.13 --with pytest==8.4.2 --with pyyaml==6.0.2 pytest -q tests/

# Did that instance's Mac answer with tools? Kept rather than replaced by
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
#
# `agent` is REQUIRED and deliberately has no default. This repo is shared by
# every instance, so a default would be one person's name, and a bare
# `just check-latch` run from the wrong checkout would probe a DIFFERENT
# owner's container and report its health as this one's. That is the same class
# as plow-pbc/agent-mgr#13 -- an instance name resolving to a container someone
# else is running -- and the cheapest place to close it is to refuse to guess.
[doc("Probe one instance's Latch reachability from inside its own container.")]
check-latch agent:
    #!/usr/bin/env bash
    set -euo pipefail
    agent-mgr compose "{{agent}}" ps --status running --quiet hermes | grep -q . \
      || { echo "the gateway is not running -- start it: agent-mgr up {{agent}}" >&2; exit 1; }
    raw="$(mktemp)"; trap 'rm -f "$raw"' EXIT
    # Status on line one, body after. No `|| printf 000`: curl already writes
    # 000 via -w on a failed transfer AND exits non-zero, so the fallback
    # appended a second one and the verdict saw "000000" -- missing the very
    # branch that explains a transport failure. Measured in the container.
    agent-mgr compose "{{agent}}" exec -T --user "$(id -u):$(id -g)" hermes sh -c '
      set -a; . /opt/data/.env; set +a
      : "${DOMO_DEVICE_UID:?empty in the dotenv -- mint a credential on this owners Mac}"
      : "${DOMO_MCP_TOKEN:?empty in the dotenv -- mint a credential on this owners Mac}"
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
