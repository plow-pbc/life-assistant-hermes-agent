# Shared settings for the local e2e loop. Sourced, never executed.
set -euo pipefail

E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$E2E_DIR/../.." && pwd)"

# The agent's image, container and the staging tree its skills are mounted
# from. One name each -- a second copy of any of them is how two loops end up
# fighting over one container.
IMAGE=life-agent:e2e
CONTAINER=life-agent-e2e
STAGING="$E2E_DIR/staging"

# A named volume over HERMES_HOME, so state outlives a restart -- which is what
# makes resume testable: onboarding's record of progress is ld/config.json, and
# with an ephemeral home every restart is a fresh owner. It is a NAMED volume,
# not a host bind: docker seeds an empty named volume from the image's own
# contents on first use, so config.yaml, the plugins and the skills skeleton
# survive being mounted over. A host bind would hide them and the gateway would
# not boot. The cost is that the seeding happens once -- an image rebuild that
# changes anything under the home needs `run-agent.sh --fresh`.
HOME_VOLUME=life-agent-e2e-home

# The pinned base, by digest, exactly as the Dockerfile names it. Kept here so
# up.sh can pull it before the build: ECR Public answers HEAD on a digest with
# 403 while answering GET normally, and BuildKit resolves FROM with HEAD.
BASE_IMAGE=public.ecr.aws/e1h7x4a2/plow-cloud-agents@sha256:84b46cbb9e7f6ea87825bb7a5e04d0071faa03c6e49e66e7b052dbaa0fdf3c1d

# The stack this loop talks to. It is brought up and owned elsewhere -- these
# scripts never start, stop or reconfigure it. The agent container reaches it
# by OrbStack's own DNS, which resolves inside a container on the default
# bridge and whose cert is trusted there, so no compose network is joined and
# no host port is written down (ports move per worktree; the names do not).
PLOW_REPO=/Users/plucas/plow-pbc/plow/main

load_env() {
  if [ ! -f "$E2E_DIR/.env" ]; then
    echo "scripts/e2e/.env is missing -- run scripts/e2e/activate.sh first" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1091
  . "$E2E_DIR/.env"
  set +a
}

require() {
  for name in "$@"; do
    if [ -z "${!name:-}" ]; then
      echo "$name is empty in scripts/e2e/.env -- re-run activate.sh" >&2
      exit 1
    fi
  done
}

# How many messages the agent has sent in this thread. The baseline every wait
# is measured against, and the reason it lives here: send.sh has to read it
# BEFORE it posts and await-reply.sh has to compare against it after, so the
# two cannot each keep their own idea of what counting means.
outbound_count() {
  python3 - "$TWIN_HOST_BASE" "$TWIN_THREAD" <<'COUNT_PY'
import json, sys, urllib.request
twin, thread = sys.argv[1:3]
with urllib.request.urlopen(f"{twin}/ui/chats/{thread}") as resp:
    chat = json.load(resp)
print(sum(1 for m in chat.get("messages", []) if m["direction"] == "outbound"))
COUNT_PY
}

# Where send.sh leaves the pre-send baseline for await-reply.sh to pick up.
BASELINE_FILE="$E2E_DIR/.pending-reply"
