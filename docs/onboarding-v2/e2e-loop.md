# The local e2e loop

Edit a skill in this checkout, restart one container, text the agent, read what
it said back — including attachments. **~21 seconds per iteration** end to end,
measured.

Everything runs against a local Plow API and the LINQ twin, which plays the
phone network. No production, no real handset, no Mac relay.

```
you ──POST /ui/inbound──> twin ──signed webhook──> Plow API ──ws──> agent container
                            ^                                            │
     GET /ui/chats/{chat_N} │<────── POST /v1/chats/{cht}/messages ───────┘
```

## What is yours and what is not

The Plow stack (`api`, `dtu-linq`, `db`) is **shared and brought up elsewhere**.
These scripts read it and talk to it; none of them start, stop or reconfigure
it. The only thing this loop owns is the agent container.

Reach it by OrbStack's names, never by a port:
`https://api.plow.orb.local`, `https://dtu-linq.plow.orb.local`. Host ports move
with the worktree and the compose project; the names do not. The one place a
port is unavoidable (the upload shim, below) derives it from the plow
checkout's `.plow-dev-env` rather than hard-coding it. That checkout is assumed
to sit in the conventional sibling layout (`../../plow/main` from this repo);
set `PLOW_MAIN` if yours is somewhere else.

## Setup, once

```sh
scripts/e2e/activate.sh      # mint the agent credential + its chat -> scripts/e2e/.env
scripts/e2e/build.sh         # pull the pinned base, build life-agent:e2e
scripts/e2e/run-agent.sh     # stage the skills, start the container
```

`activate.sh` runs the **real activation flow** — the same one `agent-mgr
activate` drives and an owner completes from their phone — against the local
stack, where the twin plays the phone network so nobody has to text anything.
It returns both halves the agent needs, the scoped bearer and the chat it is
granted, so nothing is minted by hand and no row is written directly.

It writes `scripts/e2e/.env` (gitignored, mode 600) with the token, the Plow
chat uid (`cht_…`), the twin's own thread id (`chat_N`), and the two phone
numbers. `env.example` documents every key. Re-running it **merges**: the keys
activation owns are replaced and every other line survives, so a `LATCH_MCP_URL`
and its token — things no activation can regenerate — are safe to keep there.

**LLM credit needs no seeding.** `POST /v1/chat/completions` refuses without a
positive balance (`402 Insufficient balance`, `api/plow/llm/router.py`), and a
user created on the activation path is created with a $100 welcome credit
(`WELCOME_CREDIT_USD`, `api/plow/channels/linq/routes/webhook.py`). Verify with
`select amount_usd from credits where user_id = …` if a turn ever 402s.

The model is **Sonnet through the Plow proxy** — the base image's own seed
config (`anthropic/claude-sonnet-5`, `provider: plow`), not this repo's
`runtime/config.yaml`. The Dockerfile bakes `runtime/SOUL.md` and the `ld-*`
skills but **not** `runtime/config.yaml`, so the image never sees the fleet's
`gpt-5.6-sol`/`openai-codex` choice; running that here would need a Codex
`auth.json` this machine does not have. Turns are billed to the local stack's
own Anthropic key.

## Per iteration

```sh
# edit ld-*/SKILL.md, a script, or runtime/SOUL.md
scripts/e2e/run-agent.sh                       # ~10s
scripts/e2e/send.sh "I want to set up my life dashboard."
scripts/e2e/await-reply.sh                     # ~10s
scripts/e2e/transcript.sh
```

No rebuild. `run-agent.sh` re-stages the skills and recreates the container on
top of them, so the only thing an edit costs is a restart.

| script | what it does |
|---|---|
| `run-agent.sh [--fresh] [--latch]` | stage skills, (re)start the container, wait for the plow_chat websocket |
| `send.sh "text"` | text the agent as the owner's handset, through the twin |
| `await-reply.sh [timeout] [--since N]` | block until the next outbound message lands; prints the latency |
| `transcript.sh` | the conversation as the handset sees it, media parts included |
| `fetch-attachment.sh [out]` | download the latest attachment and run `file` on it |
| `send-file.sh <host path>` | push a file into the chat with no model turn, via Plow's own media contract |
| `logs.sh [n]` | the gateway's log **inside** the container |
| `build.sh` / `down.sh` | rebuild the image / remove the container |
| `sync-skills.sh` | staging only; `run-agent.sh` calls it |

### Why the skills are staged rather than mounted straight

Every path in this repo's content is written against the fleet's `HERMES_HOME`
(`/opt/data`); this image's is `/var/lib/hermes`, and the Dockerfile does that
substitution to its own copy. A raw bind mount of the checkout would put
`/opt/data` paths back into a container that has no such directory, and every
script a SKILL.md names would fail with "No such file or directory". So
`sync-skills.sh` copies the tree into `scripts/e2e/staging/` with the same sed
and the same mode rules, and the container mounts that, read-only.

## Proof it works

Text, agent→owner:

```
2026-09-02T00:13:02Z  owner  I want to set up my life dashboard.
2026-09-02T00:13:12Z  AGENT  ... Let me check the dashboard's current setup status before we start.
2026-09-02T00:13:14Z  AGENT  The dashboard isn't set up yet, so let's kick off proper setup.
2026-09-02T00:13:17Z  AGENT  Great, let's set this up. I'll ask a few quick questions — first two:
                             1. What should I call you?
                             2. What's the Google account email whose calendar you live by?
```

That is `ld-setup` Phase 1 running off the mounted skill, reached through
`runtime/SOUL.md`'s gate — the whole path this loop exists to exercise.

Image, agent→owner (a model turn emitting a `MEDIA:` directive):

```
owner  Send me the GIF at /srv/e2e-assets/quick-q.gif as an attachment, using the MEDIA: convention.
AGENT  Here's that GIF:
AGENT  [media image/gif quick-q.gif] http://dtu-linq:8091/files/twin-att-2/quick-q.gif

$ scripts/e2e/fetch-attachment.sh /tmp/e2e-quick-q.gif
https://dtu-linq.plow.orb.local/files/twin-att-2/quick-q.gif
-rw-r--r--  403039  /tmp/e2e-quick-q.gif
/tmp/e2e-quick-q.gif: GIF image data, version 89a, 320 x 320
```

403039 bytes is `docs/onboarding-v2/assets/quick-q.gif` to the byte.

`run-agent.sh` mounts `docs/onboarding-v2/assets` at `/srv/e2e-assets`. That
directory arrives with the onboarding work and is not part of this change; until
it lands, the mount is an empty directory and the outbound-image check above has
nothing to send. Everything else in the loop runs without it.

## Gotchas

Each of these cost a debugging round; none of them announce themselves.

- **`HOME` must be set, or every turn fails.** `setpriv` keeps the caller's
  environment, so without it the agent runs with root's `HOME=/root`. A turn
  resolves its context directory from `HOME` and walks up looking for a `.git`;
  from `/root` that `stat` is denied to uid 10000 and the owner gets
  *"Sorry, I encountered an unexpected error."* with the real cause only in
  `logs/agent.log`. The account's own passwd home is `/opt/data` — the fleet's
  `HERMES_HOME`, absent from this image — so `entrypoint.sh` names
  `/var/lib/hermes` explicitly. `WorkingDirectory=/opt/hermes` matters for the
  same reason and is set the same way.

- **Nothing under `/var/lib` can be sent as media.** Hermes' media denylist is
  `/etc /proc /sys /dev /root /boot /var/log /var/lib /var/run`, and this
  image's entire `HERMES_HOME` is `/var/lib/hermes`. A `MEDIA:` path inside the
  home is dropped with *"Skipping unsafe MEDIA directive path"* and the reply
  arrives as text only — the model looks like it ignored you. Only the Hermes
  cache roots under the home are allowlisted past it, so generated images do
  deliver. This loop mounts the design assets at `/srv/e2e-assets` for that
  reason. On the fleet, where `HERMES_HOME` is `/opt/data`, the denylist does
  not bite — it is specific to the cloud image's layout.

- **The twin's upload URL is browser-facing.** Plow's media contract hands the
  client an `upload_url` it must PUT to verbatim; the twin fills it from
  `LINQ_TWIN_UPLOAD_BASE_URL`, which is `http://localhost:<host port>` — right
  for a browser on the Mac, unreachable inside a container, where the send dies
  with `Cannot connect to host localhost:<port>`. `upload-shim.py` forwards
  that exact address inside the container to the twin's host port, so the
  shared stack needs no reconfiguration.

- **`hermes send` cannot reach plow_chat.** It refuses out of process —
  *"No live adapter for platform 'plow_chat' … the platform plugin must
  register a standalone_sender_fn"* — so `send-file.sh` walks the Plow media
  API directly instead.

- **`docker logs` shows only the startup banner.** Everything after it goes to
  `$HERMES_HOME/logs/gateway.log` inside the container; `logs.sh` reads that.
  A readiness check that greps `docker logs` waits out its whole timeout on a
  container that came up fine.

- **The image is `linux/amd64` on an arm64 Mac**, so every run is emulated.
  That is most of the ~10s startup and some of the turn latency.

- **Activation can wedge on a stale chat.** `/activate` picks a line at random,
  and if that line already carries a chat owned by another account with the
  same member roster, provisioning raises `CrossOwnerCollisionError`, the
  redeem stays `pending` forever, and the twin retries the failing webhook in a
  loop. `activate.sh` randomizes the handset to dodge it; if it still fails,
  pass an unused number (`scripts/e2e/activate.sh +15557650123`).

- **Twin thread id ≠ Plow chat uid.** `/ui/chats/{id}` wants `chat_N`;
  `PLOW_HOME_CHANNEL` wants `cht_…`. Both are in `.env`.

- **`PLOW_API_BASE` must not end in `/v1`** — the plugin appends it, and a
  suffix here yields `/v1/v1/...` and 404s on every call.

- **The home outlives the container, and that is deliberate.** A named volume
  (`life-agent-e2e-home`) is mounted over `HERMES_HOME`, so sessions, memory and
  whatever `ld-setup` wrote to `/var/lib/hermes/ld` survive a restart — which is
  the only thing that makes resume testable, since onboarding's record of
  progress is `ld/config.json`. It must be a *named* volume, not a host bind:
  docker seeds an empty named volume from the image's own contents on first use,
  so `config.yaml`, the plugins and the skills skeleton survive being mounted
  over, where a bind would hide them and the gateway would not boot. The cost is
  that seeding happens once — after an image rebuild that changes anything under
  the home, run `run-agent.sh --fresh`, which is also how you get back to a
  brand-new, unset-up owner. `down.sh` keeps the volume on purpose.

- **The readiness check counts, it does not match.** `gateway.log` lives on that
  volume and therefore survives the container, so a bare grep for
  `websocket connected` matches the *previous* run's line and returns instantly,
  reporting a gateway that has not started and printing the last run's output as
  if it were this one's. `run-agent.sh` samples the count off the volume before
  it starts anything and waits for the count to rise.

- **`await-reply.sh` needs the baseline `send.sh` took.** The agent sometimes
  answers in under a second; a baseline read after the send already counts that
  reply, so the wait runs to its full timeout and calls a working turn a
  failure. `send.sh` writes the pre-send count to `.pending-reply` and
  `await-reply.sh` consumes it — reading it without deleting it would make the
  next wait return instantly on this one's reply. Calling `await-reply.sh` with
  no preceding `send.sh` warns and counts from now; `--since N` is the way to be
  exact.

## Not covered

- **The Pi and the cards.** `ld-setup`'s later phases reach the owner's Pi over
  the household LAN, which does not exist here. Latch does, but only if you give
  it one: `run-agent.sh --latch` wires the relay named in `.env` into the
  container as the `plow` MCP server. It is opt-in and off by default, because
  the device on the other end is a real person's Mac and a run that does not
  need it should not be able to touch it. Without `--latch`, the loop exercises
  Phase 1 (the interview and `write_config.py`) and stops where the first Latch
  call would be.

  **Opt-in means every run, not just the first.** `config.yaml` lives on the
  home volume and outlives the container, so a server written by one `--latch`
  run would still be there for every plain `run-agent.sh` after it. The
  entrypoint therefore rewrites that config on *every* start and takes the
  `plow` server back out whenever the credentials are absent. `--latch` with an
  empty `LATCH_MCP_URL` or `LATCH_MCP_TOKEN` refuses to start rather than
  coming up looking armed.

  The server must be named **`plow`**, not `latch`: tool names derive from the
  server name, and every `ld-*` SKILL.md calls `plow_run_command` /
  `plow_write_file`. A server called `latch` would expose `mcp__latch__*` and the
  skills would be naming tools that do not exist.
- **Owner→agent images.** The twin has `POST /ui/inbound/media` and the plugin
  downloads inbound attachments into Hermes' media cache, but this loop has not
  exercised that direction.
- **Typing indicators.** The plugin drives them automatically and the twin
  stores the state (`GET /ui/chats/{id}` carries a `typing` block, and the
  twin's inbox at `/` renders it live), but nothing here asserts on them.
