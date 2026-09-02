# Onboarding v2 — conversational first-run, modelled on the Figma sample

Date: 2026-09-01 · Branch: `onboarding-v2` · Product stage: **user-facing** (private preview).

## 1. Goal

Replace the current passive, form-like first-run interview with an agent-driven conversation that
mirrors the Figma sample (`docs/onboarding-v2/figma-sample-flow.md`): greet, learn the owner's name,
introduce itself, send them to install Plow Latch (`https://plow.co/latch` — that is how connectors get
wired), and use the install wait to collect city, rundown time and teams. The wall (Pi dashboard) becomes an
optional tail. **No downstream functionality changes**: producers, crons, the wall path and `--patch` all
behave exactly as today. Only the conversation and how `config.json` gets seeded change.

Approach chosen: prompt-only rewrite (option A). Rewrite Phase 1 of `ld-setup/SKILL.md`, change the trigger in
`runtime/SOUL.md`, bake one GIF asset into the image. No new skill, no new state script unless e2e shows resume
is unreliable.

## 2. The conversation

Agent = grey, owner = blue. The agent already knows its own name (existing identity); use it.

1. **Opener (first inbound while onboarding is incomplete):** one warm line acknowledging they showed up,
   the GIF (`quick-q.gif`), then "What should I call you?"
2. **Name lands** → patch config → intro in its own voice: 2–3 lines on what it *does* (books, reorders,
   chases refunds), that the doing happens through an app on their Mac, one privacy line (runs on their
   machine, logins in a vault it can use but never see, they set the boundaries). Then the **photo-stack slot**
   (see §5), then "only catch — I'm not on your Mac yet … let's fix that", then the bare URL
   `https://plow.co/latch` (iMessage renders the preview), then "reach out anytime if setup snags".
3. **While they install:** city or zip (→ geocode → timezone + weather coords, patch), rundown time = hour the
   morning digest goes out ("most folks say 7", patch), teams (patch). Each answer is interpreted by the model
   with context — "Kings" + Mountain View ⇒ Sacramento Kings, "7" ⇒ 07:00 local. Nothing hardcoded.
4. **Close:** "you're set" + the optional wall offer: if they want a wall display, set up
   https://github.com/plow-pbc/life-dashboard and send back the link; then the existing Phase 2–4 wall path
   runs unchanged. Otherwise mark onboarding complete.
5. **Tone rules:** one or two questions per message, no bullet lists in chat, interpret answers loosely, never
   re-ask anything `config.json` already holds, never paste script output or secrets to the owner (existing rules
   in `ld-setup/SKILL.md` §"secrets" stay).

Email, calendars, Mac username are **not asked**; they arrive via Latch connectors.

## 3. Trigger, resume, config mapping

- **Trigger** (`runtime/SOUL.md`): today the agent runs `ld-setup` only when the owner asks. v2: on any inbound
  message, if onboarding is not complete, drive the conversation above (still answering whatever they actually
  said). The chat plugin's one-time `👋` is unchanged (lives in another repo).
- **Completion marker:** a file distinct from the wall's `setup-complete`, written once name, city and rundown
  time are stored and teams has been asked (an answer of "none" counts). The wall's `setup-complete` keeps its
  current meaning.
- **Resume:** the record of progress is `config.json` itself — the agent reads it at the start of a turn and
  continues from the first missing field. No separate state file (revisit only if e2e shows fumbling).
- **Writes:** every answer is written as it lands via `write_config.py --patch` (deep-merge, re-gated, geocoding
  on `weather.location`), not one blob at the end. If the current config schema has no field for rundown time,
  add one (`digest.hour` or the closest existing convention); nothing consumes it yet, by design.
- **Assets:** `quick-q.gif` is baked into the image at a fixed path under the skill; sent with the existing
  image hook (`send_image_file` → plugin attachment contract).

## 4. Testing

- Inner loop: `just test` (~20s) — extend `tests/` for any schema/gate change (new field, completion marker).
- E2E: the local loop in `docs/onboarding-v2/e2e-loop.md` (local Plow API + DTU twin + this repo's container,
  LLM on Plow inference). Done-when evidence for the conversation is a twin transcript
  (`GET $TWIN/ui/chats/{chat_N}`) showing the opener with the GIF attachment, the Latch URL, and the three
  answers landing in `config.json` inside the container.

## 5. Open slot

Photo stack (4 screenshots of the agent at work) — design is producing them. The skill text leaves a clearly
marked slot after the intro; when files arrive they are baked next to the GIF and sent as images.

## Chunks

### Global Constraints
- Product stage: user-facing private preview. Reviewers judge copy quality and robustness of the conversation,
  not enterprise hardening.
- No functional changes outside onboarding: producers, crons, wall path, `--patch` semantics untouched.
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
Interfaces: consumes Chunk 1 harness and `write_config.py --patch` · produces the rewritten `ld-setup/SKILL.md` Phase 1, the `runtime/SOUL.md` trigger, the completion marker, the baked GIF, any new config field + gate/test updates
Done when: `just test` green; an e2e transcript from a fresh container shows the full flow — opener with GIF, name → intro → Latch URL, city/time/teams each landing in `config.json` (shown via `cat` inside the container), completion marker written, wall offered and declined; a second transcript shows resume: kill the container mid-flow, restart, next inbound continues from the first missing field without re-asking.

### Chunk 3: Doc fix
Implements: housekeeping found during recon
Interfaces: none
Done when: `write_config.py` module docstring no longer claims a patch re-registers crons (matches code and SKILL.md); `just test` green.
