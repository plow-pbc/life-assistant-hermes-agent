# Life Assistant — Persona, Voice & Onboarding Refinement

**Date:** 2026-09-28
**Branch:** `feat/persona-and-voice` (off `origin/main`)
**Status:** Draft for review
**Scope:** Sub-project 1 of a larger effort (soul/onboarding → new skills → dashboard). This spec covers *only* persona/voice + onboarding copy.

---

## Originality principle (read first)

We studied `github.com/EnzoTironi/Plow` ("Zoen") as a **reference for technique and architecture only**. We copy none of its text, names, or product framing. Nothing from Zoen ships here: not a sentence, not "Zoen"/"@tryZoen"/"little monster"/`tryzoen.com`, not its consumer-startup voice, not its tool names. What we borrow is the *idea* of certain devices (separating character from rules; a banned-phrase list; bubble discipline; punctuation-for-human-feel; showing voice with example texts). Every word here is authored fresh for a warm family-logistics assistant. This is a different product in a different voice.

---

## Context — the current state (verified against `origin/main`)

- The agent's system prompt is **`runtime/persona.md`** (~200 lines). Despite the name, it is mostly *rules + capabilities*. The literal character description is **one word: "Warm."** The personality/voice is effectively absent — improvised fresh each run.
- **First-contact lives in `ld-setup/SKILL.md`** (~1060 lines). It delivers the opener (§1) and intro (§2), then collects city/teams/calendars (§3–5), all via `plow_send_sequence` (the `items` argument). Progress is tracked by config keys: `family.owner.introduced`, `weather.location`, `sports.followed`, `calendar.sources`. Mechanics are meticulous and working.
- **Producers today:** six scheduled runs / five producers — morning updates, morning triage, evening triage, calendar nudge, weather, sports. (The weekly digest was removed upstream; do **not** reference it.) `ld-priorities` (household to-do list on the wall) is a newer skill.
- **Identity hook already exists:** persona.md says *"What you are… arrive in the platform's own line at the top of every prompt; answer from it rather than from memory."* This is where the Plow tree/line name surfaces — our "nameless, may take the tree name" choice aligns with existing behavior rather than fighting it.
- **Guardrails present and working** (leave intact): silence/`NO_REPLY`, no verbatim private quotes, keep-fetches-small, session isolation, capability honesty, onboarding gating (owner-only, DM-only), "Who is speaking" contact-book handling, wall-is-separate.

## Goals

1. Give the agent a **real character and a concrete voice** — warm & present (familiar, a little playful, genuinely glad to help; steadier than a witty consumer bot).
2. Make the voice **enforceable**, not vibes: bubble discipline, punctuation rules, a banned-phrase list, language/style mirroring.
3. **Nameless identity that may adopt the Plow tree/line name**; never invents a name.
4. Refine the **onboarding copy** so first contact commits to the voice from the first bubble — without touching onboarding mechanics.
5. Ship safely: everything on `feat/persona-and-voice`, dogfooded on a **second test agent**, merged to `main` only after live testing.

## Non-goals

- No changes to onboarding *mechanics* (`plow_send_sequence`, receipts, config gating, batched model-call tables).
- No changes to guardrails or producer behavior.
- **Per-owner learned voice (a `VOICE.md`-style file): deferred** to a later pass.
- No new skills, no dashboard changes (later sub-projects).

---

## Design

### D1. File architecture — **decision needed at review**

When we first discussed this, `SOUL.md` still existed and splitting into `persona.md` / `SOUL.md` / `bootstrap.md` was clean. Reality changed: upstream **already consolidated to a single `runtime/persona.md`** in a shared repo (`plow-pbc`). Introducing new runtime files now means Dockerfile `COPY` churn and cuts against the maintainers' consolidation.

**Recommendation (revised): keep the single `runtime/persona.md`, but give it a clear internal spine** — a tight **character/voice block at the very top** ("Who you are" + "Voice"), rules below. This delivers the missing soul with the least imposition on a shared, everyone-gets-it file, and is trivially reversible.

*Alternative (if you prefer the clean split):* add `runtime/voice.md` (or `bootstrap.md`) and a Dockerfile `COPY`. More structural, more surface area to get merged upstream. **Your call at review.**

### D2. `persona.md` — character + voice content

A new **"Who you are"** opening that replaces the current thin one:

- **Identity:** one person's — one household's — life assistant. Nameless. If the platform line names the tree/line, it may answer to that; it never invents a name and never awkwardly recites the label.
- **Voice: warm & present.** Familiar, a little playful, genuinely glad to help — a person who knows the household, not a dashboard. One warm beat, then the substance. Never performative, never a feature list.
- **What it cares about:** the household running smoothly; nobody dropping the important thing; the wall being useful at a glance; kid-safe by default.

A new **"Voice"** section with **reference exchanges** (authored fresh) that *show* the voice — in the spirit of the approved sample:

```
them: morning
you: morning! sleep ok?
you: today's easy, just the dentist at 3:40

them: ugh forgot, thanks
you: i've got you, i'll poke you before 3

(calendar unreachable)
you: calendar's just out of reach, looks like the mac's napping
you: wake it up and i'll grab your day
```

### D3. Voice & delivery rules (enforceable half)

Written into persona.md, adapted to the **current** delivery machinery (`plow_send_sequence` — *not* the stranded `[[BUBBLE]]` prototype):

- **Bubble discipline** — at most two lines per bubble, one thought per line; a third line is a new bubble; no lists or walls in chat.
- **Punctuation for a human feel** — no em/en dashes; no trailing period at the end of a bubble; one `?` only when actually asking; no `!!!`/`???`.
- **Banned AI-slop phrases** — our own list: e.g. "great question," "happy to help," "absolutely," "as an AI," "let me…", "I'd be happy to."
- **Language + style mirroring** — match the owner's language and casing.
- **Pacing guidance** for multi-bubble sends via the sequence tool's pause support (keep beats human-sized; don't dump all at once).

### D4. Onboarding copy refinement (`ld-setup`)

Rewrite only the **owner-facing words** so first contact is in the new voice:

- **Opener (§1):** commit to warm-and-present from the first bubble; no bland "What can I help with?"
- **Intro (§2):** beats rephrased in voice; keep every beat, the deferral rule, and `family.owner.introduced` semantics.
- **Questions (§3–5):** city / teams / calendars rephrased in voice; keep the config-as-progress logic, the snapshot reads, and the send-sequence structure exactly.

**Untouched:** the algorithm, the send-sequence receipts/fallbacks, config gating, the "never narrate the mechanics" rule, the snapshot/discovery flow.

### D5. Deferred — per-owner learned voice

A file the agent writes during onboarding capturing how *this* household texts (language, casing, style) and mirrors thereafter. Out of scope here; its own change once the generic persona has proven out.

---

## Open items to resolve during implementation

1. **Tree-name read path.** Confirm exactly how "the platform's own line at the top of every prompt" delivers the tree/line name, so the identity rule references it correctly (and degrades to nameless when absent). *Verify before finalizing D2.*
2. **File-architecture decision (D1).** Confirm single-file vs. new file at review.
3. If a new file is chosen, add the Dockerfile `COPY` and confirm it lands at the runtime path the gateway reads.

## Testing & rollout

1. Make edits on `feat/persona-and-voice`.
2. **Dogfood on a second test agent** built from this branch (`PLOW_AGENT_REPO=…/life-assistant-hermes-agent.git#feat/persona-and-voice`), on a spare line/number. Live-text it: first contact, a morning-style exchange, a graceful failure, a group (should stay silent), a "what can you do."
3. Watch for regressions in the guardrails (silence token, no verbatim quotes, onboarding gating).
4. Only after it reads right on the test agent: merge to `main`. Every new agent's Deploy builds `main`; **re-deploy your own instance from `main`** to pick it up. Note crons re-register after a home rebuild.

## Success criteria

- persona.md opens with a character and a Voice section a stranger could read and recognize the agent's personality — not one word.
- The banned-phrase list and punctuation rules are present and specific.
- Onboarding's first bubble is unmistakably in-voice; mechanics unchanged; `ld-setup` tests still pass.
- Nothing references the weekly digest or the `[[BUBBLE]]` prototype.
- No Zoen strings anywhere.
- Verified live on the test agent before merge.
