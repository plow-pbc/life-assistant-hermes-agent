# Rowan's life Hermes agent — task runner.
#
# HERMES_UID/GID keep container-written files owned by you. Exported here rather
# than defaulted in compose.yml: s6 chowns /opt/data to them at boot, so a wrong
# value re-owns this agent's live state in place.
export HERMES_UID := `id -u`
export HERMES_GID := `id -g`

# Run the config-contract tests.
test:
    uv run --no-project --python 3.13 --with pytest==8.4.2 --with pyyaml==6.0.2 pytest -q

# Install the declarative half of ~/.hermes-rowan.
restore:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p ~/.hermes-rowan
    umask 077
    [ -f ~/.hermes-rowan/.env ] || install -m 600 .env.example ~/.hermes-rowan/.env
    # Compared before the copy, because after it they are always identical.
    # restore is the idempotent installer and the first line of the documented
    # bring-up — the recipe you re-run to be sure state is in place — so
    # bouncing a live gateway on a byte-identical copy drops an in-flight chat
    # turn for nothing. The staleness below only exists when the two differ.
    cmp -s runtime/config.yaml ~/.hermes-rowan/config.yaml && changed= || changed=1
    install -m 600 runtime/config.yaml ~/.hermes-rowan/config.yaml
    echo "restored config.yaml to ~/.hermes-rowan"
    # Only when it actually changed: restore is the idempotent installer and
    # the first bring-up step, so bouncing a live gateway on a byte-identical
    # copy drops an in-flight chat turn for nothing.
    [ -z "$changed" ] || scripts/reload-if-running "the config just installed"

# The Plow Chat plugin, from the pinned upstream SHA. Refuses a non-SHA ref: a
# branch would silently re-point a running agent on the next upstream push, and
# this plugin holds the chat token.
# Install the Plow Chat plugin from the pinned SHA.
install-plugin:
    #!/usr/bin/env bash
    set -euo pipefail
    ref="$(tr -d '[:space:]' < runtime/plow-chat-plugin.ref)"
    [[ "$ref" =~ ^[0-9a-f]{40}$ ]] || { echo "runtime/plow-chat-plugin.ref must be a 40-char SHA, got: $ref" >&2; exit 1; }
    tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
    curl -fsSL "https://raw.githubusercontent.com/plow-pbc/seed-hermes-plow/$ref/ref/scripts/install_direct_mount.sh" -o "$tmp"
    PLOW_CHAT_PLUGIN_REF="$ref" bash "$tmp" --data-dir "$HOME/.hermes-rowan"
    scripts/reload-if-running "the plugin"

# Gmail, Google Calendar and Slack, from the same pinned SHA as the plugin. The
# skill calls the Plow connector REST API with the gateway's existing
# PLOW_CHAT_TOKEN, so there is nothing here to log in to and no second
# credential to configure.
#
# The two files are fetched directly rather than by running upstream's
# install_connectors.sh: that script copies from a path inside its own checkout,
# so curling the script alone finds no source to copy.
#
# The destination is not a preference. SKILL.md's allowed-tools line names
# /opt/data/skills/plow-connectors/plow_connector.py literally, so a skill
# installed one directory deeper — the way the property agent nests its skill
# under skills/productivity/ — loads and is then refused permission to run its
# own helper.
# Install the Gmail/Calendar/Slack connector skill from the pinned SHA.
install-connectors:
    #!/usr/bin/env bash
    set -euo pipefail
    ref="$(tr -d '[:space:]' < runtime/plow-chat-plugin.ref)"
    [[ "$ref" =~ ^[0-9a-f]{40}$ ]] || { echo "runtime/plow-chat-plugin.ref must be a 40-char SHA, got: $ref" >&2; exit 1; }
    dest="$HOME/.hermes-rowan/skills/plow-connectors"
    base="https://raw.githubusercontent.com/plow-pbc/seed-hermes-plow/$ref/ref/hermes-skill/plow-connectors"
    # Into a temp dir, then moved into place. Fetching straight at the
    # destination truncates a running agent's skill before curl produces a byte,
    # so a deleted SHA would leave it empty — and the checks below would report
    # that, having already caused it.
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    curl -fsSL "$base/SKILL.md" -o "$tmp/SKILL.md"
    curl -fsSL "$base/plow_connector.py" -o "$tmp/plow_connector.py"
    grep -q '^name: plow-connectors$' "$tmp/SKILL.md" || { echo "fetched a file that is not the plow-connectors skill" >&2; exit 1; }
    [ -s "$tmp/plow_connector.py" ] || { echo "fetched an empty plow_connector.py from $ref" >&2; exit 1; }
    mkdir -p "$dest"
    install -m 644 "$tmp/SKILL.md" "$dest/SKILL.md"
    install -m 755 "$tmp/plow_connector.py" "$dest/plow_connector.py"
    printf 'installed plow-connectors at %s (from %s)\n' "$dest" "${ref:0:7}"
    scripts/reload-if-running "the connector skill"

# Activate this agent's Plow number. Prints a code; ROWAN texts it from his
# phone, and the script polls until Plow confirms.
#
# POST /v1/auth/activate carries no credential — the account binding is
# whichever phone texts the code back. So this must be texted by Rowan, from his
# handset: the token it mints is the one the connector skill then uses to read
# mail, and a code texted by anyone else binds the agent to the wrong account.
#
# --data-dir is the ONLY thing deciding which agent this rewrites, and it
# rewrites in place: upstream's script does not honour HERMES_DOTENV, so
# exporting it changes nothing. Pointed at another agent's home it would replace
# that agent's PLOW_CHAT_CHAT_UID and PLOW_CHAT_TOKEN — taking it off its chat
# until /sethome is re-sent, and spending a one-time activation to do it.
# Hard-wired here, and the guard below is why: running the script by hand is the
# realistic way that happens.
# Activate this agent's Plow number (Rowan texts the code).
activate:
    #!/usr/bin/env bash
    set -euo pipefail
    ref="$(tr -d '[:space:]' < runtime/plow-chat-plugin.ref)"
    [[ "$ref" =~ ^[0-9a-f]{40}$ ]] || { echo "runtime/plow-chat-plugin.ref must be a 40-char SHA, got: $ref" >&2; exit 1; }
    target="$HOME/.hermes-rowan"
    # Refuse to activate into a home that is not this agent's, however this
    # recipe came to be edited or copied.
    case "$target" in
      */.hermes-rowan) ;;
      *) echo "activate refuses to target $target — that is another agent's home" >&2; exit 1 ;;
    esac
    [ -d "$target" ] || { echo "no $target — run \`just restore\` first" >&2; exit 1; }
    tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
    curl -fsSL "https://raw.githubusercontent.com/plow-pbc/seed-hermes-plow/$ref/ref/scripts/create_plow_chat_curl.sh" -o "$tmp"
    bash "$tmp" --data-dir "$target"
    # The gateway reads its dotenv at boot, and the script above just replaced
    # PLOW_CHAT_CHAT_UID and PLOW_CHAT_TOKEN inside it. Same invariant sign-in
    # restarts for. On the documented first run nothing is up and this is a
    # no-op; on a re-activation it is the difference between the gateway holding
    # the token this recipe minted and the one it replaced — while the recipe
    # prints success either way.
    # Non-fatal, and by this line the one-time activation is already spent:
    # a red exit would read as "activation failed" and invite a re-run that
    # costs a second activation. The script owns that behaviour.
    scripts/reload-if-running "the credential just written"

# A separate Codex sign-in for this agent, not a copy of another agent's
# auth.json — that file is guarded by auth.lock, and this one is a different
# person's account besides. It is a device-code flow, so the operator can run
# this recipe and hand Rowan the URL and code to complete in his own browser; he
# never needs a shell on this host.
#
# `exec`, not `run`. The image's s6 entrypoint starts a gateway whatever command
# you pass it, so `docker compose run` brings up a SECOND gateway against this
# same /opt/data — and once a chat is activated both would connect to it and
# answer every message, so every text would get two replies.
#
# --user is not optional. `exec` skips the entrypoint that remaps the in-image
# `hermes` user to HERMES_UID/GID, so a plain exec runs as ROOT and would write
# auth.json root-owned into a data directory the gateway (unprivileged, remapped)
# then cannot rewrite.
#
# The gateway reads its credential at boot, so it is restarted afterwards —
# without that, sign-in reports success while the gateway carries on
# unauthenticated, which fails silently in the worst way.
# One-time browser OAuth for this agent.
sign-in:
    #!/usr/bin/env bash
    set -euo pipefail
    docker compose ps --status running --quiet hermes | grep -q . \
      || { echo "the gateway is not running — start it first: just up" >&2; exit 1; }
    # Read out of the config the GATEWAY loads, not the repo's copy. This used to
    # be a second copy of model.provider written here, and a test asserted the
    # two agreed — but the test matched the recipe's text, and every way of
    # matching text is either too loose (provider "openai" is a substring of
    # "openai-codex") or too tight. The extraction lives in a script so the
    # contract test can run this exact command.
    #
    # ~/.hermes-rowan/config.yaml, NOT runtime/config.yaml: `restore` installs
    # the repo's copy there in a separate, manually-invoked step, and the
    # gateway resolved model.provider from the installed one at boot. Reading
    # the repo copy here would mint a credential for a provider the running
    # gateway is not using the moment the two differ — which is the same
    # authenticated-as-one-named-another failure this was changed to remove,
    # just moved one file over.
    installed=~/.hermes-rowan/config.yaml
    [ -f "$installed" ] || { echo "no $installed — run \`just restore\` first" >&2; exit 1; }
    provider="$(scripts/model-provider "$installed")"
    docker compose exec --user "$HERMES_UID:$HERMES_GID" hermes hermes auth add "$provider"
    scripts/reload-if-running "the credential just written"

# Start the gateway, then say which of Rowan's connectors it can actually reach.
up:
    #!/usr/bin/env bash
    set -euo pipefail
    docker compose up -d
    # Neutral on purpose: check-connectors distinguishes a linked connector, an
    # unlinked one, and a probe that could not run, and this only sees non-zero.
    just check-connectors || echo "gateway is up, but the connector check did not pass — see above" >&2

# Which connectors are linked, asked from INSIDE the container, because the
# container is what has to reach api.plow.co. Egress, DNS and CA config all
# differ between this shell and that network namespace, and every one of those
# failures is invisible to a host-side probe. There is deliberately no fallback
# to the host: a host answer is exactly the evidence entering the namespace was
# meant to stop accepting.
#
# `connected:false` is a real answer, not a failure — it means Rowan has not
# linked that connector to his Plow account yet.
# Report which of Rowan's connectors are linked and reachable.
check-connectors:
    #!/usr/bin/env bash
    set -euo pipefail
    docker compose ps --status running --quiet hermes | grep -q . \
      || { echo "the gateway is not running — start it first: just up" >&2; exit 1; }
    rc=0
    for c in gmail slack; do
      # `set -a; . /opt/data/.env` because the gateway loads that dotenv into its
      # own process and the skill inherits it — but a `docker compose exec` does
      # not, so without this the probe reported "token required" against a
      # perfectly good token. A check that can only ever fail is worse than none.
      if out="$(docker compose exec -T --user "$HERMES_UID:$HERMES_GID" hermes \
          sh -c 'set -a; . /opt/data/.env; set +a; exec python3 /opt/data/skills/plow-connectors/plow_connector.py "$1" status' _ "$c" 2>&1)"; then
        printf '%s: %s\n' "$c" "$out"
      else
        printf '%s: probe did not run — %s\n' "$c" "$(printf '%s' "$out" | tr '\n' ' ')" >&2
        rc=1
      fi
    done
    exit "$rc"

# Reload the gateway. It reads auth.json and .env at boot, so anything that
# rewrites a credential needs this — and it exists as a recipe because
# `docker compose restart` by hand fails on compose.yml's HERMES_UID/GID guards
# unless they are exported, which is what activate's failure message points at.
# Restart the gateway so it re-reads its credentials.
restart:
    docker compose restart hermes

# Does Rowan's Latch credential actually work? Asked from INSIDE the container,
# because the container is what has to reach the relay — egress, DNS and CA
# config all differ between this shell and that network namespace, and every one
# of those failures is invisible to a host-side probe.
#
# The Accept header is not optional. Plow's relay speaks MCP streamable-HTTP and
# answers 406 "Client must accept both application/json and text/event-stream"
# without it — which reads as a broken credential when the credential is fine.
# Measured: the same probe returns 200 with it and 406 without.
#
# A 401 means the token is REVOKED, not missing. Say which, so the fix is
# minting a fresh one from Rowan's Mac rather than hunting for a key.
# Ask the relay whether this agent can reach Rowan's Mac.
check-latch:
    #!/usr/bin/env bash
    set -euo pipefail
    docker compose ps --status running --quiet hermes | grep -q . \
      || { echo "the gateway is not running — start it first: just up" >&2; exit 1; }
    err="$(mktemp)"; trap 'rm -f "$err"' EXIT
    out="$(docker compose exec -T --user "$HERMES_UID:$HERMES_GID" hermes sh -c '
      set -a; . /opt/data/.env; set +a
      [ -n "${DOMO_DEVICE_UID:-}" ] || { echo "DOMO_DEVICE_UID is empty in the dotenv" >&2; exit 2; }
      [ -n "${DOMO_MCP_TOKEN:-}" ] || { echo "DOMO_MCP_TOKEN is empty in the dotenv" >&2; exit 2; }
      curl -sS --max-time 30 -o /dev/null -w "%{http_code}" \
        -X POST "https://api.plow.co/v1/relay/devices/$DOMO_DEVICE_UID/mcp" \
        -H "Authorization: Bearer $DOMO_MCP_TOKEN" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\",\"params\":{}}"
    ' 2>"$err")" || true
    # Read what the probe PRINTED, not what it exited with: curl that ran and got
    # no answer prints 000; curl that got an answer prints the status; a probe
    # that never ran prints nothing at all. Output alone separates those three.
    case "$out" in
      [0-9][0-9][0-9]) code="$out" ;;
      *) echo "the probe did not run in the container: $(tr '\n' ' ' < "$err")" >&2; exit 1 ;;
    esac
    case "$code" in
      200) echo "latch reachable from the container (HTTP 200)" ;;
      401) echo "DOMO_MCP_TOKEN is REVOKED — mint a fresh agent credential from Rowan's Mac" >&2; exit 1 ;;
      406) echo "relay refused the Accept header — this recipe sends it, so the probe was edited" >&2; exit 1 ;;
      000) echo "no answer from api.plow.co, asked from the container — the credential was NOT tested: $(tr '\n' ' ' < "$err")" >&2; exit 1 ;;
      *)   echo "relay returned HTTP $code from the container: $(tr '\n' ' ' < "$err")" >&2; exit 1 ;;
    esac

# Follow the gateway's logs.
logs:
    docker compose logs -f --tail 100

# Stop the gateway. Leaves ~/.hermes-rowan untouched.
down:
    docker compose down

# How to test this agent without going through the phone.
#
# `exec`, not `run`, and `--user`, for the reasons on sign-in above: `run`
# starts a rival gateway, and a plain `exec` runs as root and leaves root-owned
# files in a directory the gateway must be able to write.
# Run one agent turn in the running container.
agent PROMPT:
    #!/usr/bin/env bash
    set -euo pipefail
    docker compose ps --status running --quiet hermes | grep -q . \
      || { echo "the gateway is not running — start it first: just up" >&2; exit 1; }
    docker compose exec -T --user "$HERMES_UID:$HERMES_GID" hermes hermes chat -q {{quote(PROMPT)}}
