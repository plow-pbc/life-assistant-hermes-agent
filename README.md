# life-assistant

> [!IMPORTANT]
> **This repo is code only.** An agent holds a person's Plow credential and
> drives their own Mac through Latch — [Latch — whose Mac an agent
> reaches](#latch--whose-mac-an-agent-reaches) says what that grants. All of it
> lives in the agent's own home volume, never here. Keep it that way: nothing
> under this tree may carry a credential, a chat id, or a person's data.

A [Hermes](https://howto.plow.co/hermes) agent — texted from iMessage over the
Plow Chat platform — scoped to life and family logistics. Siblings are the
rentals agent (`plow-pbc/str-hermes-agent`) and the house-hunting agent
(`plow-pbc/property-hunt-hermes-agent`).

**One repo, one agent per person.** This is not one person's agent someone else
could copy; it is the common half, and the tracked tree is identical for
everyone. The difference is entirely in the credential its owner texts for.

This repo is content. The runtime — the Hermes image, the `plow_chat` plugin,
the hardened home, the s6 boot layer, and the gateway's own `config.yaml` — is
[`plow-pbc/plow-hermes-agent`](https://github.com/plow-pbc/plow-hermes-agent).
The `Dockerfile` here adds a persona, its skills and one background job.

## Run locally

`compose.yml` here builds this checkout and runs it.
[`plow-agents`](https://github.com/plow-pbc/plow-agents) mints the credential
the container reads, and does nothing else about running one.

```sh
plow-agents mint ln_xxx        # in this checkout, before the first `up`
docker compose up --build -d
```

**Build fails with `403 Forbidden` on the base image?** An old `docker login`
for `public.ecr.aws` has expired — those credentials last 12 hours, and the
build sends the stale one instead of asking anonymously. Forget it and build
again:

```sh
docker logout public.ecr.aws
```

The base is public and pulls anonymously; nothing here needs a registry login.

| To | Run |
| --- | --- |
| rebuild and keep its memory | `docker compose up --build -d` |
| start fresh — a new install, onboarding again, or any `SOUL.md` or skill edit | `docker compose down -v && docker compose up --build -d` |
| finish | `plow-agents revoke && docker compose down -v` |

The chat and its history live on Plow, and survive all three.

**`TZ` is this agent's own, not the provisioner's**: the base image sets
none, so a cont-init step here writes it at boot from `family.timezone` in
`ld/config.json`, falling back to `UTC` before onboarding has asked anyone
where they live. Every schedule fires in that zone — `hermes cron create` takes
no per-job zone — so changing the household's timezone takes effect on the next
restart. `ld-setup` writes the new zone and says so; the cron registration is
what refuses, and it will not schedule anything while the config and the
running container disagree.

## The account boundary — how one repo serves two people

`POST /v1/auth/activate` carries **no credential**. Its payload is
`{name, provision_chat}` and nothing else, so the account binding is decided
entirely by *which phone texts the code back*. That single fact is what lets the
tracked tree stay identical for everyone:

- The `PLOW_AGENT_TOKEN` the host drops in belongs to whoever texted.
- The Plow Chat credential and private conversation belong to that owner. Other
  people participate through group conversations; explicit owner trust controls
  whether a group can use normal tools and owner material.
- Their phone line does not draw on anyone else's pool. Plow's five service-wide
  numbers collide on **(line, participant set)**, and a different handset is a
  different set — so activating one agent spends nothing the others use.

Nothing is pre-staged for anyone. There is no credential to hand over before
bring-up; the activation exchange mints it.

Latch is bound the same way: the relay is the agent's own, `PLOW_MCP_URL` and
`PLOW_AGENT_TOKEN`, resolved by first boot from the credential the host dropped
in and published where nothing the agent runs can write. The image is shared;
the Mac that relay resolves to is not.

## What an agent cannot reach

Here a copy-paste can cross an **account** boundary, not just an agent one.

- **Another agent's state.** Two gateways sharing one home share one
  `auth.json` and one dotenv, and a container serves one credential, so
  whichever started last owns the chat. One agent per home volume, always.
- **The operations vault** (`~/hermes-vault`) — compiled guest conversations and
  property access facts, door and keypad codes among them. Not in this image.
- **Hostex and Seam.** No PMS access, no lock control — those belong to the
  rentals agent and reach a different person's property.
- **Anyone else's Mac.** `mcp_servers` is the base image's seed, not this
  repo's, so there is no MCP block here for a sibling's to arrive in by
  copy-paste.

## Bring-up

The agent writes its own `ld/config.json` from the owner's first DM:
`runtime/SOUL.md` tells it that a config missing any of `family.owner.name`,
`weather.location`, `sports.followed` or `calendar.sources` means onboarding is
unfinished, and `ld-setup/SKILL.md` is what it runs then — a conversation, not a
form, drafting each answer through `write_config.py` as it lands and discovering
the calendars from the Mac through Latch once it is connected.

**The wall is opt-in.** Only if the owner takes that offer does the rest follow:
`ld-wall-setup` mints the wall's token, brings the Pi up, and registers the
crons. The Pi keeps its own `/api/message` server on the household LAN, which
the agent reaches only through Plow Latch on the owner's Mac: it ships the token
there in two files (never through chat), runs the two install lines on the Pi
over `ssh` from the Mac, and every card after that is delivered the same way
(`ld-shared/references/latch-delivery.md`) — so cards refresh only while the Mac
is awake with Latch running. No Mac: the lines are texted to the owner to type.

So the whole of bring-up is two steps in this order: activate — the owner texts
the code from their phone, which is what writes the credentials the agent boots
with — and then `docker compose up`. The agent's first DM to them is the rest of
it; their reply is the onboarding conversation, not a third step.

`TZ` is not one of the things you pass in. The container reads
`family.timezone` from `ld/config.json` at boot and starts on `UTC` when there
is none, which every first boot is — onboarding has not asked yet. Once the
owner answers, the zone is written and takes effect on the next restart; until
that restart the cron registration refuses, by design, rather than scheduling a
household's cards against a zone the container is not running.

`ld-shared/scripts/ld_config_gate.py <path>` is the single definition of a valid
config: empty output is a pass, anything else is the list of what is wrong (its
exit code is always 0). The file stays writable by the agent's own runtime for
the rest of its life, and the gate catches only structurally-wrong config, not a
structurally-valid rewrite from a prompt-injected turn. No mitigation for that
gap exists yet.

**A turn costs you the exit code.** `write_config.py`, `mint_wall_token.py` and
`register_crons.py` all refuse loudly with a non-zero exit, but a chat turn
returns the *turn's* status. The skills therefore hold the agent to
`ld-dashboard`'s contract — paste each script's output verbatim with its exit
status, and treat the phase as unfinished until it has. `refusing`, `WARNING` or
`PAUSED` anywhere in that output means bring-up did not finish.

The crons are registered by the agent itself, as the gateway's own uid —
deliberately not by an exec from outside. A `docker exec` lands as **root**, and
on a fresh agent `jobs.json` does not exist yet, so a root-run registration
creates the schedule root-owned and the gateway can then never pause, resume or
remove anything in it.

## What only the owner can do

**Texting the activation code.** Whoever's phone texts the code back *is* the
account binding — a code texted by anyone else binds the agent to the wrong
account and spends a one-time activation doing it. The code and the number both
come back from `POST /v1/auth/activate` when activation runs, so neither can be
shared ahead of time: it is a two-person moment, not a step to hand off.

The mechanics — running activation, what to do when a code expires — are in
[`plow-agents`](https://github.com/plow-pbc/plow-agents), not here.

## What the operator can see

The agent's Plow token is the two-line file the host drops at
`/var/lib/plow/credentials`, root-owned and unreadable to the agent, and first
boot publishes it into the container environment rather than into any file the
agent can read. Through it, that person's mailbox is reachable from that host.
Whoever can read that file, or exec into the container as root, holds it. This
is stated rather than left implied — it is a fact an owner should know before
they text the activation code, not one to discover afterwards.

The agent's own dotenv is a different file and a smaller one: `ld/.env` holds
what the agent records during setup — the wall's endpoint and token, the Pi's
login, the delivery mode — and no `PLOW_*` name appears in it. The relay is not
in it either: that is the agent's own, out of the environment first boot
published.

## No connectors, and what that costs

`plow-connectors` — the skill that reached this owner's Gmail, Google Calendar
and Slack with the gateway's own `PLOW_AGENT_TOKEN` — is not installed, and
nothing here replaces it.

That is a deliberate trade. The two producers that need no account —
`ld-weather` (NWS) and `ld-sports` (ESPN) — work immediately, as does
`ld-morning-triage`, read from the Mac's iMessage DB through Latch, and the
three calendar producers — `ld-morning-updates`, `ld-weekly-digest`,
`ld-calendar-nudge` — whose calendar reads go through Latch's vendored `gog`.
`ld-dashboard` carries all six schedules.

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

The policy and tool live in the `hermes-plow-chat` plugin, which the base image
pins and bakes in, and the gateway's config is the base image's too. Adding
group prompts or another trust flag to this repo would create a second policy
path that the dashboard cannot update.

Before restoring an existing agent, migrate its config in place: copy
`calendar_nudge.owner_identities[0]` to `calendar.account` without rebuilding
the object or changing any other preference, then require an empty result from
`ld_config_gate.py`. The three calendar skills add that account to their exact
gog argv; manually run and approve each new 1-day, 3-day and 7-day gather shape
once through Latch before relying on the unattended crons. The calendar strip
adds a fourth — its `/api/calendar` curl — for the same reason: it ticks with
nobody there to answer an approval card. `ld-wall-setup` approves it by running
the strip once with the owner present, and an agent already carrying
`/var/lib/hermes/ld/setup-complete` skips that phase, so one set up before the
strip landed needs the step once, by hand, with its owner at their Mac:

```sh
s6-svstat /run/service/life-calendar-feed    # the schedule, supervised beside the gateway
/opt/hermes/.venv/bin/python3 /var/lib/hermes/skills/ld-shared/scripts/calendar_feed.py
```

The image's own path rather than this checkout's, and run as `hermes`, so this
cannot drift from what the service actually ticks — which is the whole point of
approving it.

## Layout

```
runtime/        SOUL.md: the persona and the setup rule. No config.yaml -- the
                model, plugins and mcp_servers are the base image's
image/          the s6 service definition for the calendar strip's schedule
ld-weather/     the NWS producer; ld-sports/ is the ESPN one
ld-morning-triage/  the iMessage triage producer, read through Latch
ld-morning-updates/ the calendar affirmation producer, gog through Latch
ld-shared/      the POST helper, the ld-config gate, the wire protocol, and
                calendar_feed.py -- the kiosk's calendar strip, no model in it
ld-dashboard/   the six cron schedules, all registered
ld-setup/       first-run onboarding over chat
ld-wall-setup/  the wall, if the owner wants one: token -> Pi over Latch -> crons
tests/          this agent's own contract; the runtime's live in plow-hermes-agent
Dockerfile      this repo's content on the base image
.dockerignore   keeps secrets and stale bytecode out of that build's context
```

## Latch — whose Mac an agent reaches

`mcp_servers.plow` points at the Plow relay, which forwards to Plow Latch on the
owner's Mac: `plow_read_file`, `plow_write_file`, `plow_run_command`,
`plow_browser_*`, `plow_vault` and friends. The relay authorises the connection
and tells the Mac who is asking; the Mac authorises each action, so the approval
surface stays on that machine rather than here.

The credential is the agent's own: first boot asks Plow who this agent is and
publishes the relay it is told about as `PLOW_MCP_URL`, reached with
`PLOW_AGENT_TOKEN` — the same relay the gateway itself uses. Nothing here mints
or holds a per-device pair, and an agent whose owner has no relay switched on
simply has no `PLOW_MCP_URL` and stands down. The token travels in a header,
never in the URL.

**The gateway's config is not in this repo.** Model, plugins and `mcp_servers`
are the base image's seed, read from the copy in the agent's own home, and
nothing here asserts or overrides them — changing any of them is a change to
`plow-pbc/plow-hermes-agent`. The producers do not go through `mcp_servers` to
reach the wall: they read the endpoint and token out of `ld/.env` themselves,
and the unattended one gathers through the relay first boot published. Either
way, nothing here needs an `mcp_servers` entry naming those variables.

## Building the image

```sh
docker build -t life-assistant .
```

The `FROM` is pinned by **digest**. The `base-<sha>` half names the commit of
[`plow-pbc/plow-hermes-agent`](https://github.com/plow-pbc/plow-hermes-agent) it
was built from, and the `@sha256:` half is what the build actually resolves — so
no tag reassignment can substitute different bytes under an existing owner. Bump
both together when moving to a newer base.

Nothing here needs a registry login: the base is public and is fetched
anonymously. A build that fails with `403 Forbidden` on it has an expired
`docker login` for `public.ecr.aws` in the way — those credentials last 12
hours, and the stale one is sent in place of asking anonymously. `docker logout
public.ecr.aws` is the whole of the fix.

## Open

- **Connectors are gone, not unlinked.** This agent installs no
  `plow-connectors`, so Gmail and Slack are unreachable however linked the
  owner's Plow account is. Google Calendar is back — through a vendored `gog`
  behind Latch rather than a connector skill; all three calendar producers ride
  it. See [No connectors, and what that costs](#no-connectors-and-what-that-costs).
