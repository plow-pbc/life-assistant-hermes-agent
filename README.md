# Rowan's life Hermes agent

> [!CAUTION]
> **This repository is private and must stay private.** It holds runtime
> configuration for an agent that reads and sends a person's mail, reads his
> calendar, and holds his Plow credential. Do not make it public, fork it
> publicly, or paste its contents anywhere public — including into tools that
> retain what they are shown.

A [Hermes](https://howto.plow.co/hermes) agent — texted from iMessage over the
Plow Chat platform — scoped to life and family logistics for Rowan. One of a
fleet on `wakeup`: the rentals agent is `sams-str-hermes-agent`, the mail and
calendar agent is `sams-admin-hermes-agent`, the house-hunting agent is
`sams-property-hermes-agent`.

What makes this one different from its three siblings: **it is not the same
person's agent.** They are all keyed by purpose on one operator's Plow account.
This one is keyed by a different person, on his own account — which is the fact
every other decision here follows from.

It runs the upstream `nousresearch/hermes-agent` image directly, pinned by
digest, with no derived layer. All state lives in `~/.hermes-rowan` on the host,
mounted at `/opt/data`; the image is stateless. Its clock is `America/Chicago` —
Rowan's zone, and the one setting deliberately unlike all three siblings, which
run the operator's Pacific.

## The account boundary

`POST /v1/auth/activate` carries **no credential**. Its payload is
`{name, provision_chat}` and nothing else, so the account binding is decided
entirely by *which phone texts the code back*. Rowan texts it from his handset,
and three things follow:

- The `PLOW_CHAT_TOKEN` that lands in `~/.hermes-rowan/.env` is **his**.
- The `plow-connectors` skill reuses that same token, so the Gmail, Google
  Calendar and Slack it reaches are **his**, not the operator's.
- His phone line does not draw on the operator's pool. Plow's five service-wide
  numbers collide on **(line, participant set)**, and his handset is a different
  set — so activating here spends nothing the other three agents are using.

Nothing of Rowan's is pre-staged on this host. There is no credential to hand
over before bring-up; the activation exchange mints it.

## What this agent cannot reach

Asserted in `tests/test_config_contract.py`, so a copy-paste from a sibling's
compose file fails loudly instead of quietly widening the blast radius. Here
that copy-paste would cross an **account** boundary, not just an agent one,
which is why these are tests rather than review notes:

- **The three sibling agents' state** — `~/.hermes`, `~/.hermes-admin`,
  `~/.hermes-property`. Two gateways sharing one home would share one
  `auth.json` and one dotenv, including one `PLOW_CHAT_CHAT_UID`, so whichever
  started last would own the chat.
- **The operations vault** (`~/hermes-vault`) — compiled guest conversations and
  property access facts, door and keypad codes among them.
- **Hostex and Seam.** No PMS access, no lock control — those belong to the
  rentals agent and reach a different person's property.
- **The operator's Mac.** Latch here points at *Rowan's* device uid, minted on
  his machine. `tests/` asserts `latch` is the only `mcp_servers` entry, so a
  sibling's block arriving by copy-paste fails loudly.

One mount here against the rentals agent's six. That asymmetry is the design,
not an omission to be tidied up later.

## Bring-up

```sh
agent-mgr restore rowan             # config.yaml + a .env skeleton into ~/.hermes-rowan
agent-mgr install-plugin rowan      # the Plow Chat plugin, from the pinned SHA
agent-mgr restore rowan  # Gmail / Calendar / Slack, from the same pinned SHA
agent-mgr activate rowan            # prints a code — Rowan texts it from his phone
agent-mgr up rowan                  # must precede sign-in: that runs inside this container
agent-mgr sign-in rowan             # one-time Codex device flow — Rowan completes it
just check-latch         # can this container reach Rowan's Mac?
agent-mgr check-connectors rowan    # which of his connectors are linked and reachable
agent-mgr agent rowan 'what is on my calendar tomorrow?'   # a turn without the phone
```

Google Calendar has no connector of its own — upstream puts it **under the
`gmail` connector**, as a `calendar.*` action namespace. So `gmail status` is
the Google connector's status and covers both mail and calendar, and
`check-connectors` probing `gmail` and `slack` is the complete set. There is no
third name to add.

## What only Rowan can do

Everything else is automated. These two are not — and neither can be done for
him, for different reasons:

**1. Texting the activation code.** `agent-mgr activate rowan` does everything up to the
text itself: it prints a `Text Plow Activate: <code>` line naming the number to
send it to, then polls until Plow confirms. Only Rowan can send it, from his
phone, and that text *is* the account binding — a code texted by anyone else
binds this agent to the wrong account and spends a one-time activation doing it.

**Neither the code nor the number can be shared ahead of time.** Both come back
from `POST /v1/auth/activate` when the recipe runs: Plow assigns the line — you
cannot request a specific number — and the code is single-use and time-limited,
with the poll giving up after 15 minutes. So this is a two-person moment rather
than a step to hand off: run the recipe while Rowan is at his phone, read him
the code and the number, and wait for `Verified: chat is active.`

If it expires, re-running is safe *before* a successful redeem — it just issues a
fresh code. After one succeeds, re-running spends another activation and strands
the previous chat.

**2. The Codex sign-in.** `agent-mgr sign-in rowan` runs a device-code flow. It is not a
copy of a sibling's `auth.json`: that file is guarded by `auth.lock`, two live
gateways sharing one session is untested, and this is a different person's
account and billing besides.

Both are **device-bound, not shell-bound.** The operator runs both recipes here;
Rowan completes both on his own device — the text from his phone, the OAuth from
his browser. He never needs an account on `wakeup`.

## What the operator can see

Rowan's Plow token lives in `~/.hermes-rowan/.env` on `wakeup`, owned by the
operator's uid, and through it his mailbox is reachable from this host. The
container boundary does not change that: the dotenv is on the host side of the
mount. This is stated rather than left implied — it is a fact he should know
before he texts the activation code, not one to discover afterwards.

## The plugin and the skill are pinned, not vendored

Two pins now, in two repos, and the split is the point:

| Pin | Lives in | Covers |
|---|---|---|
| the Plow Chat plugin | `plow-pbc/agent-mgr`'s `runtime/plow-chat-plugin.ref` | every agent on the host — `agent-mgr install-plugin rowan`, and read again by `agent-mgr activate rowan` |
| `plow-connectors` | this repo's `skills.tsv` | Gmail / Calendar / Slack for *this* agent, replayed by `agent-mgr restore rowan` |

The plugin pin left with the deployment, and had to: it is fleet mechanism, so
one repo bumping it for every agent is the behaviour you want. The connector
skill stays here because it is this agent's, reviewable beside the config it
runs under.

This repo used to hold one pin covering both, on the argument that two which can
drift would mean the skill reading the mail and the plugin holding the token
came from different upstream trees. That argument survives — it just belongs one
layer up now. `agent-mgr` pins the plugin once for the fleet, which is a stronger
guarantee than each agent pinning it and hoping the copies agree.

Both refuse a ref that is not a 40-char SHA. A branch would silently re-point a
running agent on the next upstream push, and these carry the plugin holding the
chat token and the skill that reads Rowan's mail.

Copying these into the repo instead would make it a fork of them, which is what
`srosro/str-hermes-agent#138` spent −1,311 LOC undoing after a vendored plugin
drifted until production was serving a working tree.

`agent-mgr restore rowan` fetches the two skill files directly rather than
running upstream's `install_connectors.sh`: that script copies from a path
inside its own checkout, so curling the script alone finds no source to copy.
The destination is not a preference either — `SKILL.md`'s `allowed-tools` line
names `/opt/data/skills/plow-connectors/plow_connector.py` literally, so a skill
installed one directory deeper loads and is then refused permission to run its
own helper.

## Layout

```
agent.env       which agent this is: Rowan's timezone, and where its config lives
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
and `plow-pbc/agent-mgr#9` for graduating that shape upstream. It reaches the
container through `agent-mgr compose`, the documented escape hatch, rather than
a compose file this repo no longer owns.

## Latch — the Mac this reaches is Rowan's

`mcp_servers.latch` points at the Plow relay, which forwards to Plow Latch on
**Rowan's** Mac: `plow_read_file`, `plow_write_file`, `plow_run_command`,
`plow_browser_*`, `plow_vault` and friends. The relay authorises the connection
and tells the Mac who is asking; the Mac authorises each action, so the approval
surface stays on his machine rather than here.

Credentials live in `~/.hermes-rowan/.env` as `DOMO_DEVICE_UID` +
`DOMO_MCP_TOKEN`, minted from his Mac (`POST /v1/relay/agents`, which needs the
`relay:device` scope only that machine holds). The token travels in a header,
never in the URL.

`just check-latch` probes it from **inside** the container. It sends
`Accept: application/json, text/event-stream` — not optional: Plow's relay
speaks MCP streamable-HTTP and answers `406 Client must accept both
application/json and text/event-stream` without it, which reads as a dead
credential when the credential is fine. Measured: 200 with the header, 406
without.

**Editing `runtime/config.yaml` is not enough** — the gateway reads the
*installed* copy at `~/.hermes-rowan/config.yaml`. Run `agent-mgr restore rowan`, which
installs it and reloads the gateway only if the file actually changed.

## Open

- **Connectors are Rowan's to link.** Google and Slack are linked on his Plow
  account, not here. `agent-mgr check-connectors rowan` reports `connected:false` until he
  does, which is a real answer rather than a failure.
