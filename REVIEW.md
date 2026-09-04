# Review instructions — life-assistant

Repo-specific reviewer policy. The universal voice posture (Broken-Glass,
pro-simplification, and the don't-propose list) is supplied by the reviewers
themselves and is deliberately not restated here.

## What this repo is

**One assistant** — its persona, its skills, its schedules and its defaults.
The runtime underneath it (the Hermes image, boot, the hardened home and the
gateway's `config.yaml` seed) is `plow-pbc/plow-hermes-agent`, and every turn's
prompt framing and the Plow tools are the `plow_chat` plugin in
`plow-pbc/hermes-plow-chat`, which the base pins. `README.md` owns the product
prose; this file does not restate it.

**Stage:** pre-PMF, one repo per person, the tracked tree identical for
everyone — the difference is entirely the credential its owner texts for. The
agent holds that credential and drives its owner's Mac through Latch, so a
credential, a chat id or a person's data anywhere under this tree is blocking.

## Review priority

Subtractive remedies outrank additive ones. The falsifiable gate here is
**ownership**: this repo declares facts about one assistant, and a mechanism a
second assistant would also want belongs to a sibling — the remedy is a PR
there, plus a pin bump if this repo holds one, not a copy carried here.

**Repo-specific contrast pairs:**

| Variant DON'T (suppress / flag-as-shape) | Variant DO (real finding) |
|---|---|
| Flag persona prose, a skill's wording or a schedule for being **specific to one assistant**. Being one person's assistant is this repo's whole reason to exist; generality here is the bloat, not the fix. | Flag a change that a **sibling repo owns** per [`plow-hermes-agent` README § The repos](https://github.com/plow-pbc/plow-hermes-agent#the-repos): a base fix — `plow-init`, boot, the gateway config seed — or a plugin patch, which are `plow-hermes-agent` and `hermes-plow-chat`; a hand-rolled Plow-API or Latch-relay client, which the plugin's seed skills and `latch` already own; a deploy or skill-seeding mechanism, which is `agent-mgr`. The test is who else would have to change if the fact changed. |

**Update cadence:** edit when the stage changes. Product and architecture edits
belong in `README.md`, not here.
