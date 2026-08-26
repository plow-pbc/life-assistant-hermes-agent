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
(`srosro/sams-property-hermes-agent`).

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
- The `plow-connectors` skill reuses that same token, so the Gmail, Google
  Calendar and Slack it reaches are theirs.
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
agent-mgr restore <agent>            # config.yaml, plugin and skills into its home
agent-mgr activate <agent>           # prints a code — its owner texts it from their phone
agent-mgr up <agent>                 # must precede sign-in: that runs inside this container
agent-mgr sign-in <agent>            # one-time Codex device flow — its owner completes it
just check-latch <agent>             # can this container reach that owner's Mac?
agent-mgr check-connectors <agent>   # which of their connectors are linked and reachable
agent-mgr agent <agent> 'what is on my calendar tomorrow?'   # a turn without the phone
```

Google Calendar has no connector of its own — upstream puts it **under the
`gmail` connector**, as a `calendar.*` action namespace. So `gmail status` is
the Google connector's status and covers both mail and calendar, and
`check-connectors` probing `gmail` and `slack` is the complete set. There is no
third name to add.

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

## The plugin and the skill are pinned, not vendored

Two pins, in two repos, and the split is the point:

| Pin | Lives in | Covers |
|---|---|---|
| the Plow Chat plugin | `plow-pbc/agent-mgr` | every agent on the host — installed by `agent-mgr restore <agent>`, read again by `agent-mgr activate <agent>` |
| `plow-connectors` | this repo's `skills.tsv` | Gmail / Calendar / Slack for *this instance*, replayed by `agent-mgr restore <agent>` |

The plugin pin left with the deployment, and had to: it is fleet mechanism, so
one repo bumping it for every agent is the behaviour you want. The connector
skill stays here because it is this agent's, reviewable beside the config it
runs under.

This repo used to hold one pin covering both, on the argument that two which can
drift would mean the skill reading the mail and the plugin holding the token
came from different upstream trees. That argument survives; it belongs one layer
up now. Pinning once for the fleet is a stronger guarantee than each agent
pinning and hoping the copies agree.

Neither may be a branch: one would silently re-point a running agent on the next
upstream push, and these carry the plugin holding the chat token and the skill
that reads a person's mail. The refusal is `plow-pbc/agent-mgr`'s — its
`install-plugin` path for the plugin ref, `lib/fetch-skill` for each `skills.tsv`
row — and left this repo with the recipes that used to carry it. Cited by name
rather than line, because a line number in another repo goes stale on its next
edit and nothing here can detect that.

What this repo still asserts is the checked-in value:
`test_every_pinned_skill_is_a_sha_not_a_branch` fails review if a row in
`skills.tsv` is not 40 hex characters.

Copying these into the repo instead would make it a fork of them, which is what
`srosro/str-hermes-agent#138` spent −1,311 LOC undoing after a vendored plugin
drifted until production was serving a working tree.

`restore` fetches the two skill files directly rather than running upstream's
`install_connectors.sh`: that script copies from a path inside its own checkout,
so curling the script alone finds no source to copy. The destination is not a
preference either — `SKILL.md`'s `allowed-tools` line names
`/opt/data/skills/plow-connectors/plow_connector.py` literally, so a skill
installed one directory deeper loads and is then refused permission to run its
own helper.

## Layout

```
agent.env       what is true of every instance: where its config lives
runtime/        config.yaml: model, plugins, mcp_servers
skills.tsv      the pinned plow-connectors SHA, reviewable beside the config
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

- **Connectors are each owner's to link.** Google and Slack are linked on their
  Plow account, not here. `agent-mgr check-connectors <agent>` reports
  `connected:false` until they do, which is a real answer rather than a failure.
