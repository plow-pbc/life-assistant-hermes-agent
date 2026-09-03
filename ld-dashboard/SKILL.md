---
name: ld-dashboard
description: The life-dashboard's cron spec — the seven producer schedules as reviewed data — six producers, the triage on two clocks —, and the idempotent registration that replays them. Use when asked to set up, re-register, inspect or repair the life dashboard crons, or after rebuilding the agent's home.
---

# Life Dashboard — the cron spec

Seven rows, versioned — six producers, the triage on two clocks. All seven are registered.

## Why a skill and not a note

`hermes cron` persists jobs to `/var/lib/hermes/cron/jobs.json`, and nothing
replays it on a rebuild. A rebuilt instance therefore comes up with a
wall screen that never updates and nothing to diff against — the schedules are
the one part of this agent's behaviour that a deploy silently drops. Keeping
them here means "set up the life dashboard crons" replays a reviewed spec rather
than improvising seven schedules from a sentence.

## Registering

**This is a bring-up step, not a repair step.** A rebuild does not
replay `jobs.json`, so an instance that has been brought up without it has a wall
screen that never updates — and nothing to diff against, because the failure
looks identical to a producer that is running and finding nothing. Run it after
`sign-in`, and again after any rebuild of the home.

You are already inside the container, running as the gateway's own uid. Just
run it:

    /var/lib/hermes/skills/ld-dashboard/scripts/register_crons.py

**Then paste its output verbatim and report its exit status. The run is not
done until you have.** This matters more than it looks: the script signals
every refusal it has — a missing or unusable `config.json`, a
`family.timezone` that is not the container's zone, an empty `TZ`, a failed
`cron create`, an unreadable `jobs.json`, a producer that is registered but
PAUSED — through its output and a non-zero exit, and a turn does not propagate
an exit code. If you summarise instead of pasting, "set up the crons, though
one was already there and isn't active" is a perfectly honest sentence
describing a run that failed, and the operator has no way to tell. Do not
paraphrase, and do not call it done on a non-zero exit.

(The uid matters to whoever invokes this from the *host* — a bare
A `docker exec` lands as root and would create the schedule
root-owned. That is the README's problem, and the reason bring-up goes through
a chat turn rather than an exec. Nothing for you to do about it here.)

Create-if-missing, so it is safe to re-run: it reads what is already scheduled
and creates only what is absent.

Create-if-missing also means a row whose spec changed is left as it was:
an instance that registered `ld-morning-triage` before it carried a
delivery target keeps the old, card-only job. Give it the target once —
`hermes cron edit <job id> --deliver plow_chat:<the PLOW_HOME_CHANNEL
value>` from a turn, or remove the job and re-run this — and the
`ld-evening-triage` row registers on its own as a missing one.

Then verify it — see [Unattended runs](#unattended-runs), which owns both the
host and in-container forms and what a forced run does and does not prove.

What counts as "already registered" comes from `/var/lib/hermes/cron/jobs.json`, the
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
    Resume it: /opt/hermes/bin/hermes cron resume ld-weather

## The spec

`/var/lib/hermes/skills/ld-dashboard/scripts/register_crons.py`'s `JOBS` is the single
source; this table summarises it.

| producer | schedule | card | state |
|---|---|---|---|
| `ld-weather` | `0 6 * * *` | 3 · weather | **live** |
| `ld-sports` | `0 6 * * *` | 5 · sports | **live** |
| `ld-morning-updates` | `0 7 * * *` | 2 · affirmation | **live** — Google Calendar via Latch's vendored gog |
| `ld-morning-triage` | `5 7 * * *` | 1 · alert | **live** — iMessage + Gmail through Latch, texted to the owner |
| `ld-evening-triage` | `0 18 * * *` | 1 · alert | **live** — the same sheet at 18:00 (skill `ld-morning-triage`), texted to the owner |
| `ld-weekly-digest` | `0 17 * * 0` | 4 · digest | **live** — Google Calendar via Latch's vendored gog |
| `ld-calendar-nudge` | `20,50 * * * *` | 1 · alert | **live** — Google Calendar via Latch's vendored gog |

All seven rows register unconditionally. The blocked/LIVE partition machinery
left with the last blocked row — no blocked producer is on any roadmap, and
git history keeps the pattern if one ever loses its data source again.

## Two values that are never literals

**The timezone.** `hermes cron create` takes no per-job zone — every job fires in
the container's zone, which is `TZ`. `0 6 * * *` therefore
means 06:00 wherever the container thinks it is, and
`register_crons.py` is what proves that zone equals `family.timezone` in
`/var/lib/hermes/ld/config.json` — it reads the container's `TZ` and **refuses to
register at all** if the two differ, naming both zones. The gate does not: it
checks only that `family.timezone` is non-blank, which a perfectly valid
`America/Chicago` config satisfies while its cards land two hours late on a
Los_Angeles container, silently.

**The Plow Chat delivery target.** Three rows message the owner as well as
posting a card — the digest and the two triage runs — and which chat that is
was minted by this instance's own activation — so it can never be a literal
here, on a repo more than one person runs. It sits in `JOBS` as
`plow_chat:${PLOW_HOME_CHANNEL}`, and
`resolve_deliver()` expands it from the container environment, where first boot
published it after asking Plow which chat this agent's owner holds. Run this
from a turn, which inherits that environment from the gateway; a bare
`docker exec` session carries none of it. An unset or blank variable is refused
by name — an empty target is a chat leg that silently delivers nowhere.

The rows that text the owner take two delivery paths, on purpose. `--deliver`
relays EVERY final response, so it fits a row whose every run has content:
`ld-weekly-digest` (weekly, always a digest, its final response IS the digest)
and the two triage rows (an alert, or the one line `No alert today.`) — their
rows ride it. It does not fit `ld-calendar-nudge` — half-hourly with quiet
no-op runs — so its chat leg lives in its committed `post_nudge.py`
coordinator and its row's `deliver` is `None`
(`test_only_always_has_content_rows_ride_the_native_deliver_arm` pins the
divide).

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

    text the agent: 'list the dashboard crons, force one run, report what happened'

**What that proves, and what it does not.** A forced run exercises the producer,
the mount, `/var/lib/hermes/ld/config.json` and the kiosk POST — the whole path a
6 a.m. fire would take *once it starts*. It does not show that the
already-running gateway loaded the newly created schedule. If the gateway caches
its jobs at startup, every command above succeeds, a card appears, and the wall
screen is still dark tomorrow morning.

**Measured 2026-08-27**, on the image named below: it does load them. Both jobs
were created against an already-running gateway and fired unforced at their
first 06:00, with no restart in between — `/opt/hermes/bin/hermes cron runs`
shows `source=builtin` where a forced run shows `source=direct`, which is how
you tell the two apart:

    083e8b7fcc7a  completed  job=6213bd7c696c  source=builtin   2026-08-27T06:00:09-07:00
    786a083d6047  completed  job=4d0318081ba4  source=builtin   2026-08-27T06:00:09-07:00
    c04d47403bc2  completed  job=6213bd7c696c  source=direct    2026-08-27T05:26:05-07:00

Both kiosk cards changed on that fire and the three cards no producer owns did
not, so the change came from the schedule and not from the earlier forced runs.
`Next run` rolled to the following day on both.

A forced run is now enough for bring-up: the schedule-loading question is
settled above, and you do not need the restart.

An image bump is the one thing that could reintroduce startup caching, so check
yours against the one it was measured on:

    /opt/hermes/bin/hermes --version
    Hermes Agent v0.19.0 (2026.7.20) · upstream b4f8c491

If you ever need to re-settle it, the thing to know is that `source=builtin` is
the one row you cannot force: it takes a real scheduled fire, so create a job a
few minutes out against the running gateway, let it land, and remove it again
-- a throwaway left behind keeps firing on the live agent.
