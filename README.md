# life-assistant

> [!CAUTION]
> **This repository is private and must stay private.** It holds runtime
> configuration for agents that read and send a person's mail, read their
> calendar, and hold their Plow credential. Do not make it public, fork it
> publicly, or paste its contents anywhere public — including into tools that
> retain what they are shown.

A [Hermes](https://howto.plow.co/hermes) agent — texted from iMessage over the
Plow Chat platform — scoped to life and family logistics. Siblings on `wakeup`
are the rentals agent (`srosro/str-hermes-agent`) and the house-hunting agent
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
4. `agent-mgr register rowan` → `restore` → `up`

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

```sh
agent-mgr restore <agent>            # config.yaml and the plugin into its home
agent-mgr activate <agent>           # prints a code — its owner texts it from their phone
agent-mgr up <agent>                 # must precede sign-in: that runs inside this container
agent-mgr sign-in <agent>            # one-time Codex device flow — its owner completes it
just check-latch <agent>             # can this container reach that owner's Mac?
agent-mgr agent <agent> 'what is the weather today?'          # a turn without the phone

# The dashboard crons. Not optional, and not replayed by restore --
# `hermes cron` persists to /opt/data/cron/jobs.json, which agent-mgr does not
# touch, so a rebuilt home comes up with a wall screen that never updates and
# nothing to diff against. Create-if-missing, so re-running it is safe.
agent-mgr compose <agent> exec -T --user "$(id -u):$(id -g)" hermes \
  /opt/data/skills/ld-dashboard/scripts/register_crons.py
```

`--user` is not optional, and is the same pin `just check-latch` uses.
`agent-mgr compose … exec` lands as **root** — measured: `id` inside the `str`
container returns `uid=0` — and on a fresh instance `jobs.json` does not exist
yet, so an unpinned exec has `hermes cron create` write the schedule root-owned,
after which the gateway, running as `HERMES_UID`, can never pause, resume or
remove anything in it. This repo has already been bitten by root-owned paths
created inside these nested binds (`plow-pbc/agent-mgr#44`).

Then confirm a job actually fires **without restarting the gateway**. That is
the whole point of the step, and a schedule the running gateway never picked up
looks identical to a producer that runs and finds nothing:

```sh
agent-mgr compose <agent> exec -T --user "$(id -u):$(id -g)" hermes \
  /opt/hermes/bin/hermes cron run <job-id>      # job-id from `hermes cron list`
agent-mgr compose <agent> exec -T --user "$(id -u):$(id -g)" hermes \
  /opt/hermes/bin/hermes cron runs              # then look for the card on the kiosk
```

The dashboard also needs `/opt/data/ld/config.json` (the producers read
`weather` and `sports` from it) plus `DASHBOARD_ENDPOINT_URL` and
`DASHBOARD_TOKEN` in the instance's dotenv. `ld-shared/scripts/ld_config_gate.py`
is the single definition of a valid config — run it rather than eyeballing the
JSON, and make sure its `family.timezone` matches the container's `AGENT_TZ`,
because `hermes cron create` takes no per-job zone and every producer fires in
the container's.

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
work immediately. The four that read a person's calendar or mail do not:

| producer | card | needs | tracked by |
|---|---|---|---|
| `ld-morning-updates` | 2 · affirmation | Google Calendar | `plow-pbc/latch#183` |
| `ld-weekly-digest` | 4 · digest | Google Calendar | `plow-pbc/latch#183` |
| `ld-calendar-nudge` | 1 · alert | Google Calendar | `plow-pbc/latch#183` |
| `ld-morning-triage` | 1 · alert | Gmail + Slack | a rewrite onto the Mac's iMessage DB through Latch |

`ld-dashboard` carries all six schedules and registers only the two that can
run, so the blocked ones are recorded rather than lost. `agent-mgr
check-connectors` has nothing to report on this instance and asking a turn about
tomorrow's calendar will not work until `latch#183` lands.

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
ld-shared/      the POST helper, the ld-config gate and the wire protocol
ld-dashboard/   the six cron schedules; two registered, four blocked
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
  `plow-connectors`, so Google and Slack are unreachable however linked the
  owner's Plow account is, and `agent-mgr check-connectors <agent>` has nothing
  to probe. `plow-pbc/latch#183` is what brings Google back — through a vendored
  `gog` behind Latch rather than a connector skill. See [No connectors, and what
  that costs](#no-connectors-and-what-that-costs).
