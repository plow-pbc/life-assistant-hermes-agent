# life-assistant

> [!IMPORTANT]
> **This repo is code only.** An instance holds a person's Plow credential and
> drives their own Mac through Latch — [Latch — whose Mac an instance
> reaches](#latch--whose-mac-an-instance-reaches) says what that grants, and
> [No connectors, and what that costs](#no-connectors-and-what-that-costs) what
> it no longer does. All of it lives in the instance's home on the host, never
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
| a second person's | `<name>` | `~/.hermes-<name>` | `hermes-<name>` | **not yet** — see [Adding a second instance](#adding-a-second-instance) |

None of it is declared in this repo. For a *registered* instance `agent-mgr`
derives home and container from the **registry name**, so any number of them run
from one checkout with no per-person fork — two rows against the same directory
resolve to separate homes and containers.

**Neither row is state.** Both are what `agent-mgr` *would* derive from the
registry name; this table says what each instance is permitted, not what the
registry holds, because nothing in this tree can check that.

```sh
agent-mgr register life ~/services/life-assistant-hermes-agent
# agent-mgr register <name> ~/services/life-assistant-hermes-agent   # same directory
#   NOT YET -- see README "Adding a second instance" for the preconditions.
```

That is why `agent.env` declares no `AGENT_HOME`, `AGENT_CONTAINER` or
`AGENT_PROJECT`. It is not an omission — a descriptor that named a home could
only ever name *one*, and `agent-mgr`'s ownership guard refuses a home that does
not match the instance's own name, so a repo that declared one could not be
shared at all. Silence is what makes this work.

`agent.env` is a **closed set** holding only `AGENT_CONFIG` and `AGENT_LIVE`;
`tests/test_config_contract.py` fails on any other key, of any kind. Every
instance reads this one file, so a new key is given to all of them — adding one
is a deliberate edit to `DESCRIPTOR_KEYS`, not something a comment can justify.

It runs the upstream `nousresearch/hermes-agent` image directly, pinned by
digest, with no derived layer. State lives in the instance's own home on the
host, mounted at `/opt/data`; the image is stateless.

## Adding a second instance

A second person's instance is **not registered yet**. Everything blocking it
lives here — the table, the register block, the bring-up line and the
`justfile` all point at this section rather than carrying their own copy, so
there is one place to correct when it changes.

**Precondition 1 — the timezone.** `agent.env` is a closed set (above) and could
not carry `AGENT_TZ` even if it were open: a shared descriptor holds one value
and every instance would read it. Instances therefore inherit `agent-mgr`'s fleet
default, `America/Los_Angeles`. A person in another zone cannot be registered
until [`plow-pbc/agent-mgr#14`](https://github.com/plow-pbc/agent-mgr/issues/14)
adds a per-instance overlay. This is not cosmetic — a life assistant resolves
"tomorrow morning" and every scheduled thing against its clock, so an offset is
wrong in exactly the place the agent exists for.

**Precondition 2 — the home must be unowned, and nothing would stop you.** A
pre-agent-mgr stack that already serves `~/.hermes-<name>` from its own compose
file is not registered, which is precisely the gap described in
[What an instance cannot reach](#what-an-instance-cannot-reach). Registering and
restoring while that stack runs is that case. Precondition 1 does not
imply this one: #14 landing makes it *more* reachable, not less.

**The order, therefore:**

1. `agent-mgr#14` lands
2. write `~/.config/agent-mgr/<name>.env` with their `AGENT_TZ`
3. **bring any pre-agent-mgr stack down** and confirm `~/.hermes-<name>` is unowned
4. `agent-mgr register <name>`, then the ordinary [Bring-up](#bring-up) — a
   pointer rather than a copy, deliberately: this section's whole premise is
   that everything points here instead of carrying its own sequence, and the
   abbreviated `deploy` → `up` chain that used to sit on this line was already
   missing the `mkdir` that keeps `skills/` and `ld/` from landing root-owned.

Step 1 retires precondition 1. Step 3 is the one that outlives it.

## The account boundary — how one repo serves two people

`POST /v1/auth/activate` carries **no credential**. Its payload is
`{name, provision_chat}` and nothing else, so the account binding is decided
entirely by *which phone texts the code back*. That single fact is what lets the
tracked tree stay identical for everyone:

- The `PLOW_AGENT_TOKEN` that lands in the instance's `.env` belongs to whoever
  texted.
- The Plow Chat credential and private conversation belong to that owner. Other
  people participate through group conversations; explicit owner trust controls
  whether a group can use normal tools and owner material. Replies remain
  visible to the whole group.
  (The agent used to reach Gmail, Calendar and Slack through the
  `plow-connectors` skill — see [No connectors, and what that
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
  `auth.json` and one dotenv, including one `PLOW_HOME_CHANNEL`, so whichever
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
registered today; see [Adding a second instance](#adding-a-second-instance) for the other.

Nothing to land first for the dashboard itself. Before restoring this revision,
deploy the Plow current-session preferences endpoint and compatible
`hermes-plow-chat` pin; otherwise removing the local notification override can
put self-improvement reviews back in the owner's chat. The agent writes its own
`ld/config.json` and mints
the wall's token on the owner's first reply: `runtime/SOUL.md` tells it that
a missing or gate-failing config means it is not set up, and
`ld-setup/SKILL.md` is what it runs then — the interview, `write_config.py`,
`mint_wall_token.py`, then the Pi bring-up. The Pi keeps its own
`/api/message` server on the household LAN, which the agent reaches only
through Plow Latch on the owner's Mac: it ships the token there in two files
(never through chat), runs the two install lines on the Pi over `ssh` from
the Mac, and every card after that is delivered the same way
(`ld-shared/references/latch-delivery.md`) — so cards refresh only while
the Mac is awake with Latch running. No Mac: the lines are texted to the
owner to type. Then `register_crons.py` and a forced `ld-weather` run. The
one thing that has to
be true beforehand is that the container's zone is the owner's: `AGENT_TZ` in
the instance dotenv, set after `deploy` and before `up` (agent-mgr README,
*Where a per-person value goes*). Setup refuses a timezone answer that is not
the container's, and the owner cannot fix that side themselves.

Host-side edits to `~/.hermes-<agent>/ld/config.json` still work — it is the
instance owner's file, mode 600; edit it, then `agent-mgr restart <agent>`.
`ld-shared/scripts/ld_config_gate.py <path>` is the single definition of a
valid config: empty output is a pass, anything else is the list of what is
wrong (its exit code is always 0). Host-side edits are not the only way it
changes: the file stays writable by the agent's own runtime for the rest of
its life, and the gate only catches structurally-wrong config, not a
structurally-valid rewrite from a prompt-injected turn (compose.override.yml
has the detail; no mitigation for that gap exists yet).

The `ld-viewer-dev` skill is an operator tool, not part of setup, and needs
three more host-side files in the instance home, all landed as the instance
owner: `ld-dev/repo-url` (one line, the household repo's SSH URL),
`ld-dev/ssh/deploy_key` (mode 600 — a per-repo deploy key registered
write-scoped on the household repo; minted host-side, the private half never
displayed), `ld-dev/ssh/known_hosts` (GitHub's published SSH host keys —
`gh api meta --jq '.ssh_keys[]' | sed 's/^/github.com /'` — so git-over-ssh
runs strict, no trust-on-first-use), and `ld-dev/ssh/pi_key` (mode 600 —
OpenSSH refuses a group/world-readable private key; the kiosk-diagnostics
SSH key, its `.pub` authorized on the Pi).

```sh
agent-mgr deploy <agent>             # config.yaml and the plugin into its home
agent-mgr activate <agent>           # prints a code — its owner texts it from their phone

# BEFORE `up`, and not optional. Both directories have to be owned by the
# instance owner, for different reasons. `skills/`: compose.override.yml mounts
# each skill UNDER /opt/data, which is already the home bind, so the runtime
# creates the missing mountpoint inside that bind's source on the host -- as
# root, and no later `deploy` can install into it. plow-pbc/agent-mgr#44 is the
# fleet-level fix; until it lands this line stands in for it. `ld/`: the agent
# writes its config and every composed tile there with its file tool, so a
# root-owned `ld/` costs every card, quietly -- nothing downstream re-checks it.
# Two paths, not `{skills,ld}` -- brace expansion is bash/zsh, and under the
# host's `sh` that collapses to one literal directory and exits 0.
mkdir -p ~/.hermes-<agent>/skills ~/.hermes-<agent>/ld

agent-mgr up <agent>                 # must precede sign-in: that runs inside this container
agent-mgr sign-in <agent>            # one-time Codex device flow — its owner completes it
just check-latch <agent>             # can this container reach that owner's Mac?

# Then the owner replies to the agent's 👋 from their phone. That reply IS the
# rest of bring-up: the agent interviews them, writes ld/config.json, mints
# the wall's token, brings the Pi up through Latch over their LAN and ships
# the token to the Pi and the Mac (or texts them the lines when there's no
# Mac), registers the crons and forces a weather card (ld-setup/SKILL.md).
# Nothing more to run here.
```

The crons are registered by the agent itself, inside the container, as the
gateway's own uid — deliberately not by an exec from here. A plain
`agent-mgr compose … exec` runs as **root** (measured: `id` in the `str`
container returns `uid=0`, because the image's s6 entrypoint remaps its
in-image `hermes` user to `HERMES_UID`/`HERMES_GID` and a bare exec bypasses
that), and on a fresh instance `jobs.json` does not exist yet — so a root-run
registration creates the schedule root-owned and the gateway can then never
pause, resume or remove anything in it. `agent-mgr` pins the pair on every
exec it makes for this reason; the repo has been bitten by root-owned paths
inside these nested binds before (`plow-pbc/agent-mgr#44`).

**A turn costs you the exit code.** `write_config.py`, `mint_wall_token.py` and
`register_crons.py` all refuse loudly with a non-zero exit, but a chat turn
returns the *turn's* status. `ld-setup/SKILL.md` therefore holds the agent to
`ld-dashboard`'s contract — paste each script's output verbatim with its
exit status, and treat the phase as unfinished until it has. `refusing`,
`WARNING` or `PAUSED` anywhere in that output means bring-up did not finish.

If you are re-registering from here rather than through the owner's chat,
take the exit code directly and pin the uid yourself:

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
`PLOW_AGENT_TOKEN` — is no longer installed, and nothing here replaces it.

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
plugin is pinned in `plow-pbc/agent-mgr`, installed by `agent-mgr deploy
<agent>` and read again by `agent-mgr activate <agent>`. It is fleet mechanism,
so one repo bumping it for every agent is the behaviour you want; this repo used
to carry a pin covering both, and that argument now belongs one layer up.

## Trusted group conversations

Conversation trust is state on the owner's Plow `Chat`, not on the physical
phone line and not in this repository. The owner can change it from the Plow
dashboard's **Trusted lines** card or ask the agent in that conversation, which
uses the shared `plow_set_conversation_trusted` tool after explicit
confirmation. A member cannot change the setting.

In an untrusted group, the assistant keeps owner material out of the thread. In
a trusted group, every participant may ask it to use its normal tools and
connected accounts, and requested results are answered where everyone can see
them. For this life assistant, “What's on the schedule today?” reaches Google
Calendar through Latch's vendored `gog`; trust changes whether that result may
be returned to the group, not how calendar access works. Credentials,
authentication secrets, raw tokens, and payment-card secrets remain excluded.

The policy and tool live in the one `hermes-plow-chat` plugin used by Docker and
cloud agents. Deploy the Plow API preference first, advance agent-mgr's exact
plugin SHA second, then run `agent-mgr deploy <agent>` so the installed copy in
that instance's home changes. `runtime/config.yaml` only enables the platform;
adding group prompts or another trust flag there would create a second policy
path that the dashboard cannot update.

Before restoring an existing instance, migrate its config in place: copy
`calendar_nudge.owner_identities[0]` to `calendar.account` without rebuilding
the object or changing any other preference, then require an empty result from
`ld_config_gate.py`. The three calendar skills now add that account to their
exact gog argv; manually run and approve each new 1-day, 3-day, and 7-day gather
shape once through Latch before relying on the unattended crons.

`skills.tsv` stays as an empty file rather than being deleted or commented:
`agent-mgr` gates its replay on `[ -s skills.tsv ]` — size, not content — and
feeds every non-empty line to `lib/fetch-tree` as a repo name, so a `# see
latch#183` line would kill `deploy` at deploy time.
`test_skills_tsv_carries_no_comment_lines` holds that, and
`test_every_pinned_skill_is_a_sha_not_a_branch` still checks any row that
latch#183 later puts back.

## Layout

```
agent.env       what is true of every instance: where its config lives, and
                that it is live (AGENT_LIVE=1 -- transitions ask first)
runtime/        config.yaml: model, plugins, mcp_servers; SOUL.md: persona + the setup rule
skills.tsv      empty -- no connectors on this instance (see above)
ld-weather/     the NWS producer; ld-sports/ is the ESPN one
ld-morning-triage/  the iMessage triage producer, read through Latch
ld-morning-updates/ the calendar affirmation producer, gog through Latch
ld-shared/      the POST helper, the ld-config gate and the wire protocol
ld-dashboard/   the six cron schedules, all registered
ld-payments/    pay a bill/person via the owner-approval flow (not deployable yet -- see below)
ld-setup/       first-run setup end-to-end: config -> wall token -> Pi over Latch -> crons
scripts/        latch-verdict.py -- the one thing this repo owns outright
tests/          this agent's own contract; the fleet-wide ones live in agent-mgr
Dockerfile      builds this agent as a standalone image -- adapter only (see below)
.dockerignore   keeps secrets and stale bytecode out of that build's context
```

Deployment is `plow-pbc/agent-mgr`'s. This repo carried its own `compose.yml`,
verbatim copies of `model-provider` and `reload-if-running`, and eleven recipes
re-implementing `deploy`, `activate`, `up` and the rest.

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
*installed* copy in the instance's home. Run `agent-mgr deploy <agent>`, which
installs it and reloads the gateway only if the file actually changed.

## Building a standalone image

`Dockerfile` bakes this repo's `runtime/SOUL.md` and `ld-*` skills onto the
pinned Hermes base, so the agent runs as a self-contained container instead of
from `agent-mgr`'s mounts. `agent-mgr` is unaffected and keeps running the repo
the way it always has.

```sh
# ECR Public 403s HEAD on a digest reference, and BuildKit resolves FROM with
# HEAD -- so pull the pinned base first (pull uses GET), then build.
docker pull public.ecr.aws/e1h7x4a2/plow-cloud-agents@sha256:84b46cbb9e7f6ea87825bb7a5e04d0071faa03c6e49e66e7b052dbaa0fdf3c1d
docker build .
```

Then put the Plow credentials in the image's dotenv, `/var/lib/hermes/.env`:

```
PLOW_API_BASE=https://api.plow.co       # API root, no /v1 suffix
PLOW_HOME_CHANNEL=cht_...               # the home chat
PLOW_AGENT_TOKEN=...                    # the agent's scoped bearer
HERMES_CUSTOM_PLOW_API_KEY=...          # the model key config.yaml names
TZ=America/Los_Angeles                  # the owner's own zone, not UTC
```

`TZ` is load-bearing, not cosmetic: `ld-setup` refuses to write a config whose
timezone disagrees with the container's, and the dashboard crons refuse to
register without one — so an agent provisioned in `UTC` for an owner who is not
in `UTC` cannot finish setup, and its reminders and digests never schedule.

Those are the names `plow-chat-platform` reads. The pre-unification spellings
(`PLOW_CHAT_BASE_URL`, `PLOW_CHAT_CHAT_UID`, `PLOW_CHAT_TOKEN`) are retired and
nothing reads them; an instance still on them runs `agent-mgr
migrate-plugin-env <name>` or re-activates.

The `FROM` is pinned by **digest**, not by tag. The `base-<sha>` half names the
commit of [`plow-pbc/plow-hermes-agent`](https://github.com/plow-pbc/plow-hermes-agent)
it was built from, and the `@sha256:` half is what the build actually resolves —
so no tag reassignment can substitute different bytes under an existing tenant.
Bump both together when moving to a newer base.

The pull step above is not optional on this registry: ECR Public answers `HEAD`
on a digest reference with `403 Forbidden` while answering `GET` normally, and
BuildKit resolves a `FROM` with `HEAD` — so a clean `docker build .` fails at
metadata resolution until the digest is in the local store. `docker pull` takes
the `GET` path and puts it there. The tag-only form has no such problem, which
is the trade for the supply-chain guarantee.

## Open

- **Connectors are gone, not unlinked.** This instance installs no
  `plow-connectors`, so Gmail and Slack are unreachable however linked the
  owner's Plow account is, and `agent-mgr check-connectors <agent>` has nothing
  to probe. Google Calendar is back — through a vendored `gog` behind Latch
  rather than a connector skill; all three calendar producers ride it, and
  `plow-pbc/latch#183`'s port work is done. See [No connectors, and what
  that costs](#no-connectors-and-what-that-costs).

- **`ld-payments` is the instruction layer only, and not yet deployable.** The
  skill tells the agent to stop refusing payment requests and run them through
  the owner-approval flow; it does **not** implement the guardrail. It is safe
  to run only once the platform's fail-closed payment gate and the owner
  per-payment confirmation infra are live — so it must not reach the deployed
  agent before those. Mounted for review; a deploy is gated on that ordering.
