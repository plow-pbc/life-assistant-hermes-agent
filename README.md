# life-assistant

> [!IMPORTANT]
> **This repo is code only.** The instances it configures hold a person's Plow
> credential and reach their calendar through their own Mac (see [No connectors,
> and what that costs](#no-connectors-and-what-that-costs) for what they no
> longer reach) — all of that lives in the instance's home on the host, never
> here. Keep it that way:
> nothing under this tree may carry a credential, a chat id, or a person's data.

A [Hermes](https://howto.plow.co/hermes) agent — texted from iMessage over the
Plow Chat platform — scoped to life and family logistics. Siblings on `wakeup`
are the rentals agent (`plow-pbc/str-hermes-agent`) and the house-hunting agent
(`plow-pbc/property-hunt-hermes-agent`).

**One repo, one instance per person.** This is not one person's agent that
someone else could copy; it is the common half, and each person who runs it gets
an instance.

| instance | registry name | home | container | may be registered? |
|---|---|---|---|---|
| the operator's | `life` | `~/.hermes-life` | `hermes-life` | yes — command below |
| Rowan's | `rowan` | `~/.hermes-rowan` | `hermes-rowan` | **no** — see [Migrating `rowan`](#migrating-rowan) |

None of it is declared in this repo. For a *registered* instance `agent-mgr`
derives home and container from the **registry name**, so any number of them run
from one checkout with no per-person fork — two rows against the same directory
resolve to separate homes and containers.

**Neither row is state.** Both are what `agent-mgr` *would* derive from the
registry name; this table says what each instance is permitted, not what the
registry holds, because nothing in this tree can check that. `rowan`'s values
happen to be live today — supplied by its own pre-agent-mgr compose file in
another repo, not by anything here.

```sh
agent-mgr register life ~/services/life-assistant-hermes-agent
# agent-mgr register rowan ~/services/life-assistant-hermes-agent   # same directory
#   DO NOT RUN YET -- see README "Migrating rowan" for the preconditions.
```

That is why `agent.env` declares no `AGENT_HOME`, `AGENT_CONTAINER` or
`AGENT_PROJECT`. It is not an omission — a descriptor that named a home could
only ever name *one*, and `agent-mgr`'s ownership guard refuses a home that does
not match the instance's own name, so a repo that declared one could not be
shared at all. Silence is what makes this work.

`agent.env` is a **closed set** holding only `AGENT_CONFIG`;
`tests/test_config_contract.py` fails on any other key, of any kind. Every
instance reads this one file, so a new key is given to all of them — adding one
is a deliberate edit to `DESCRIPTOR_KEYS`, not something a comment can justify.

It runs the upstream `nousresearch/hermes-agent` image directly, pinned by
digest, with no derived layer. State lives in the instance's own home on the
host, mounted at `/opt/data`; the image is stateless.

## Migrating `rowan`

`rowan` is the second instance and it is **not registered**. Everything blocking
it lives here — the table, the register block, the bring-up line and the
`justfile` all point at this section rather than carrying their own copy, so
there is one place to correct when it changes.

**Precondition 1 — the timezone.** `agent.env` is a closed set (above) and could
not carry `AGENT_TZ` even if it were open: a shared descriptor holds one value
and every instance would read it. Instances therefore inherit `agent-mgr`'s fleet
default, `America/Los_Angeles`. `rowan` needs `America/Chicago`, so it cannot be
registered until [`plow-pbc/agent-mgr#14`](https://github.com/plow-pbc/agent-mgr/issues/14)
adds a per-instance overlay. This is not cosmetic — a life assistant resolves
"tomorrow morning" and every scheduled thing against its clock, so a two-hour
offset is wrong in exactly the place the agent exists for.

**Precondition 2 — the home is already occupied, and nothing would stop you.**
`rowan`'s live pre-agent-mgr stack owns `~/.hermes-rowan` right now, serving it
from its own compose file in another repo — and it is not registered, which is
precisely the gap described in
[What an instance cannot reach](#what-an-instance-cannot-reach). Registering and
restoring while that stack runs is that case. Precondition 1 does not
imply this one: #14 landing makes it *more* reachable, not less.

**The order, therefore:**

1. `agent-mgr#14` lands
2. write `~/.config/agent-mgr/rowan.env` with `AGENT_TZ=America/Chicago`
3. **bring the pre-agent-mgr stack down** and confirm `~/.hermes-rowan` is unowned
4. `agent-mgr register rowan`, then the ordinary [Bring-up](#bring-up) — a
   pointer rather than a copy, deliberately: this section's whole premise is
   that everything points here instead of carrying its own sequence, and the
   abbreviated `restore` → `up` chain that used to sit on this line was already
   missing the `mkdir` that keeps `skills/` and `ld/` from landing root-owned.

Step 1 retires precondition 1. Step 3 is the one that outlives it, so this
section stays until `rowan` is actually migrated — not until #14 closes.

## The account boundary — how one repo serves two people

`POST /v1/auth/activate` carries **no credential**. Its payload is
`{name, provision_chat}` and nothing else, so the account binding is decided
entirely by *which phone texts the code back*. That single fact is what lets the
tracked tree stay identical for everyone:

- The `PLOW_CHAT_TOKEN` that lands in the instance's `.env` belongs to whoever
  texted.
- The Plow Chat line is theirs, so the agent texts and is texted by them and
  nobody else. (It used to reach their Gmail, Calendar and Slack too, through
  the `plow-connectors` skill — see [No connectors, and what that
  costs](#no-connectors-and-what-that-costs).)
- Their phone line does not draw on anyone else's pool. Plow's five service-wide
  numbers collide on **(line, participant set)**, and a different handset is a
  different set — so activating one instance spends nothing the others use.

Nothing is pre-staged on this host for anyone. There is no credential to hand
over before bring-up; the activation exchange mints it.

Latch is bound the same way: `runtime/config.yaml` reaches the relay through
`${DOMO_DEVICE_UID}` / `${DOMO_MCP_TOKEN}`, read from the instance's own dotenv.
The config naming those variables is shared; the Mac they resolve to is not.

## What an instance cannot reach

Here a copy-paste can cross an **account** boundary, not just an agent one. Two
of these are asserted by `tests/test_config_contract.py` -- the `mcp_servers`
allowlist and the interpolation contract -- and are marked below. The mount-level
ones left with the deployment and are `plow-pbc/agent-mgr`'s compose contract
now; they are stated here as design, not as something this tree enforces:

- **Another instance's state.** Two gateways sharing one home share one
  `auth.json` and one dotenv, including one `PLOW_CHAT_CHAT_UID`, so whichever
  started last owns the chat. `agent-mgr`'s collision check refuses two
  *registered* agents resolving to the same home — but only registered ones: a
  container running outside the registry holding that home is invisible to it,
  and nothing here would stop a second gateway opening alongside it.
- **The operations vault** (`~/hermes-vault`) *(agent-mgr's compose contract)* — compiled guest conversations and
  property access facts, door and keypad codes among them.
- **Hostex and Seam** *(agent-mgr's compose contract)*. No PMS access, no lock control — those belong to the
  rentals agent and reach a different person's property.
- **Anyone else's Mac** *(asserted here)*. `tests/` asserts `latch` is the only `mcp_servers`
  entry, so a sibling's block arriving by copy-paste fails loudly. Which Mac
  `latch` reaches is the dotenv's business, not this tree's.

One mount here against the rentals agent's six. That asymmetry is the design,
not an omission to be tidied up later.

## Bring-up

`<agent>` is the registry name. `life` is the only instance that *may* be
registered today; see [Migrating `rowan`](#migrating-rowan) for the other.

Land `/opt/data/ld/config.json` before you start, not after: the last step below
registers the crons, and `register_crons.py` reads `family.timezone` from that
file and refuses without it — and `compose.override.yml` binds the file
read-only over itself, so it must exist on the host before `agent-mgr up`
(Docker would otherwise create a root-owned directory in its place) and the
agent can read but never rewrite it; edits happen host-side, then `up`. The
producers read their location and teams from it too, as the agent. It goes at
`~/.hermes-<agent>/ld/config.json` on the host, landed as the instance owner
rather than through a root `exec`, and the live one is mode-600. The dotenv needs `DASHBOARD_ENDPOINT_URL` and `DASHBOARD_TOKEN`
alongside it. The `ld-viewer-dev` skill needs three more host-side files in
the instance home, all landed as the instance owner: `ld-dev/repo-url` (one
line, the household repo's HTTPS URL), `ld-dev/git-credentials` (mode 600,
one `https://x-access-token:<PAT>@github.com` line — land it via a
`read -rs`-style recipe so the token never touches a shell history or
transcript), and `ld-dev/ssh/pi_key` (mode 600 — OpenSSH refuses a
group/world-readable private key; the kiosk-diagnostics SSH key, its `.pub`
authorized on the Pi). `ld-shared/scripts/ld_config_gate.py` is the single definition of
a valid config — run it rather than eyeballing the JSON. Its `family.timezone`
must match the container's `AGENT_TZ`, because `hermes cron create` takes no
per-job zone and every producer fires in the container's; you do not have to
check that by eye, though — `register_crons.py` refuses to register at all when
they differ, so a mismatch stops bring-up rather than reaching the wall two
hours late.

```sh
agent-mgr restore <agent>            # config.yaml and the plugin into its home
agent-mgr activate <agent>           # prints a code — its owner texts it from their phone

# BEFORE `up`, and not optional. Both directories have to be owned by the
# instance owner, for different reasons. `skills/`: compose.override.yml mounts
# each skill UNDER /opt/data, which is already the home bind, so the runtime
# creates the missing mountpoint inside that bind's source on the host -- as
# root, and no later `restore` can install into it. plow-pbc/agent-mgr#44 is the
# fleet-level fix; until it lands this line stands in for it. `ld/`: nothing
# mounts there, so it is instead whoever first lands config.json in it who owns
# it -- and `agent-mgr compose ... exec` is root (see below). The agent writes
# each composed tile to /opt/data/ld/<bundle>-text with its file tool for the
# wrapper to read back, so a root-owned `ld/` costs every card and does it
# quietly: a blocked file tool is the kind of thing an agent improvises around,
# and intermittent posting looks fine from the kiosk. Nothing downstream
# re-checks either one: `register_crons.py` refuses on a config it cannot read,
# but an `ld/` the agent can read and not write passes bring-up clean and
# surfaces later as a card that stopped updating. This line is the only defence.
# Two paths, not `{skills,ld}` -- brace expansion is bash/zsh, and under the
# host's `sh` that collapses to one literal directory and exits 0.
mkdir -p ~/.hermes-<agent>/skills ~/.hermes-<agent>/ld

# ALSO before `up`: the ld-config must be a real FILE at this path.
# compose.override.yml binds it read-only over /opt/data/ld/config.json, and
# Docker materializes a missing bind source as a root-owned DIRECTORY at both
# ends -- bring-up then fails on a config nothing can read or replace. The
# guard keeps `up` from creating that directory (an `exit` would close the
# shell this block is pasted into); the later steps still run and fail
# loudly against the missing container, which needs no fence.
if [ -f ~/.hermes-<agent>/ld/config.json ]; then
  agent-mgr up <agent>               # must precede sign-in: that runs inside this container
else
  echo 'STOP: land ld/config.json first (see above)'
fi
agent-mgr sign-in <agent>            # one-time Codex device flow — its owner completes it
just check-latch <agent>             # can this container reach that owner's Mac?
agent-mgr agent <agent> 'what is the weather today?'          # a turn without the phone

# The dashboard crons. Not optional, and not replayed by restore --
# `hermes cron` persists to /opt/data/cron/jobs.json, which agent-mgr does not
# touch, so a rebuilt home comes up with a wall screen that never updates and
# nothing to diff against. Create-if-missing, so re-running it is safe.
agent-mgr agent <agent> 'set up the life dashboard crons; paste the output verbatim + exit code'
```

That is a turn, not an exec, and deliberately: `ld-dashboard` is a skill, so the
agent reads it and runs `register_crons.py` itself — inside the container, as
the gateway's own uid, with no uid for anyone to choose. A plain
`agent-mgr compose … exec` runs as **root** (measured: `id` in the `str`
container returns `uid=0`, because the image's s6 entrypoint remaps its in-image
`hermes` user to `HERMES_UID`/`HERMES_GID` and a bare exec bypasses that), and
on a fresh instance `jobs.json` does not exist yet — so a root-run registration
creates the schedule root-owned and the gateway can then never pause, resume or
remove anything in it. `agent-mgr` pins the pair on every exec it makes
(`agent-mgr:146`, `:194`, `:343`) for this reason; going through it is how this
repo avoids restating a uid rule. The repo has been bitten by root-owned paths
inside these nested binds before (`plow-pbc/agent-mgr#44`).

**The turn costs you the exit code.** `register_crons.py` refuses loudly — a missing or
unusable `config.json`, a `family.timezone` that is not the container's zone,
an empty `TZ`, a failed `cron create`, an unreadable `jobs.json`, a producer
that is registered but PAUSED — but a turn returns the *turn's* status, so a
non-zero exit reaches you only as whatever the agent chose to say about it. The
skill therefore instructs the agent to paste the script's output verbatim and
report its exit status, and to treat the run as unfinished until it has; ask for
it explicitly if it does not appear, because a summary is exactly the thing that
drops the words you would grep for. `refusing to register`, `WARNING` or
`PAUSED` anywhere in that output means bring-up did not finish.

If you are scripting this rather than running it by hand, take the exit code
directly and pin the uid yourself:

```sh
agent-mgr compose <agent> exec -T --user "$(id -u):$(id -g)" hermes \
  /opt/data/skills/ld-dashboard/scripts/register_crons.py
```

`$(id -u)` is right **only when the same host user who brought the instance up
runs it** — `agent-mgr` sets `HERMES_UID="$(id -u)"` from the invoking user
(`lib/common.sh:481`), so it is that user's uid that got baked in, not whoever
runs this later. Measured on `wakeup`: host `uid=1000`, `HERMES_UID=1000`, live
`jobs.json` owned `1000:1000`. A different operator or a root shell must read
`HERMES_UID`/`HERMES_GID` off the running container instead of borrowing their
own.

Then check it landed and watch a card appear — see
[Unattended runs](ld-dashboard/SKILL.md#unattended-runs), which carries both the
host and in-container forms, and what a forced run does and does not prove.

There is no `check-connectors` step: this instance has no connectors. See
[No connectors, and what that costs](#no-connectors-and-what-that-costs).

## What only the instance's owner can do

Everything else is automated. These two are not — and neither can be done on
their behalf, for different reasons:

**1. Texting the activation code.** `agent-mgr activate <agent>` does everything
up to the text itself: it prints a `Text Plow Activate: <code>` line naming the
number to send it to, then polls until Plow confirms. Only the owner can send
it, from their phone, and that text *is* the account binding — a code texted by
anyone else binds the instance to the wrong account and spends a one-time
activation doing it.

**Neither the code nor the number can be shared ahead of time.** Both come back
from `POST /v1/auth/activate` when the recipe runs: Plow assigns the line — you
cannot request a specific number — and the code is single-use and time-limited,
with the poll giving up after 15 minutes. So this is a two-person moment rather
than a step to hand off: run the recipe while they are at their phone, read them
the code and the number, and wait for `Verified: chat is active.`

If it expires, re-running is safe *before* a successful redeem — it just issues a
fresh code. After one succeeds, re-running spends another activation and strands
the previous chat.

**2. The Codex sign-in.** `agent-mgr sign-in <agent>` runs a device-code flow. It
is not a copy of a sibling's `auth.json`: that file is guarded by `auth.lock`,
two live gateways sharing one session is untested, and it may be a different
person's account and billing besides.

Both are **device-bound, not shell-bound.** The operator runs both recipes on
`wakeup`; the owner completes both on their own device — the text from their
phone, the OAuth from their browser. They never need an account on `wakeup`.

## What the operator can see

An instance's Plow token lives in its home's `.env` on `wakeup`, owned by the
operator's uid, and through it that person's mailbox is reachable from this
host. The container boundary does not change that: the dotenv is on the host
side of the mount. This is stated rather than left implied — it is a fact an
owner should know before they text the activation code, not one to discover
afterwards.

## No connectors, and what that costs

`skills.tsv` is **empty**. `plow-connectors` — the skill that reached this
owner's Gmail, Google Calendar and Slack with the gateway's own
`PLOW_CHAT_TOKEN` — is no longer installed, and nothing here replaces it.

That is a deliberate trade, not an oversight. It is what lets the life-dashboard
producers arrive as this agent's own mounted skills instead of a fetched tree,
and the two that need no account — `ld-weather` (NWS) and `ld-sports` (ESPN) —
work immediately, as do `ld-morning-triage`, rewritten onto the Mac's
iMessage DB read through Latch, and the three calendar producers —
`ld-morning-updates`, `ld-weekly-digest`, `ld-calendar-nudge` — their
calendar reads through Latch's vendored `gog`.

`ld-dashboard` carries all six schedules, all six registered. `agent-mgr
check-connectors` has nothing to report on this instance; calendar access is
Latch's vendored `gog`, not a connector.

**The one pin left is the plugin's, and it is not this repo's.** The Plow Chat
plugin is pinned in `plow-pbc/agent-mgr`, installed by `agent-mgr restore
<agent>` and read again by `agent-mgr activate <agent>`. It is fleet mechanism,
so one repo bumping it for every agent is the behaviour you want; this repo used
to carry a pin covering both, and that argument now belongs one layer up.

`skills.tsv` stays as an empty file rather than being deleted or commented:
`agent-mgr` gates its replay on `[ -s skills.tsv ]` — size, not content — and
feeds every non-empty line to `lib/fetch-tree` as a repo name, so a `# see
latch#183` line would kill `restore` at deploy time.
`test_skills_tsv_carries_no_comment_lines` holds that, and
`test_every_pinned_skill_is_a_sha_not_a_branch` still checks any row that
latch#183 later puts back.

## Layout

```
agent.env       what is true of every instance: where its config lives
runtime/        config.yaml: model, plugins, mcp_servers
skills.tsv      empty -- no connectors on this instance (see above)
ld-weather/     the NWS producer; ld-sports/ is the ESPN one
ld-morning-triage/  the iMessage triage producer, read through Latch
ld-morning-updates/ the calendar affirmation producer, gog through Latch
ld-shared/      the POST helper, the ld-config gate and the wire protocol
ld-dashboard/   the six cron schedules, all registered
scripts/        latch-verdict.py -- the one thing this repo owns outright
tests/          this agent's own contract; the fleet-wide ones live in agent-mgr
```

Deployment is `plow-pbc/agent-mgr`'s. This repo carried its own `compose.yml`,
verbatim copies of `model-provider` and `reload-if-running`, and eleven recipes
re-implementing `restore`, `activate`, `up` and the rest.

`check-latch` is the exception that stayed, because `agent-mgr check-latch`
classifies the response and this one shows it — see the recipe's own comment,
and `plow-pbc/agent-mgr#9` for graduating that shape upstream. It takes the
agent name as an argument and reaches the container through `agent-mgr compose`,
the documented escape hatch, rather than a compose file this repo no longer owns.

## Latch — whose Mac an instance reaches

`mcp_servers.latch` points at the Plow relay, which forwards to Plow Latch on
the owner's Mac: `plow_read_file`, `plow_write_file`, `plow_run_command`,
`plow_browser_*`, `plow_vault` and friends. The relay authorises the connection
and tells the Mac who is asking; the Mac authorises each action, so the approval
surface stays on that machine rather than here.

Credentials live in the instance's `.env` as `DOMO_DEVICE_UID` +
`DOMO_MCP_TOKEN`, minted from that Mac (`POST /v1/relay/agents`, which needs the
`relay:device` scope only that machine holds). The token travels in a header,
never in the URL.

`just check-latch <agent>` probes it from **inside** the container. It sends
`Accept: application/json, text/event-stream` — not optional: Plow's relay
speaks MCP streamable-HTTP and answers `406 Client must accept both
application/json and text/event-stream` without it, which reads as a dead
credential when the credential is fine. Measured: 200 with the header, 406
without.

**Editing `runtime/config.yaml` is not enough** — the gateway reads the
*installed* copy in the instance's home. Run `agent-mgr restore <agent>`, which
installs it and reloads the gateway only if the file actually changed.

## Open

- **Connectors are gone, not unlinked.** This instance installs no
  `plow-connectors`, so Gmail and Slack are unreachable however linked the
  owner's Plow account is, and `agent-mgr check-connectors <agent>` has nothing
  to probe. Google Calendar is back — through a vendored `gog` behind Latch
  rather than a connector skill; all three calendar producers ride it, and
  `plow-pbc/latch#183`'s port work is done. See [No connectors, and what
  that costs](#no-connectors-and-what-that-costs).
