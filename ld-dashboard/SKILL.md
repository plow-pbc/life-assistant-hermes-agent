---
name: ld-dashboard
description: The life-dashboard's cron spec — the six producer schedules as reviewed data, and the idempotent registration that replays them. Use when asked to set up, re-register, inspect or repair the life dashboard crons, or after rebuilding the agent's home.
---

# Life Dashboard — the cron spec

The six producer schedules, versioned. Two are registered; four are blocked and
say why.

## Why a skill and not a note

`hermes cron` persists jobs to `/opt/data/cron/jobs.json`, and `agent-mgr
restore` does **not** replay it. A rebuilt instance therefore comes up with a
wall screen that never updates and nothing to diff against — the schedules are
the one part of this agent's behaviour that a restore silently drops. Keeping
them here means "set up the life dashboard crons" replays a reviewed spec rather
than improvising six schedules from a sentence.

## Registering

Inside the container:

    /opt/data/skills/ld-dashboard/scripts/register_crons.py

Preview without changing anything: `… register_crons.py --dry-run`.

Create-if-missing, so it is safe to re-run. Two behaviours are deliberate and
were both learned by the retired seed installer this is ported from:

- A failed `hermes cron list` **aborts**. An empty snapshot read as "nothing
  exists" re-registers every job, duplicating all of them.
- Dedup matches a job name as a **whole word**, never a substring, so a stale
  `ld-weather-v2` cannot be mistaken for `ld-weather` and skip the real one.

## The spec

`scripts/register_crons.py` `JOBS` is the single source; this table summarises it.

| producer | schedule | card | state |
|---|---|---|---|
| `ld-weather` | `0 6 * * *` | 3 · weather | **live** |
| `ld-sports` | `0 6 * * *` | 5 · sports | **live** |
| `ld-morning-updates` | `0 7 * * *` | 2 · affirmation | blocked — Google Calendar, `plow-pbc/latch#183` |
| `ld-morning-triage` | `5 7 * * *` | 1 · alert | blocked — Gmail + Slack; needs the iMessage rewrite through Latch |
| `ld-weekly-digest` | `0 17 * * 0` | 4 · digest | blocked — Google Calendar, `plow-pbc/latch#183` |
| `ld-calendar-nudge` | `20,50 * * * *` | 1 · alert | blocked — Google Calendar, `plow-pbc/latch#183` |

Blocked means the producer body is not in this repo and its cron is not
registered: `plow-connectors` was dropped, so those four have no data source on
this agent. Their bodies stay fetchable in the archived upstream repos, and
their card numbers stay reserved here so the mapping cannot silently renumber
when they land.

## Two values that are never literals

**The timezone.** `hermes cron create` takes no per-job zone — every job fires in
the container's zone, which is `agent-mgr`'s `AGENT_TZ`. `0 6 * * *` therefore
means 06:00 wherever the container thinks it is, and
`ld-shared/scripts/ld_config_gate.py` is what proves that zone equals
`family.timezone` in `/opt/data/ld/config.json`. A mismatch is not an error
anywhere — it is a dashboard that updates at the wrong hour.

**The Plow Chat delivery target.** `ld-calendar-nudge` messages the owner, and
which chat that is was minted by this instance's own activation. It resolves
from `PLOW_CHAT_CHAT_UID` at registration time and refuses to register on a
blank one — a literal would message whoever the spec was written for, and a
blank uid registers a job that delivers to `plow_chat:` and fails silently at
06:00 rather than at create time.

## Unattended runs

The producers fire with nobody present. Nothing here auto-approves: the sibling
`str` agent has run a cron every two minutes for months with no
`hooks_auto_accept` and no `HERMES_ACCEPT_HOOKS` anywhere in its config, so a
scheduled turn on this image does not gate on a human. Confirm it the same way
rather than trusting this paragraph — force one run and read the result:

    /opt/hermes/bin/hermes cron run <job-id>
    /opt/hermes/bin/hermes cron runs
