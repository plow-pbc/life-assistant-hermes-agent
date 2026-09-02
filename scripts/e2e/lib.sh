# Shared settings for the local e2e loop. Sourced, never executed.
set -euo pipefail

E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$E2E_DIR/../.." && pwd)"

# Which loop this is. Two cooks in two worktrees were sharing one container,
# one home volume and one .env, so whoever ran second silently took the first
# one's agent out from under them. E2E_INSTANCE separates them.
#
# `default` deliberately suffixes NOTHING. The names below are the ones already
# running on people's machines and written into their notes; renaming the
# default instance would strand every container that exists right now -- the
# scripts would stop finding it, `down.sh` would not remove it, and the next
# run-agent.sh would leave it behind rather than replace it.
E2E_INSTANCE="${E2E_INSTANCE:-default}"
if [ "$E2E_INSTANCE" = "default" ]; then
  INSTANCE_SUFFIX=""
else
  INSTANCE_SUFFIX="-$E2E_INSTANCE"
fi

# The agent's image, container and the staging tree its skills are mounted
# from. One name each PER INSTANCE -- a second copy of any of them is how two
# loops end up fighting over one container.
IMAGE=life-agent:e2e
CONTAINER="life-agent-e2e$INSTANCE_SUFFIX"
# Staging is per-instance too: run-agent.sh mounts it into the container, so a
# shared tree means one cook's skill edit lands in the other's agent mid-run.
STAGING="$E2E_DIR/staging$INSTANCE_SUFFIX"

# A named volume over HERMES_HOME, so state outlives a restart -- which is what
# makes resume testable: onboarding's record of progress is ld/config.json, and
# with an ephemeral home every restart is a fresh owner. It is a NAMED volume,
# not a host bind: docker seeds an empty named volume from the image's own
# contents on first use, so config.yaml, the plugins and the skills skeleton
# survive being mounted over. A host bind would hide them and the gateway would
# not boot. The cost is that the seeding happens once -- an image rebuild that
# changes anything under the home needs `run-agent.sh --fresh`.
HOME_VOLUME="life-agent-e2e-home$INSTANCE_SUFFIX"

# The pinned base, by digest, exactly as the Dockerfile names it. Kept here so
# up.sh can pull it before the build: ECR Public answers HEAD on a digest with
# 403 while answering GET normally, and BuildKit resolves FROM with HEAD.
BASE_IMAGE=public.ecr.aws/e1h7x4a2/plow-cloud-agents@sha256:84b46cbb9e7f6ea87825bb7a5e04d0071faa03c6e49e66e7b052dbaa0fdf3c1d

# The stack this loop talks to. It is brought up and owned elsewhere -- these
# scripts never start, stop or reconfigure it. The agent container reaches it
# by OrbStack's own DNS, which resolves inside a container on the default
# bridge and whose cert is trusted there, so no compose network is joined and
# no host port is written down (ports move per worktree; the names do not).
#
# Its checkout is the one path outside this repo any of this reads --
# run-agent.sh derives the twin's host port from that checkout's .plow-dev-env.
# The default is the conventional sibling layout next to this repo; PLOW_MAIN
# overrides it for a checkout that lives anywhere else, and is read from the
# environment or from .env (load_env re-resolves after sourcing it).
plow_repo() {
  printf '%s' "${PLOW_MAIN:-$(cd "$REPO_DIR/../.." && pwd)/plow/main}"
}
PLOW_REPO="$(plow_repo)"

# This instance's credentials: .env.<instance> if it exists, else the shared
# .env. The fallback is what keeps `default` working with the file everyone
# already has, and what lets a second cook activate their own chat without
# touching it.
ENV_FILE="$E2E_DIR/.env.$E2E_INSTANCE"
[ -f "$ENV_FILE" ] || ENV_FILE="$E2E_DIR/.env"

load_env() {
  if [ ! -f "$ENV_FILE" ]; then
    echo "${ENV_FILE#$E2E_DIR/} is missing -- run scripts/e2e/activate.sh first" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1091
  . "$ENV_FILE"
  set +a
  # Re-resolved: PLOW_REPO was set when this file was sourced, before .env had
  # been read, so a PLOW_MAIN that lives in .env would otherwise be ignored.
  PLOW_REPO="$(plow_repo)"
}

require() {
  for name in "$@"; do
    if [ -z "${!name:-}" ]; then
      echo "$name is empty in ${ENV_FILE#$E2E_DIR/} -- re-run activate.sh" >&2
      exit 1
    fi
  done
}

# The thread as a waiter needs to see it: "<outbound count> <typing 0|1>".
#
# Both halves in ONE request because turn.sh reads them together every couple of
# seconds, and two round trips against the same endpoint is twice the traffic
# for a less consistent answer -- the count and the indicator would be read at
# different instants and could disagree about the same moment.
#
# Typing is only ever the SECOND half of a done-talking test. The twin clears it
# on every outbound message it stores (`_add_outbound_message`,
# dtu/linq/linq_twin/store.py), so it is already false when the first of three
# messages lands. What it is good for is the opposite case: it stays true across
# a long tool call, which is exactly the silence a quiet window would otherwise
# read as the end of the turn.
chat_state() {
  python3 - "$TWIN_HOST_BASE" "$TWIN_THREAD" <<'STATE_PY'
import json, sys, urllib.request
twin, thread = sys.argv[1:3]
with urllib.request.urlopen(f"{twin}/ui/chats/{thread}") as resp:
    chat = json.load(resp)

# Hermes' own runtime notices are outbound messages too: the progress ticker,
# the redirect/interrupt notice, the one-time /busy tip. Counting them as
# replies makes a wait return while the turn is still running, and the next
# scripted send then REDIRECTS that turn instead of starting a new one -- the
# whole conversation collapses into a single turn that answers everything at the
# end and leaves a transcript with no conversation in it. Measured: five paced
# sends became one 425-second turn, 49 api calls, one summary message. The
# opening glyph is the only marker they carry.
#
# They are dropped here rather than in outbound_count so that turn.sh's quiet
# window does not see them either: a ticker arriving every few seconds would
# otherwise reset that window for as long as the turn ran, and a turn that
# emitted nothing but notices would look like a reply.
NOTICE = ("\u23f3", "\u21aa", "\U0001f4a1", "\u26a1")
replies = 0
for message in chat.get("messages", []):
    if message["direction"] != "outbound":
        continue
    text = "".join(part.get("value", "") for part in message.get("parts", [])
                   if part.get("type") == "text").lstrip()
    if text.startswith(NOTICE):
        continue
    replies += 1
print(replies, 1 if (chat.get("typing") or {}).get("is_typing") else 0)
STATE_PY
}

# How many messages the agent has sent in this thread. The baseline every wait
# is measured against, and the reason it lives here: send.sh has to read it
# BEFORE it posts and await-reply.sh has to compare against it after, so the
# two cannot each keep their own idea of what counting means.
outbound_count() {
  chat_state | cut -d" " -f1
}

# Where send.sh leaves the pre-send baseline for await-reply.sh to pick up.
BASELINE_FILE="$E2E_DIR/.pending-reply$INSTANCE_SUFFIX"

# The stub's runtime state and the lock, per instance for the same reason.
STUB_PID_FILE="$E2E_DIR/.latch-stub$INSTANCE_SUFFIX.pid"
STUB_PORT_FILE="$E2E_DIR/.latch-stub$INSTANCE_SUFFIX.port"
STUB_LOG_FILE="$E2E_DIR/.latch-stub$INSTANCE_SUFFIX.log"
LOCK_FILE="$E2E_DIR/.lock$INSTANCE_SUFFIX"
