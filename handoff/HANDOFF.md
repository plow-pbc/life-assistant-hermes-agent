# Onboarding multi-bubble — engineer handoff

## TL;DR

The paced, multi-bubble onboarding intro is **built and proven live in the
local harness**. What works today, texting a real agent:

- **Multi-bubble intro** — the introduction arrives as several separate
  iMessages (greeting → gist → app → privacy → "want to see the kind of thing I
  mean?" → photos → catch+link → city question), not one wall of text.
- **Ordered, in-position photos** — the four preview images land exactly where
  the SKILL places them (right after the lead-in), not batched to the end.
- **Photo stack** — the four images post as one collapsed "4 Photos" stack
  (once the API cap-4 deploy lands; see step 2), with a per-image fallback in
  the meantime.
- **Human-timing cadence** — a ~1s gap between bubbles and two deliberate
  `[[PAUSE]]` reading beats (after the photo stack, after the Latch link).
- **Perms fix** — the boot race that left the agent wedged/"paused" is fixed
  (its own PR, independent of everything else).

**Goal:** ship the paced multi-bubble onboarding.

The work splits across three homes, so it ships as **two PRs on this repo + one
patch** (the plugin can't be PR'd from here — it lives in the base image):

| Piece | Where it lives | Vehicle |
|---|---|---|
| Perms fix (s6 `life-home-perms`) | this repo, `image/s6-overlay/` | **PR A** (normal) — see Links |
| Onboarding SKILL + this `handoff/` | this repo, `ld-setup/SKILL.md` | **PR B** (DRAFT, gated) — this PR |
| plow_chat plugin (marker runtime) | **base image** repo | `handoff/plugin-changes.patch` |

---

## Ship order for tomorrow

### 1. Plugin — the unblocker (do this first)
Apply `handoff/plugin-changes.patch` to the `plow_chat` plugin in the
**base-image repo** (the one that builds `plow-cloud-agents`), file
`plugins/plow_chat/__init__.py` (or equivalent). Details + apply instructions:
`handoff/PLUGIN-CHANGES.md`. Then **rebuild + publish** the `plow-cloud-agents`
base image, and **bump `Dockerfile:11` `FROM`** in this repo to the new base
tag/digest.

Until this is in the base image, the SKILL must NOT ship — the markers would
reach users as literal `[[BUBBLE]]` text.

### 2. API cap #1726 — deploy it (can run in parallel with step 1)
`plow-pbc/plow` PR **#1726** is already **merged** (raised
`MAX_ATTACHMENTS_PER_MESSAGE` 3 → 4), but the running API still enforces the old
cap. Run the **Deploy API** workflow (`deploy-api.yml`, `workflow_dispatch` on
`main`, ECS, ~5–12 min) so `api.plow.co` actually enforces cap-4.

Needed for the **collapsed** 4-photo stack: iMessage only collapses to
"N Photos" at **4+** images; 1–3 render as separate bubbles. Until this deploys,
the plugin's fallback posts the four previews as individual images (correct
position, just not collapsed).

### 3. SKILL draft PR (this PR) — mark ready + merge
Once the plugin is in the base image (step 1), flip this DRAFT PR to ready and
merge. **Not before** — the gate at the top of this PR's description is real.

### 4. Perms-fix PR — review + merge anytime
Independent of everything above; safe to merge on its own, whenever.

### 5. Rebuild + test
Rebuild the agent image, deploy/run, text the agent, and confirm the intro
arrives as: separate bubbles → **photo stack** → pause → "only catch…" + **bare
Latch URL** (link preview renders, nothing trailing it) → pause →
"what city are you in?".

---

## Testing harness notes

- **Harness dir:** `/Users/marydyer/Hacking/plow-dev/life-assistant-hermes-agent`
- **Line up:** uses the `plow-agents` CLI (`login` / `lines` / `mint ln_xxx`)
  plus `docker compose up --build -d`. Rebuild loop is
  `docker compose down -v && docker compose up --build -d` (the `-v` wipes
  onboarding volume state for a fresh run; it keeps `./plow-credentials`).
- **Currently minted on the Willow line** — `ln_p1`, +1 650 346 6610.
- **Gotcha:** the **Spruce** line (`ln_p3`) has a stray *cloud* agent attached
  (`cloud d98c...`) that will ALSO answer. Avoid Spruce for local testing —
  use Willow.

---

## Do NOT ship (harness-only)

Two things stay uncommitted in the harness working tree and are **not** part of
any PR:

- the `compose.yml` bind-mount line
  (`./plow_chat_bubble.py:/opt/hermes/plugins/plow_chat/__init__.py:ro`), and
- `plow_chat_bubble.py` itself (the modified plugin copy).

They exist only so the harness can run the modified plugin over the stock base
image before the plugin is baked in. **Once the plugin is in the base image
(step 1), delete the bind-mount line.** The plugin's *content* is preserved for
shipping as `handoff/plugin-changes.patch`.

---

## Links

- **PR A — perms fix (normal):** https://github.com/plow-pbc/life-assistant-hermes-agent/pull/122
- **PR B — onboarding SKILL (this draft PR):** _(this PR — link filled in on open)_
- **plow #1726 — API attachment cap 3→4 (merged):** https://github.com/plow-pbc/plow/pull/1726
