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

- No changes to the onboarding *delivery machinery* (`plow_send_sequence`, receipts, batched model-call tables). We keep all of that. **We do restructure the completion gate and move the sports question — see D6.**
- No changes to guardrails or producer behavior.
- **Per-owner learned voice (a `VOICE.md`-style file): deferred** to a later pass.
- No dashboard/viewer changes (later sub-project). `ld-wall-setup` gains the sports question (D6) but the wall's setup mechanics are otherwise untouched.

## Scope update (2026-09-28)

Mid-design, two things settled that grew the scope beyond copy:
- **File architecture (D1) resolved: keep a single `runtime/persona.md`.** (Mary.) The character + Voice edits are already applied there.
- **Onboarding restructured (D6):** sports leaves general onboarding for wall setup; the completion gate drops from four keys to three. This changes what "onboarding complete" means for *everyone*, so it must be proven on the second test agent before merge.

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
you: Morning! Today's an easy one, just the dentist at 3:40.

them: ugh forgot, thanks
you: Got you, I'll nudge you before 3.

(calendar unreachable)
you: Can't see the calendar right now. Looks like the Mac's asleep.
you: Wake it up and I'll pull your whole day together.
```

**Target voice: "a friend catching up over brunch."** Sentence case. Mostly complete
sentences with contractions, the occasional fragment as a natural beat. Not
all-lowercase/all-fragments (Zoen), not stiff/work-formal. The relaxed middle.
(Mary, 2026-09-28.)

### D3. Voice & delivery rules (enforceable half)

Written into persona.md, adapted to the **current** delivery machinery (`plow_send_sequence` — *not* the stranded `[[BUBBLE]]` prototype):

- **"Friend over brunch" register — sentence case.** Relaxed and conversational, properly capitalized. Mostly complete sentences with contractions, and the occasional fragment as a natural beat. *Not* all-lowercase/all-fragments (the Zoen device we studied), *not* stiff or work-formal where every sentence is complete. The middle. Firm owner preference (Mary, 2026-09-28).
- **Warm, not terse; easy, not stiff.** Short is good; clipped is not. A whole thought, never a telegram — and never a memo.
- **Bubble discipline** — one or two sentences per bubble; a genuinely separate thought gets its own bubble; no lists or walls in chat.
- **No dashes.** No em dashes or en dashes in messages. Use a comma, a colon, a period, or a new bubble instead. (Firm owner preference, Mary 2026-09-28.) Trailing periods are fine; we do not adopt Zoen's no-trailing-period rule.
- **Banned AI-slop phrases** — our own list: e.g. "great question," "happy to help," "absolutely," "as an AI," "I'd be happy to."
- **Language mirroring** — match the owner's language; keep our own relaxed sentence-cased style regardless of how tersely they type.
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

### D6. Onboarding restructure — separate the general assistant from the wall

**Principle:** general onboarding sets up the *assistant*; sports and anything wall-specific belong to *wall setup*. Verified against the code: `calendar.sources` is already multi-calendar (one authenticated account, many calendar ids) and the wall's calendar strip (`calendar_feed.py`) reads *all* of them, so calendars serve both the triage skills and the wall — they stay in general onboarding. `sports.followed` is read only by `ld-sports` (the kiosk sports card) and is **not** in `ld_config_gate.py`; it is only wedged into onboarding as a completion key.

**New general-onboarding shape:**
1. Time zone (the city question — sets `weather.location` + `family.timezone`).
2. Calendars — reworded to invite picking **several** calendars "to keep an eye on" (for the daily/weekly triage), not one. Mechanics of §5 unchanged.
3. Close by **offering the Pi wall** (still `ld-wall-setup`'s job; onboarding just tees it up).

**Completion gate: four keys → three.** New set: `family.owner.introduced`, `weather.location`, `calendar.sources`. Drop `sports.followed`.

**Sports moves into `ld-wall-setup`:** the followed-teams question is asked while standing up the wall (the sports card lives there). `ld-sports` still reads `sports.followed`; with no wall/teams set it simply produces nothing.

**Files touched by D6:**
- `ld-setup/SKILL.md` — reword calendar step (several calendars); remove the teams step (§ teams); drop `sports.followed` from the completion-key logic and the front-matter description; make the close offer the Pi.
- `runtime/persona.md` — update the onboarding-gate key list (drop `sports.followed`).
- `ld-wall-setup/SKILL.md` — add the followed-teams question + write `sports.followed`.
- `tests/test_onboarding_contract.py` (and any config-contract test) — update the expected key set from four to three.
- `ld-shared/references/config.example.json` — no schema change needed (sports stays valid); confirm comments still accurate.

**Risk:** this changes what "onboarding complete" means for every deployed agent. Existing installs already have all keys, so they read as complete regardless; new installs finish one question sooner. Must be proven on the test agent (see Testing).

## Open items to resolve during implementation

1. **Tree-name read path.** Confirm exactly how "the platform's own line at the top of every prompt" delivers the tree/line name, so the identity rule references it correctly (and degrades to nameless when absent). *Verify before finalizing D2.*
2. **File-architecture decision (D1).** Confirm single-file vs. new file at review.
3. If a new file is chosen, add the Dockerfile `COPY` and confirm it lands at the runtime path the gateway reads.

## Testing & rollout

Two layers, and we do **both** before merge (Mary wants this proven thoroughly).

**Layer 1 — automated contract tests (local, fast, run first):**
- Update and run `tests/test_onboarding_contract.py` for the three-key gate; it must pass.
- Run the full suite (`ld_config_gate`, config-contract, cron-spec, etc.) to confirm nothing else assumed the four-key set.
- Green here is the gate for building the test agent.

**Layer 2 — live dogfood on a second test agent** built from this branch (`PLOW_AGENT_REPO=…/life-assistant-hermes-agent.git#feat/persona-and-voice`), on a spare line/number. Text-test, from a *fresh* config so real first-run onboarding fires:
- **Voice:** first contact, a morning-style exchange, a graceful failure (calendar unreachable), casual "thanks," a "what can you do." Reads as the brunch-friend voice; sentence case; no dashes; no banned filler.
- **Onboarding restructure (the risky part):**
  - Not-connected path: intro shows the combined app bubble + the single download CTA, then the city question.
  - Connected path: no app talk at all, straight to the city question.
  - No sports question anywhere in general onboarding.
  - Calendar step invites picking **several** calendars, and multiple picks are recorded.
  - Onboarding reports **complete** with just `introduced` + `weather.location` + `calendar.sources` (no sports).
  - The close **offers the Pi**.
  - Wall setup (`ld-wall-setup`) is where the **followed-teams** question now appears, and it writes `sports.followed`.
- **Guardrail regressions:** silence token in a group, no verbatim private quotes, onboarding still owner-only/DM-only, resume mid-onboarding doesn't re-ask stored answers.

**Merge & propagate — only after both layers pass:**
- Merge `feat/persona-and-voice` to `main`. Every new agent's Deploy builds `main`.
- **Re-deploy your own instance from `main`** to pick it up. Crons re-register after a home rebuild.

## Success criteria

- persona.md opens with a character and a Voice section a stranger could read and recognize the agent's personality — not one word.
- The banned-phrase list and punctuation rules (incl. no dashes) are present and specific.
- Onboarding's first bubble is unmistakably in-voice; the send-sequence machinery is unchanged.
- General onboarding no longer asks about sports; the completion gate is the three keys (`introduced`, `weather.location`, `calendar.sources`); the calendar step invites several picks; the close offers the Pi.
- `ld-wall-setup` asks the followed-teams question and writes `sports.followed`.
- `tests/test_onboarding_contract.py` (three-key) and the full suite pass.
- Nothing references the weekly digest or the `[[BUBBLE]]` prototype.
- No Zoen strings anywhere.
- Verified live on the second test agent (both paths) before merge.
