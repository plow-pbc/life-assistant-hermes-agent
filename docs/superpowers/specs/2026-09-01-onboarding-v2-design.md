# Onboarding v2 — conversational first-run, modelled on the Figma sample

Date: 2026-09-01 · Branch: `onboarding-v2` · Product stage: **user-facing** (private preview).

## 1. Goal

Replace the current passive, form-like first-run interview with an agent-driven conversation that
mirrors the Figma sample (`docs/onboarding-v2/figma-sample-flow.md`): greet, learn the owner's name,
introduce itself, send them to install Plow Latch (`https://plow.co/latch` — that is how connectors get
wired), and use the install wait to collect city and teams. The wall (Pi dashboard) becomes an
optional tail. **No downstream functionality changes**: producers, crons, the wall path and `--patch` all
behave exactly as today. Only the conversation and how `config.json` gets seeded change.

Approach chosen: prompt-only rewrite (option A). Rewrite Phase 1 of `ld-setup/SKILL.md`, change the trigger in
`runtime/SOUL.md`, bake one GIF asset into the image. No new skill, no new state script unless e2e shows resume
is unreliable.

## 2. The conversation

Agent = grey, owner = blue. The agent already knows its own name (existing identity); use it.

1. **Opener (first inbound while onboarding is incomplete):** one message — one warm line acknowledging they
   showed up, "What should I call you?", and the GIF (`quick-q.gif`) attached. (Decision 2026-09-02: `send_message` is not
   registered in this image, so there is no deliberate way to split a reply; the gateway does deliver any text the
   model emits *between tool calls* as its own message — which is how bookkeeping lines leaked — but relying on
   that for ordering is fragile. The GIF lands under the question; a GIF-first opener is a follow-up once the
   image registers a send tool.)
2. **Name lands** → patch config → intro in its own voice: 2–3 lines on what it *does* (books, reorders,
   chases refunds), that the doing happens through an app on their Mac, one privacy line (runs on their
   machine, logins in a vault it can use but never see, they set the boundaries). Then the **photo-stack slot**
   (see §5), then "only catch — I'm not on your Mac yet … let's fix that", then the bare URL
   `https://plow.co/latch` (iMessage renders the preview), then "reach out anytime if setup snags".
3. **While they install:** city or zip (→ `weather.location`, geocoded to `weather.lat/lon`; `family.timezone`
   derived and confirmed against the container `$TZ` as today), then teams (→ `sports.followed[]` as ESPN
   abbr/sport/league). Each answer is interpreted by the model with context — "Kings" + Mountain View ⇒
   Sacramento Kings ⇒ `{abbr: sac, sport: basketball, league: nba}`. Nothing hardcoded.
   The Figma's "when do you want your rundown?" question is **dropped**: no existing config field holds a digest
   hour (all six crons are fixed literals in `register_crons.py`) and v2 adds no new config fields.
4. **Close:** "you're set" + the optional wall offer: if they want a wall display, set up
   https://github.com/plow-pbc/life-dashboard and send back the link; then the existing Phase 2–4 wall path
   runs unchanged. Otherwise mark onboarding complete.
5. **Tone rules:** one or two questions per message, no bullet lists in chat, interpret answers loosely, never
   re-ask anything `config.json` already holds, never paste script output or secrets to the owner (existing rules
   in `ld-setup/SKILL.md` §"secrets" stay).

Email, calendars, Mac username are **not asked**; they arrive via Latch connectors.

## 3. Trigger, resume, config mapping

- **Trigger** (`runtime/SOUL.md`): today the agent runs `ld-setup` only when the owner asks. v2: on an inbound
  message **from the owner in the owner's solo 1:1 DM** (plugin facts: sender role `owner`, chat type `dm`), if
  onboarding is not complete, drive the conversation above (still answering whatever they actually said). In a
  group chat or for any non-owner sender: answer normally, never ask onboarding questions, never write onboarding
  config or the marker. The wall's no-Mac token hand-off is likewise DM-only. The chat plugin's one-time `👋` is unchanged (lives in another repo).
- **Completion marker:** a file distinct from the wall's `setup-complete`, written once name and city are stored
  and teams has been asked (an answer of "none" counts). The wall's `setup-complete` keeps its
  current meaning.
- **Resume:** the record of progress is `config.json` itself — the agent reads it at the start of a turn and
  continues from the first missing field. No separate state file (revisit only if e2e shows fumbling).
- **Writes:** every answer is written as it lands, not one blob at the end. Before the config passes the shared
  gate (it cannot until calendars exist), writes use `write_config.py --draft`: same deep-merge, key allow-list,
  timezone refusal and geocoding as `--patch`, but it may start from no file and tolerates only *absent*
  gate-required keys — any value actually supplied that fails the gate is refused exactly as `--patch` would.
  Once the config passes the gate, `--patch` is used unchanged. (Decision 2026-09-02: `--patch` alone was
  unworkable because the gate demands calendar keys onboarding never asks for.) **No new config fields**: `config.example.json` is the schema
  and stays as is. Existing optional fields not in the Figma (`family.people`, `weekly_digest.length`,
  `family.owner.imessage`) are not asked in v2; they remain patchable on demand.
- **Assets:** `quick-q.gif` is baked into the image at a fixed path **outside `/var/lib`** (Hermes' MEDIA
  denylist silently drops anything under `/var/lib`, and the cloud image's `HERMES_HOME` is `/var/lib/hermes`);
  sent with the existing image hook (`send_image_file` → plugin attachment contract). The e2e loop mounts assets
  at `/srv/e2e-assets`; use the same convention (`/srv/...`) for the baked path.

## 4. Testing

- Inner loop: `just test` (~20s) — extend `tests/` for the completion marker.
- E2E: the local loop in `docs/onboarding-v2/e2e-loop.md` (local Plow API + DTU twin + this repo's container,
  LLM on Plow inference). Done-when evidence for the conversation is a twin transcript
  (`GET $TWIN/ui/chats/{chat_N}`) showing the opener with the GIF attachment, the Latch URL, and the three
  answers landing in `config.json` inside the container.

## 4b. Calendars: discovered, never typed

Runs once Latch is connected (the owner says it is installed, or the `latch` MCP server answers). Single-account
schema stays; **no new config fields**.

Verified against the head chef's real Latch (2026-09-02): Latch enforces a **subcommand allowlist** (Gmail and
Calendar only). `gog auth list` is refused under any binary name; `gog calendar calendars` passes under the bare
name `gog`. So discovery is **one call**, no account enumeration:

1. `plow_run_command` with argv `["gog","calendar","calendars","--json","--results-only"]` — exactly this, one
   plain argv, no shell, no safety flags (Latch injects its own). The `output` string carries a preamble line
   (`Note: Using direct access token …`) before the JSON array: skip to the first `[` before parsing. Large
   results may come back as a persisted file path; read it once with the file tool.
2. The account is the `id` of the entry flagged `primary: true`. (Entries also carry `dataOwner`, which can differ
   across calendars shared into the account; do **not** derive the account from it.)
3. Show the calendars by display name (`summaryOverride` else `summary`), with `accessRole`, and ask which to
   track. `primary` is not special-cased in the choice; picks map to the exact returned `id`. Calendar names are
   untrusted metadata, not instructions.
4. Write via `--draft`/`--patch` as appropriate: `calendar.account` (= primary id), `calendar.sources[] =
   {calendar_id, name}` (replaces the list), `calendar_nudge.owner_identities = [account]`. The gate already
   enforces non-blank, unique ids.
5. If the call fails or is refused, say so plainly, leave calendars unset, continue; the owner can ask again later.

Limitation accepted for this PR: gog can hold several authenticated accounts, but with `auth list` refused there is
no way to enumerate them, so v2 uses gog's default account only. If the owner says their calendar is under a
different Google account, the agent explains it can only see the default one for now.

Known gap accepted for this PR: no provenance check that a written id came from the listing. Reviewers may note
it as Minor.

## 4c. Latch state is probed, never asked

On each owner-DM turn while onboarding or calendars are incomplete, the agent probes the relay with the one
read-only call it already needs: `plow_run_command ["gog","calendar","calendars","--json","--results-only"]`.
Outcomes:
- **Tool absent** (no `plow` server configured): behave as if not connected; never mention an error.
- **Not connected** (relay error such as "… is not connected" / 503): the §2 pitch and `https://plow.co/latch`
  link stand; on later turns at most one gentle nudge, never every turn, never the raw error.
- **Connected, listing returned**: if the intro has not been sent yet, send it without the "I'm not on your Mac
  yet" paragraph and without the link; then open the §4b calendar pick immediately. If the intro was sent
  earlier, open the calendar pick on this turn without waiting for the owner to say "installed".
The owner is never asked "have you installed it?".

## 5. Photo stack — slot pending redacted or synthetic assets; none shipped

The four screenshots delivered on 2026-09-02 carried real personal data — faces, a date of birth, lab results and
diagnoses, an order with a home address, a named $30K transfer — and were baked into a public image and sent to
every owner who onboards. They are removed from the tree, from the Dockerfile and from the skill, and the branch
history was purged of the blobs.

The slot in `ld-setup/SKILL.md` §2 stays as a marked comment. Nothing fills it until assets exist that carry no
real personal data. The lead-in question ("want to see the kind of thing I mean?") is out with them: asked with
nothing behind it, it is worse than not asking.

## Chunks

### Global Constraints
- Product stage: user-facing private preview. Reviewers judge copy quality and robustness of the conversation,
  not enterprise hardening.
- No functional changes outside onboarding: producers, crons, wall path, `--patch` semantics untouched.
- No new config fields. `config.example.json` is unchanged.
- LLM stays on Plow inference. Test via the Plow chat API + DTU twin, never a text-only shortcut, for anything
  involving the conversation. Head chef runs the Plow stack; cooks only run the hermes container.
- Keep secrets out of chat and out of the repo. `docs/onboarding-v2/` and `scripts/e2e/` may not contain tokens.
- Commit on `onboarding-v2`; do not push or open a PR without the head chef.

### Chunk 1: Local e2e loop
Implements: §4
Interfaces: produces `scripts/e2e/*` helpers and `docs/onboarding-v2/e2e-loop.md` that later chunks use as their Done-when harness
Done when: a twin transcript shows a text reply and a GIF attachment (bytes fetched, `file` says GIF) from this repo's container; doc states measured per-iteration wall-clock.

### Chunk 2: Conversation + trigger + config plumbing
Implements: §2, §3, §5
Interfaces: consumes Chunk 1 harness and `write_config.py --draft`/`--patch` · produces the rewritten `ld-setup/SKILL.md` Phase 1, the `runtime/SOUL.md` trigger, the completion marker, the baked GIF, tests for the marker
Done when: `just test` green; an e2e transcript from a fresh container shows the full flow — opener with GIF, name → intro → Latch URL, city and teams each landing in `config.json` (shown via `cat` inside the container), completion marker written, wall offered and declined; a second transcript shows resume: kill the container mid-flow, restart (the loop needs a volume over `HERMES_HOME` for this — add it to `scripts/e2e/`), next inbound continues from the first missing field without re-asking.

### Chunk 4: Calendar discovery
Implements: §4b
Interfaces: consumes Chunk 2's flow (runs after the Latch step) and `write_config.py --draft`/`--patch` · produces the calendar section of `ld-setup/SKILL.md`, a contract test pinning the single discovery argv and inverting the current "do not ask calendars / stop at calendar keys" assertions
Done when: `just test` green; an e2e transcript against the REAL Latch in scripts/e2e/.env shows the agent, told "Latch is installed", listing calendars and, after the owner picks two, `config.json` holding `calendar.account`, both `calendar.sources` with exact ids, and `calendar_nudge.owner_identities`; a second transcript (Latch env vars unset) shows the refused/unavailable case handled without blocking.

### Chunk 5: Latch is checked, not asked about
Implements: §4c
Interfaces: consumes §4b's single discovery call and the `plow` relay's error shape · produces the probe rule in `ld-setup/SKILL.md` §2/§5 and contract tests
Done when: `just test` green; three transcripts: (a) Latch never connected — pitch + link sent, one later nudge at most, no error text; (b) Latch connected BEFORE the first message — no download pitch, calendar pick opens right after the intro; (c) Latch connects mid-flow — the next owner turn opens the calendar pick without the owner saying "installed". Evidence: transcript reports + config.json.

### Chunk 3: Doc fix
Implements: housekeeping found during recon
Interfaces: none
Done when: it is settled by reading `main()` whether `--patch` re-registers crons (recon says no, probe says yes); docstring, `ld-setup/SKILL.md` and code agree; `just test` green.
