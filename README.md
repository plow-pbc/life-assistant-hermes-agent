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

Runs the upstream `nousresearch/hermes-agent` image directly, pinned by digest,
with no derived layer. All state lives in `~/.hermes-rowan` on the host, mounted
at `/opt/data`. The image is stateless.

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
- **Hostex, Seam and Latch.** No `mcp_servers` at all: no PMS access, no lock
  control, no reach into the operator's Mac.

One mount here against the rentals agent's six. That asymmetry is the design,
not an omission to be tidied up later.

## Bring-up

```sh
just restore             # config.yaml + a .env skeleton into ~/.hermes-rowan
just install-plugin      # the Plow Chat plugin, from the pinned SHA
just install-connectors  # Gmail / Calendar / Slack, from the same pinned SHA
just activate            # prints a code — Rowan texts it from his phone
just up                  # must precede sign-in: that runs inside this container
just sign-in             # one-time Codex device flow — Rowan completes it
just check-connectors    # which of his connectors are linked and reachable
just agent 'what is on my calendar tomorrow?'   # a turn without the phone
```

Google Calendar has no connector of its own — upstream puts it **under the
`gmail` connector**, as a `calendar.*` action namespace. So `gmail status` is
the Google connector's status and covers both mail and calendar, and
`check-connectors` probing `gmail` and `slack` is the complete set. There is no
third name to add.

## What only Rowan can do

Everything else is automated. These two are not — and neither can be done for
him, for different reasons:

**1. Texting the activation code.** `just activate` does everything up to the
text itself: it prints a `Text Plow Activate: …` line and polls until Plow
confirms. Only Rowan can send it, from his phone, and that text *is* the account
binding — a code texted by anyone else binds this agent to the wrong account and
spends a one-time activation doing it.

**2. The Codex sign-in.** `just sign-in` runs a device-code flow. It is not a
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

`runtime/plow-chat-plugin.ref` holds one 40-char SHA of
[plow-pbc/seed-hermes-plow](https://github.com/plow-pbc/seed-hermes-plow), and
it covers both installs:

| Recipe | What it fetches at that SHA |
|---|---|
| `just install-plugin` | `ref/scripts/install_direct_mount.sh` — the Plow Chat plugin |
| `just install-connectors` | `ref/hermes-skill/plow-connectors/{SKILL.md,plow_connector.py}` |
| `just activate` | `ref/scripts/create_plow_chat_curl.sh` |

All three refuse a ref that is not a 40-char SHA. A branch would silently
re-point a running agent on the next upstream push, and this pin carries both
the plugin holding the chat token and the skill that reads Rowan's mail.

One pin, not two, deliberately: two that can drift would mean the skill reading
the mail and the plugin holding the token came from different upstream trees.

Copying these into the repo instead would make it a fork of them, which is what
`sams-str-hermes-agent#138` spent −1,311 LOC undoing after a vendored plugin
drifted until production was serving a working tree.

`just install-connectors` fetches the two skill files directly rather than
running upstream's `install_connectors.sh`: that script copies from a path
inside its own checkout, so curling the script alone finds no source to copy.
The destination is not a preference either — `SKILL.md`'s `allowed-tools` line
names `/opt/data/skills/plow-connectors/plow_connector.py` literally, so a skill
installed one directory deeper loads and is then refused permission to run its
own helper.

## Layout

| Path | What |
|---|---|
| `compose.yml` | The gateway service. One mount, no ports, no build. |
| `justfile` | Every recipe, and the homes none of them may reach |
| `runtime/config.yaml` | The declarative half of `~/.hermes-rowan` |
| `runtime/plow-chat-plugin.ref` | The one pinned upstream SHA |
| `.env.example` | The environment-key contract, with no values |
| `tests/` | What this agent must not reach, asserted |

## Open

- **Confirm Rowan's timezone.** `TZ=America/Los_Angeles` is inherited from the
  host and its three siblings. A life assistant reads "tomorrow morning" off
  this, so a wrong value is wrong in the one place this agent is for.
- **Connectors are linked on Rowan's side, not here.** `just check-connectors`
  reports `connected:false` for either connector he has not linked to his Plow
  account yet. That is a real answer, not a failure — and because Calendar rides
  on the `gmail` connector, linking Google is what turns on the calendar
  questions this agent is mostly for.
