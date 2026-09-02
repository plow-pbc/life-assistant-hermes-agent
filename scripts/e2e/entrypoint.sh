#!/bin/sh
# Stand in for the VM's systemd boot: write the tenant dotenv, restore the
# ownership first-boot.sh normally sets, then run the gateway as the agent.
#
# The image is built to be booted by systemd (agent-setup.service writes the
# dotenv, hermes-gateway.service runs the gateway as `hermes`). A local
# container has no init, so this reproduces only the two things the gateway
# actually depends on -- and does it in the same order, because the gateway
# unit's ConditionPathExists is the dotenv.
set -eu

: "${PLOW_API_BASE:?}" "${PLOW_AGENT_TOKEN:?}" "${PLOW_HOME_CHANNEL:?}"

cat > /var/lib/hermes/.env <<ENV
PLOW_API_BASE=${PLOW_API_BASE}
PLOW_AGENT_TOKEN=${PLOW_AGENT_TOKEN}
PLOW_HOME_CHANNEL=${PLOW_HOME_CHANNEL}
HERMES_CUSTOM_PLOW_API_KEY=${HERMES_CUSTOM_PLOW_API_KEY}
TZ=${TZ}
ENV

# first-boot.sh's ownership rules, and for its reasons: root-owned 0640 so the
# agent cannot rewrite its own API base, group-readable so register_crons.py
# can still read the dotenv directly; config.yaml owned by the agent because
# the plow_chat plugin rewrites it through Hermes' config writer, and the
# home's sticky bit refuses a rename-over by a non-owner.
chown root:hermes /var/lib/hermes/.env
chmod 0640 /var/lib/hermes/.env
chown hermes:hermes /var/lib/hermes/config.yaml
chown root:hermes /var/lib/hermes
chmod 3770 /var/lib/hermes

# The gateway unit's WorkingDirectory, and not cosmetic: a turn builds its
# system prompt from the CWD, and from the image's own /root it walks up
# looking for a .git and dies on `PermissionError: '/root/.git'` -- uid 10000
# cannot stat it. Every turn fails with "an unexpected error" until this is set.
# A real Latch relay, wired in only when the loop was given one.
#
# The server is named `plow`, NOT `latch`. Tool names are derived from the
# server name (mcp__plow__plow_run_command), and every ld-* SKILL.md calls
# `plow_run_command` / `plow_write_file` by those names -- a server called
# `latch` here would expose mcp__latch__* and the skills would be naming tools
# that do not exist. `plow` is also what the cloud image's own seed config
# calls it; runtime/config.yaml's `latch` block is the fleet's, and is dead on
# this image.
#
# The URL and the bearer come from the environment so the credential is never
# written into a repo file.
#
# Written into the config rather than passed as env because Hermes reads its
# MCP servers from config.yaml; the file is the agent's own (first-boot hands
# it to uid 10000) so this runs before the chown below.
if [ -n "${LATCH_MCP_URL:-}" ] && [ -n "${LATCH_MCP_TOKEN:-}" ]; then
  /usr/bin/python3 - <<'LATCH_PY'
import os
import yaml
path = "/var/lib/hermes/config.yaml"
with open(path) as f:
    config = yaml.safe_load(f) or {}
config.setdefault("mcp_servers", {})["plow"] = {
    "url": os.environ["LATCH_MCP_URL"],
    "headers": {"Authorization": "Bearer " + os.environ["LATCH_MCP_TOKEN"]},
    "enabled": True,
}
with open(path, "w") as f:
    yaml.safe_dump(config, f, sort_keys=False)
print("e2e-entrypoint: wired the plow (Latch relay) MCP server into config.yaml")
LATCH_PY
fi

# The twin's upload origin, forwarded before the gateway starts so the first
# attachment does not race it. Root here, and it stays root: it binds a port
# and touches nothing else.
if [ -n "${TWIN_UPLOAD_PORT:-}" ]; then
  /usr/bin/python3 /usr/local/bin/e2e-upload-shim &
fi

cd /opt/hermes

# setpriv keeps the caller's environment, so without this the agent runs with
# root's HOME. A turn resolves its context directory from HOME and walks up
# looking for a .git; from /root that stat is denied to uid 10000 and EVERY
# turn dies with "Sorry, I encountered an unexpected error." The account's own
# passwd home is /opt/data -- the fleet's HERMES_HOME, which does not exist in
# this image -- so name the home this image actually has.
HOME=/var/lib/hermes
export HOME

exec setpriv --reuid=10000 --regid=10000 --init-groups \
  /opt/hermes/.venv/bin/hermes gateway run --replace --no-supervise
