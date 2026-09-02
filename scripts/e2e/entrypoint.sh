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

: "${PLOW_API_BASE:?}" "${PLOW_AGENT_TOKEN:?}" "${PLOW_HOME_CHANNEL:?}" "${TWIN_UPLOAD_PORT:?}"

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
# This runs on EVERY start, and it removes the server as readily as it writes
# it. config.yaml lives on the home volume and outlives the container, so a
# block written by one `run-agent.sh --latch` would otherwise still be there
# for every plain `run-agent.sh` after it -- the relay opted into once and
# reachable forever, which is the opposite of what opt-in means when the device
# on the other end is a person's actual Mac. Absent credentials must therefore
# mean "take it out", never "leave whatever is already there".
#
# The server key is NOT the harness's to choose. It is also the prefix on the
# tool a model calls -- key `latch` means `mcp__latch__plow_run_command` -- so
# the key belongs to whoever writes the sheets and the seed, and a loop that
# hardcodes it silently tests a spelling nobody shipped. This block reads the
# key the seeded config.yaml already carries, and fills in nothing but url,
# headers and enabled under it.
#
# Order: the seeded key if there is exactly one, else $E2E_RELAY_KEY, else the
# base seed's own name. The env var is the escape hatch for a volume whose
# relay was removed by a previous no-credentials run, which leaves no key to
# read; it is not a way to rename the relay, and the check below is what stops
# it becoming one.
#
# The tool name stays `plow_run_command` under every key -- the two are not the
# same string and an earlier version of this comment had them confused.
#
# The URL and the bearer come from the environment so the credential is never
# written into a repo file.
#
# Written into the config rather than passed as env because Hermes reads its
# MCP servers from config.yaml; the file is the agent's own (first-boot hands
# it to uid 10000) so this runs before the chown below.
# The venv's python, not /usr/bin/python3: pyyaml is installed in Hermes' own
# environment and nowhere else, so the system interpreter dies here with
# ModuleNotFoundError -- and because this block now runs on EVERY start, that
# took down plain no-Latch runs too, before the gateway ever started.
/opt/hermes/.venv/bin/python3 - <<'LATCH_PY'
import os
import re

import yaml

path = "/var/lib/hermes/config.yaml"
skills_dir = "/var/lib/hermes/skills"
url = (os.environ.get("LATCH_MCP_URL") or "").strip()
token = (os.environ.get("LATCH_MCP_TOKEN") or "").strip()

# What the base seed writes today. Only reached when the config carries no
# relay at all and nobody named one.
SEED_RELAY_KEY = "latch"

# No existence check: `chown hermes:hermes /var/lib/hermes/config.yaml` above
# runs under `set -e`, so a container missing this file has already exited
# before anything here runs.
with open(path) as f:
    config = yaml.safe_load(f) or {}

# The e2e image's config.yaml comes off the home VOLUME, which is seeded once
# and then outlives every rebuild -- so a key added to runtime/config.yaml does
# not reach an existing volume. Set here too, or the loop tests an agent that
# still has the tool the product has taken away.
config.setdefault("agent", {})["disabled_toolsets"] = ["clarify"]
config.setdefault("display", {})["file_mutation_verifier"] = False

servers = config.setdefault("mcp_servers", {})

# The seed's key, read rather than assumed. Exactly one server is the shape
# this repo ships (test_config_contract asserts it), so one key is the relay's;
# with none or several, nothing here can tell which, and the fallbacks decide.
seeded = list(servers)
if len(seeded) == 1:
    relay_key, key_source = seeded[0], "the seeded config.yaml"
elif (os.environ.get("E2E_RELAY_KEY") or "").strip():
    relay_key, key_source = os.environ["E2E_RELAY_KEY"].strip(), "$E2E_RELAY_KEY"
else:
    relay_key, key_source = SEED_RELAY_KEY, "the base seed's default"

# The prefix the SHEETS call, which is the half of the pair the harness cannot
# see and used to contradict in silence. A disagreement here is not cosmetic:
# the build registers mcp__<relay_key>__plow_run_command and the turn goes
# hunting for a tool under a name that was never registered -- twenty-one
# tool_search calls and an answer of nothing, on the run that found it.
prefixes = set()
for root, _dirs, files in os.walk(skills_dir):
    for name in files:
        if not name.endswith((".md", ".py")):
            continue
        with open(os.path.join(root, name), errors="replace") as handle:
            prefixes.update(re.findall(r"mcp__([A-Za-z0-9_]+?)__", handle.read()))

print(f"e2e-entrypoint: relay key {relay_key!r} (from {key_source}); "
      f"skills call {sorted(prefixes) or ['no mcp__ tools']}")
if prefixes and prefixes != {relay_key}:
    raise SystemExit(
        f"e2e-entrypoint: relay key {relay_key!r} does not match the tool "
        f"prefix the staged skills call ({', '.join(sorted(prefixes))}). "
        "The key IS the prefix, so this build would register a tool no sheet "
        "names. Fix the sheets or the seed -- or set $E2E_RELAY_KEY if the "
        "volume simply has no relay to read.")

if url and token:
    # Only url/headers/enabled. The key came from the seed; this fills it in.
    servers[relay_key] = {
        "url": url,
        "headers": {"Authorization": "Bearer " + token},
        "enabled": True,
    }
    print(f"e2e-entrypoint: wired the {relay_key} relay MCP server into config.yaml")
else:
    # Every known spelling, popped unconditionally. `pop(a) is not None or
    # pop(b) is not None` short-circuits: with a volume carrying BOTH the
    # second pop never runs and a relay pointing at a real Mac stays in the
    # config on a run that asked for no relay at all.
    stale = [relay_key, SEED_RELAY_KEY, "plow"]
    removed = [key for key in dict.fromkeys(stale)
               if servers.pop(key, None) is not None]
    if removed:
        print("e2e-entrypoint: no Latch credentials -- removed the relay MCP "
              f"server ({', '.join(removed)}) an earlier --latch run left behind")
    else:
        print("e2e-entrypoint: no Latch relay (config.yaml names no relay MCP server)")

print("e2e-entrypoint: clarify disabled; file-mutation verifier footer off")

with open(path, "w") as f:
    yaml.safe_dump(config, f, sort_keys=False)
LATCH_PY

# The twin's upload origin, forwarded before the gateway starts so the first
# attachment does not race it. Root here, and it stays root: it binds a port
# and touches nothing else.
#
# Unconditional. run-agent.sh always sets TWIN_UPLOAD_PORT and exits if it
# cannot derive it, so the old `if` never once decided anything -- it only meant
# that a container started without the port would come up looking healthy and
# fail on the first attachment, with nothing said at boot. TWIN_UPLOAD_PORT is
# a precondition now, checked with the others at the top.
/usr/bin/python3 /usr/local/bin/e2e-upload-shim &

cd /opt/hermes

# setpriv keeps the caller's environment, so without this the agent runs with
# root's HOME. A turn resolves its context directory from HOME and walks up
# looking for a .git; from /root that stat is denied to uid 10000 and EVERY
# turn dies with "Sorry, I encountered an unexpected error." The account's own
# passwd home is /opt/data -- the fleet's HERMES_HOME, which does not exist in
# this image -- so name the home this image actually has.
HOME=/var/lib/hermes
export HOME

# The base image ships HERMES_HOME=/opt/data AND HERMES_WRITE_SAFE_ROOT=/opt/data,
# the fleet's paths. The cloud runtime overrides the first (systemd unit, and us)
# and NOT the second -- so write_file refuses every path in the agent's own home
# with "outside HERMES_WRITE_SAFE_ROOT (/opt/data)". Onboarding only survives it
# because write_config.py runs through the terminal instead. Corrected here so
# the loop tests the agent rather than that bug; the image itself still has it.
HERMES_WRITE_SAFE_ROOT=/var/lib/hermes
export HERMES_WRITE_SAFE_ROOT

exec setpriv --reuid=10000 --regid=10000 --init-groups \
  /opt/hermes/.venv/bin/hermes gateway run --replace --no-supervise
