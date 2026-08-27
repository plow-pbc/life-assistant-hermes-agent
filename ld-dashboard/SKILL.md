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

**This is a bring-up step, not a repair step.** `agent-mgr restore` does not
replay `jobs.json`, so an instance that has been brought up without it has a wall
screen that never updates — and nothing to diff against, because the failure
looks identical to a producer that is running and finding nothing. The README's
bring-up block and the `justfile` header both carry this line for that reason;
run it after `sign-in`, and again after any rebuild of the home.

Inside the container, **as the gateway's own uid** — `agent-mgr compose … exec`
lands as root, and a schedule created root-owned is one the gateway can never
pause, resume or remove:

    /opt/data/skills/ld-dashboard/scripts/register_crons.py

Then force one run (`hermes cron run <job-id>`) and check the kiosk. That proves
the producer, not the scheduler: whether the already-running gateway loads a
newly created job without a restart is unmeasured, so watch the first unforced
fire before calling bring-up done.

Preview without changing anything: `… register_crons.py --dry-run`.

Create-if-missing, so it is safe to re-run — including `--dry-run`, which reads
the same state and therefore reports `already present, would skip` exactly where
the real run would skip.

What counts as "already registered" comes from `/opt/data/cron/jobs.json`, the
file `hermes cron` itself writes. Not from `hermes cron list`: that is a
human-readable rendering nothing pins, and matching on its text needed a new
guard every time it was wrong — whole-word matching, a name regex, a floor for
when the regex found nothing, a positively-recognised empty-schedule notice, a
flag so paused jobs counted. Reading the file makes all of that ordinary: a name
is a field, so is `enabled`, so is `paused_at`.

One invariant survives from the retired seed installer, because it is the one
that matters: **never read "I could not tell what is registered" as "nothing
is"** — that re-registers every job and duplicates all of them. So an unreadable
or unexpected `jobs.json` aborts.

An *absent* one is the case the file cannot settle on its own: a fresh instance
and a wrong path raise the same `ENOENT`. Two things close that. Before doing
anything it checks the mounted home — if `/opt/data` is not there, the container
is not wired correctly and it refuses rather than reading an empty schedule:

    refusing to register: /opt/data does not exist ... the agent home is not
    mounted, or JOBS_FILE is wrong

That is the guard most likely to be the one you actually see. The other is the
read-back below.

**A paused producer is neither.** It is registered, so re-registering duplicates
it; it will never fire, so skipping it silently leaves a card that stops
updating. It is left alone, named, and — after the rest of the run finishes —
**the script exits non-zero**, because the exit code is the only signal that
reaches an unattended re-provision. `--dry-run` says the same thing and still
exits 0 — a preview must not report a failure over a state it did not create:

    WARNING: ld-weather is registered but PAUSED -- it will never fire...
    Resume it: hermes cron resume ld-weather

The first job it creates is read back out of `jobs.json` — the floor under a
path that *looks* plausible: nothing pins it, so a wrong one would read as an
empty schedule on every run and register duplicates forever, quietly. The
read-back makes that fail on run one, and says the job has been created so a
retry does not add a second.

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

**The Plow Chat delivery target.** `ld-calendar-nudge` messages the owner as
well as posting a card, and which chat that is was minted by this instance's own
activation — so it can never be a literal here, on a repo more than one person
runs.

Right now **nothing expands it and nothing checks it.** The producer is blocked,
so the target sits in `JOBS` as data (`plow_chat:${PLOW_CHAT_CHAT_UID}`)
recording what it will need, and `create_argv()` has no `--deliver` arm at all:
the resolver that used to expand the variable and refuse a blank one was
reachable only from this one blocked row, so it was deleted rather than carried
as roadmap inventory.

> **If you are unblocking `ld-calendar-nudge`, read this.** Flipping `blocked`
> to `None` is not enough — `create_argv()` will silently drop the target and
> the nudge will post its card and message nobody.
> `test_no_live_job_needs_a_delivery_target` fails the moment you do it, which
> is the intended tripwire; the expansion it is asking for is in git history as
> `resolve_deliver` (deleted in the round that answered knightwatch's stage-one
> simplification probe). Restore it with its refusals, or write a better one —
> but do not delete the assertion.

## Unattended runs

The producers fire with nobody present. Nothing here auto-approves: the sibling
`str` agent has run a cron every two minutes for months with no
`hooks_auto_accept` and no `HERMES_ACCEPT_HOOKS` anywhere in its config, so a
scheduled turn on this image does not gate on a human. Confirm it the same way
rather than trusting this paragraph — force one run and read the result:

    /opt/hermes/bin/hermes cron run <job-id>
    /opt/hermes/bin/hermes cron runs
