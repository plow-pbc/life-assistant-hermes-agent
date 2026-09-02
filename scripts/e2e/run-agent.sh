#!/usr/bin/env bash
# (Re)start the agent container on the staged skills. This is the whole
# per-iteration step: edit a skill, run this, text the agent.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# FIRST, before anything that can fail. Everything below here exits on bad input
# -- a missing .env, an empty credential, a plow checkout that is not where
# PLOW_MAIN says -- and until this ran, that exit left the PREVIOUS container up.
# Restart a --latch run as a plain one, have it fail on any of those, and the
# relay you thought you had just turned off is still serving a real Mac. The
# container has to be gone before this script can fail at all.
# Before the teardown below, not after: a refusal must not remove the container
# on its way out, and the container this would remove might be the other cook's.
#
# $PPID is the invoking shell, so re-running from the same terminal is fine and
# a second terminal is not. A lock whose pid is gone is stale and taken over --
# a cook who closed their terminal should not need to know this file exists.
if [ -f "$LOCK_FILE" ]; then
  holder="$(cat "$LOCK_FILE" 2>/dev/null || true)"
  if [ -n "$holder" ] && [ "$holder" != "$PPID" ] && kill -0 "$holder" 2>/dev/null; then
    echo "instance '$E2E_INSTANCE' is in use by another shell (pid $holder)." >&2
    echo "Starting here would take its container out from under it." >&2
    echo "Use your own: E2E_INSTANCE=<name> scripts/e2e/run-agent.sh" >&2
    echo "If that shell is gone, remove ${LOCK_FILE#"$E2E_DIR"/}." >&2
    exit 1
  fi
fi
echo "$PPID" > "$LOCK_FILE"

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

load_env
require PLOW_API_BASE PLOW_AGENT_TOKEN PLOW_HOME_CHANNEL

# --fresh: throw the home away and start from an unset-up agent. That is the
# first half of a resume test (and what you want after an image rebuild);
# without it the container comes back onto the state it had.
# --latch: hand the container the REAL relay from .env. Off by default and
# deliberately opt-in -- the device on the other end is a person's actual Mac,
# and a run that does not need it should not be able to touch it.
WITH_LATCH=""
WITH_STUB=""
for arg in "$@"; do
  [ "$arg" = "--latch" ] && WITH_LATCH=1
  [ "$arg" = "--latch-stub" ] && WITH_STUB=1
done

# --latch-stub: a fake relay on this Mac instead of a real one, so the calendar
# step can be exercised with nothing at risk. Mutually exclusive with --latch,
# and loudly so: the two differ only in whether the far end is somebody's actual
# machine, which is not a thing to resolve by precedence.
if [ -n "$WITH_LATCH" ] && [ -n "$WITH_STUB" ]; then
  echo "--latch and --latch-stub are mutually exclusive: one is a real Mac," >&2
  echo "the other is a stub. Say which you meant." >&2
  exit 1
fi

# Whatever ran before, whichever mode this run is: a stub left over from an
# earlier run would keep answering on its port and a plain run would look
# relayless while still being served.
if [ -f "$STUB_PID_FILE" ]; then
  kill "$(cat "$STUB_PID_FILE")" 2>/dev/null || true
  rm -f "$STUB_PID_FILE" "$STUB_PORT_FILE"
fi

if [ -n "$WITH_STUB" ]; then
  # A fresh bearer per run, so the port is not an open endpoint for as long as
  # it is bound -- the stub binds 0.0.0.0 because host.docker.internal is how
  # the container reaches it, and a loopback bind is not reachable that way.
  LATCH_MCP_TOKEN="stub-$(head -c 18 /dev/urandom | base64 | tr -d '/+=')"
  rm -f "$STUB_PORT_FILE"
  # Its output goes to a file, not to ours. A background process holding this
  # script's stdout open means anything reading that output -- a pipe, a tee, a
  # CI step -- blocks until the stub dies, which is for as long as the container
  # runs. The log is also where you look to see whether the agent called the
  # tool at all.
  STUB_MODE="${STUB_MODE:-normal}" \
    "$E2E_DIR/latch_stub.py" --port 0 --port-file "$STUB_PORT_FILE" \
      --token "$LATCH_MCP_TOKEN" --verbose \
      >> "$STUB_LOG_FILE" 2>&1 &
  echo $! > "$STUB_PID_FILE"
  for _ in $(seq 1 50); do
    [ -s "$STUB_PORT_FILE" ] && break
    sleep 0.1
  done
  [ -s "$STUB_PORT_FILE" ] || { echo "latch stub did not come up" >&2; exit 1; }
  LATCH_MCP_URL="http://host.docker.internal:$(cat "$STUB_PORT_FILE")/mcp"
  export LATCH_MCP_URL LATCH_MCP_TOKEN
  WITH_LATCH=1   # from here the wiring is identical; only the far end differs
  echo "latch stub: $LATCH_MCP_URL (mode ${STUB_MODE:-normal})"
fi

# Asked for and not available is an error, not a warning. Passing the flag with
# nothing behind it used to print "wired (real Mac)" and start a container with
# empty LATCH_* -- the entrypoint would then take the relay OUT, and the run
# would look armed while ld-setup's every Latch call failed for want of a
# server. A flag that says it did something has to have done it.
if [ -n "$WITH_LATCH" ] && [ -z "$WITH_STUB" ]; then
  for name in LATCH_MCP_URL LATCH_MCP_TOKEN; do
    if [ -z "${!name:-}" ]; then
      echo "--latch needs $name in scripts/e2e/.env, and it is empty." >&2
      echo "Nothing mints it: paste the relay's URL and bearer in yourself." >&2
      echo "activate.sh merges rather than rewrites, so they will survive." >&2
      exit 1
    fi
  done
fi

if [ "${1:-}" = "--fresh" ] || [ "${2:-}" = "--fresh" ]; then
  # No `docker rm` here: the container went away above, which is also what frees
  # the volume for this to succeed.
  docker volume rm "$HOME_VOLUME" >/dev/null 2>&1 || true
  echo "removed $HOME_VOLUME -- this run starts from a brand new home"
fi
if [ -n "$WITH_STUB" ]; then
  echo "latch relay: wired (STUB -- no real Mac)"
else
  echo "latch relay: ${WITH_LATCH:+wired (real Mac)}${WITH_LATCH:-not wired}"
fi

echo "instance: $E2E_INSTANCE (container $CONTAINER, env ${ENV_FILE#"$E2E_DIR"/})"

"$E2E_DIR/sync-skills.sh"

# Derived, never written down: host ports move with the worktree and the
# compose project, and this is the same `plow-dev-env print` the stack's own
# `just api chat` uses. Only read -- the stack is not this loop's to change.
TWIN_UPLOAD_PORT="$("$PLOW_REPO/scripts/plow-dev-env" print "$PLOW_REPO/.plow-dev-env" PLOW_DTU_LINQ_PORT)"
[ -n "$TWIN_UPLOAD_PORT" ] || { echo "could not read PLOW_DTU_LINQ_PORT from $PLOW_REPO/.plow-dev-env" >&2; exit 1; }

# --platform: the published base is linux/amd64 and this is an arm64 Mac, so
# the run is emulated. Naming it here keeps docker from picking a manifest that
# does not exist rather than emulating.
#
# Every ld-* skill and SOUL.md is mounted from the staging tree, read-only --
# the agent runs them, it does not edit them. The mounts land flat under
# /var/lib/hermes/skills because every SKILL.md names an absolute skills path
# and each wrapper hops ../../ld-shared off its own realpath.
# The design assets ride along at a fixed path so an image round trip has
# something real to send without a `docker cp` after every restart.
#
# /srv, NOT anywhere under the home. Hermes refuses to deliver a model-emitted
# MEDIA: path under /var/lib (its media denylist is /etc /proc /sys /dev /root
# /boot /var/log /var/lib /var/run), and this image's whole HERMES_HOME is
# /var/lib/hermes -- so an asset parked inside the home is rejected with
# "Skipping unsafe MEDIA directive path" and the reply arrives as text only.
# Only the Hermes cache roots under the home are allowlisted past that.
mounts=(-v "$HOME_VOLUME:/var/lib/hermes"
        -v "$E2E_DIR/entrypoint.sh:/usr/local/bin/e2e-entrypoint:ro"
        -v "$E2E_DIR/upload-shim.py:/usr/local/bin/e2e-upload-shim:ro"
        -v "$STAGING/SOUL.md:/var/lib/hermes/SOUL.md:ro"
        -v "$REPO_DIR/docs/onboarding-v2/assets:/srv/e2e-assets:ro")
for skill in "$STAGING"/skills/*/; do
  mounts+=(-v "$skill:/var/lib/hermes/skills/$(basename "$skill"):ro")
done

# Sampled off the VOLUME, before anything starts, so the new gateway cannot
# write its line between the start and this read.
before_ready="$(docker run --rm --platform linux/amd64 -v "$HOME_VOLUME:/home" \
  alpine sh -c 'grep -c "websocket connected" /home/logs/gateway.log 2>/dev/null || echo 0' \
  2>/dev/null | tr -d "[:space:]")"
[ -n "$before_ready" ] || before_ready=0

docker run -d --name "$CONTAINER" --platform linux/amd64 \
  -e PLOW_API_BASE="$PLOW_API_BASE" \
  -e PLOW_AGENT_TOKEN="$PLOW_AGENT_TOKEN" \
  -e PLOW_HOME_CHANNEL="$PLOW_HOME_CHANNEL" \
  -e HERMES_CUSTOM_PLOW_API_KEY="$HERMES_CUSTOM_PLOW_API_KEY" \
  -e HERMES_HOME=/var/lib/hermes \
  -e HERMES_DISABLE_LAZY_INSTALLS=1 \
  -e API_SERVER_HOST=127.0.0.1 -e API_SERVER_PORT=8642 \
  -e PYTHONUNBUFFERED=1 \
  -e TZ="$TZ" \
  -e TWIN_UPLOAD_PORT="$TWIN_UPLOAD_PORT" \
  -e LATCH_MCP_URL="${WITH_LATCH:+${LATCH_MCP_URL:-}}" \
  -e LATCH_MCP_TOKEN="${WITH_LATCH:+${LATCH_MCP_TOKEN:-}}" \
  "${mounts[@]}" \
  --entrypoint /usr/local/bin/e2e-entrypoint \
  "$IMAGE" >/dev/null

# Readiness is the plugin's own websocket line, and it is read from the log
# FILE rather than `docker logs`: the gateway writes its startup banner to
# stdout but everything after it goes to $HERMES_HOME/logs/gateway.log, so a
# stdout grep waits out the whole timeout on a container that came up fine.
#
# Counted, not matched. That log lives on the home volume, so it SURVIVES the
# container -- a bare grep matches the previous run's "websocket connected" and
# returns instantly, reporting a gateway that has not started yet and printing
# the last run's lines as if they were this one's. The count taken before the
# start is the only thing that distinguishes them.
echo -n "$CONTAINER started; waiting for the plow_chat websocket"
for _ in $(seq 1 60); do
  now="$(docker exec "$CONTAINER" sh -c \
    'grep -c "websocket connected" /var/lib/hermes/logs/gateway.log 2>/dev/null || echo 0')"
  if [ "${now:-0}" -gt "${before_ready:-0}" ]; then
    echo " -- up"
    docker exec "$CONTAINER" grep -E "plow_chat|Gateway running" \
      /var/lib/hermes/logs/gateway.log | tail -4
    exit 0
  fi
  echo -n "."
  sleep 2
done
echo " -- TIMED OUT"
docker exec "$CONTAINER" tail -20 /var/lib/hermes/logs/gateway.log 2>&1 || true
exit 1
