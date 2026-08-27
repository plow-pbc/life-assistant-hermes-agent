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
looks identical to a producer that is running and finding nothing. Run it after
`sign-in`, and again after any rebuild of the home.

You are already inside the container, running as the gateway's own uid. Just
run it:

    /opt/data/skills/ld-dashboard/scripts/register_crons.py

**Then paste its output verbatim and report its exit status. The run is not
done until you have.** This matters more than it looks: the script signals every
refusal it has — a missing or unusable `ld-config.json`, a `family.timezone`
that is not the container's zone, an empty `TZ`, a failed `cron create`, an
unreadable `jobs.json`, a producer that is registered but PAUSED — through
its output and a
non-zero exit, and a turn does not propagate an exit code. If you summarise
instead of pasting, "set up the crons, though one was already there and isn't
active" is a perfectly honest sentence describing a run that failed, and the
operator has no way to tell. Do not paraphrase, and do not call it done on a
non-zero exit.

(The uid matters to whoever invokes this from the *host* — a bare
`agent-mgr compose … exec` lands as root and would create the schedule
root-owned. That is the README's problem, and the reason bring-up goes through
`agent-mgr agent` rather than an exec. Nothing for you to do about it here.)

Create-if-missing, so it is safe to re-run: it reads what is already scheduled
and creates only what is absent.

Then verify it — see [Unattended runs](#unattended-runs), which owns both the
host and in-container forms and what a forced run does and does not prove.

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

An *absent* one reads as a fresh instance, and **nothing distinguishes that from
a wrong `JOBS_FILE`** — both raise the same `ENOENT`. That is a decision, not an
oversight: this deployment has one operator-run instance and one path, so a
wrong one would be a code edit rather than a configuration mistake, and the
guards that used to tell them apart cost more than the fault they fenced. If you
ever see it register the same jobs on every run, the path is what to check.

**A paused producer is neither.** It is registered, so re-registering duplicates
it; it will never fire, so skipping it silently leaves a card that stops
updating. It is left alone, named, and — after the rest of the run finishes —
**the script exits non-zero** — the signal an unattended re-provision can act
on. A turn does not propagate that code, which is why § Registering above
requires you to paste the output verbatim and report the status rather than
summarise: that instruction is what carries the signal across the gap. A
*scripted* caller that wants the code itself uses the exec form instead (README
§ Bring-up, with its uid caveat):

    WARNING: ld-weather is registered but PAUSED -- it will never fire...
    Resume it: hermes cron resume ld-weather

## The spec

`/opt/data/skills/ld-dashboard/scripts/register_crons.py`'s `JOBS` is the single
source; this table summarises it.

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
`register_crons.py` is what proves that zone equals `family.timezone` in
`/opt/data/ld/config.json` — it reads the container's `TZ` and **refuses to
register at all** if the two differ, naming both zones. The gate does not: it
checks only that `family.timezone` is non-blank, which a perfectly valid
`America/Chicago` config satisfies while its cards land two hours late on a
Los_Angeles container, silently.

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

The producers fire with nobody present. Nothing here auto-approves, and it does
not need to: the sibling `str` agent has run a cron every two minutes for months
with no `hooks_auto_accept` and no `HERMES_ACCEPT_HOOKS` anywhere in its config,
so a scheduled turn on this image does not gate on a human.

Verify rather than trust that paragraph. From inside the container:

    /opt/hermes/bin/hermes cron list          # is the job there, and not paused?
    /opt/hermes/bin/hermes cron run <job-id>  # force one
    /opt/hermes/bin/hermes cron runs          # then look for the card on the kiosk

From the host, where the bring-up reader is standing — a turn, for the same
reason bring-up is one (no uid to get wrong):

    agent-mgr agent <agent> 'list the dashboard crons, force one run, report what happened'

**What that proves, and what it does not.** A forced run exercises the producer,
the mount, `/opt/data/ld/config.json` and the kiosk POST — the whole path a
6 a.m. fire would take *once it starts*. It does not show that the
already-running gateway loaded the newly created schedule. If the gateway caches
its jobs at startup, every command above succeeds, a card appears, and the wall
screen is still dark tomorrow morning.

That question is **unmeasured**. Until it is, do not call bring-up done on a
forced run: set a job a couple of minutes out and watch it fire on its own, or
restart the gateway after registering so the answer cannot decide the outcome.
Whoever settles it should write the answer here and delete this paragraph.
