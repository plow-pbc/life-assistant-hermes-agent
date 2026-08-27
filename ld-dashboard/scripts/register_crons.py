#!/usr/bin/env python3
"""Register the life-dashboard producer crons, idempotently, from a versioned spec.

Why this exists at all. `hermes cron` persists jobs to /opt/data/cron/jobs.json,
which `agent-mgr restore` does NOT replay -- so a rebuilt instance comes up with
a wall screen that never updates and nothing to diff against. Keeping the six
definitions here means "set up the life dashboard crons" replays a reviewed spec
instead of improvising six schedules from a sentence.

Ported from the retired seed's install-skills.sh CRON_JOBS table
(seed-life-dashboard-hermes-agent@678c7b17, ref/install-skills.sh:366-410) --
same schedules, same prompts, same two invariants it learned the hard way:

  * a failed `hermes cron list` ABORTS. An empty snapshot read as "nothing
    exists" re-registers every job, duplicating all of them.
  * dedup matches a job name as a WHOLE WORD, never a substring. Every name here
    is a prefix of nothing, but `ld-weather` is a substring of a hypothetical
    `ld-weather-v2`, and counting a longer stale job as "already present" would
    silently skip the real one.

It runs INSIDE the container, where hermes is on PATH -- unlike the seed's
version, which drove `docker compose exec` from the host.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

HERMES = "/opt/hermes/bin/hermes"

# What a genuinely empty schedule looks like, as opposed to a listing this
# cannot read. Both parse to zero names, and only one of them is safe to act on:
# an empty schedule means register everything, a format change means register
# everything AGAIN. So the empty case has to be recognised positively rather
# than inferred from the absence of names.
#
#   $ hermes cron list
#   No scheduled jobs.
#   Create one with 'hermes cron create ...' or the /cron command in chat.
EMPTY_LISTING = re.compile(r"^\s*No scheduled jobs\b", re.I | re.M)

# The spec. One row per producer, live or not.
#
# `deliver` is None for every card-only producer: the card IS the delivery, over
# the kiosk POST. ld-calendar-nudge also messages the owner, and its target is
# per-instance -- the chat uid this instance activated -- so it resolves from the
# environment at registration time and is never a literal here. A literal would
# message whoever the spec was written for.
#
# No timezone anywhere. `hermes cron create` takes no per-job zone: jobs fire in
# the container's zone, which is agent-mgr's AGENT_TZ, and the ld-config gate is
# what proves that zone equals family.timezone.
JOBS = (
    {
        "name": "ld-weather",
        "card": 3,
        "type": "weather",
        "schedule": "0 6 * * *",
        "prompt": (
            "Run the ld-weather producer now: fetch the forecast and post the "
            "self-contained weather HTML tile to the kiosk as card 3, type weather."
        ),
        "skill": "ld-weather",
        "deliver": None,
        "blocked": None,
    },
    {
        "name": "ld-sports",
        "card": 5,
        "type": "sports",
        "schedule": "0 6 * * *",
        "prompt": (
            "Run the ld-sports producer now: fetch results and post the "
            "self-contained sports HTML tile to the kiosk as card 5, type sports."
        ),
        "skill": "ld-sports",
        "deliver": None,
        "blocked": None,
    },
    {
        "name": "ld-morning-updates",
        "card": 2,
        "type": "affirmation",
        "schedule": "0 7 * * *",
        "prompt": (
            "Run the ld-morning-updates affirmation producer now: compose the "
            "morning affirmation and post it to the kiosk as card 2, type affirmation."
        ),
        "skill": "ld-morning-updates",
        "deliver": None,
        "blocked": "reads Google Calendar; plow-connectors is dropped -- blocked on plow-pbc/latch#183",
    },
    {
        "name": "ld-morning-triage",
        "card": 1,
        "type": "alert",
        "schedule": "5 7 * * *",
        "prompt": (
            "Run the ld-morning-triage producer now: surface the one "
            "most-important unaddressed inbound across Gmail and Slack from the "
            "last 36h and post it to the kiosk as card 1, type alert."
        ),
        "skill": "ld-morning-triage",
        "deliver": None,
        "blocked": "reads Gmail and Slack; needs a rewrite onto the Mac's iMessage DB through Latch",
    },
    {
        "name": "ld-weekly-digest",
        "card": 4,
        "type": "digest",
        "schedule": "0 17 * * 0",
        "prompt": (
            "Run the ld-weekly-digest producer now: compose the week-ahead "
            "digest and post it to the kiosk as card 4, type digest."
        ),
        "skill": "ld-weekly-digest",
        "deliver": None,
        "blocked": "reads Google Calendar; plow-connectors is dropped -- blocked on plow-pbc/latch#183",
    },
    {
        "name": "ld-calendar-nudge",
        "card": 1,
        "type": "alert",
        "schedule": "20,50 * * * *",
        "prompt": (
            "Run the ld-calendar-nudge producer now: if a meeting with other "
            "attendees starts within the lookahead window, post a kiosk reminder "
            "and message the owner over Plow Chat."
        ),
        "skill": "ld-calendar-nudge",
        "deliver": "plow_chat:${PLOW_CHAT_CHAT_UID}",
        "blocked": "reads Google Calendar; plow-connectors is dropped -- blocked on plow-pbc/latch#183",
    },
)

LIVE = tuple(j for j in JOBS if not j["blocked"])
BLOCKED = tuple(j for j in JOBS if j["blocked"])


def resolve_deliver(spec, env):
    """Expand a ${VAR} delivery target from the environment, or fail loudly.

    Only reached for a live job. A blank uid would register a job that delivers
    to the literal string `plow_chat:` -- accepted at create time and silently
    undeliverable at 06:00, which is the failure this refuses to create.

    Two ways to be left holding an unusable target, and both refuse here. The
    variable is set but blank, which the substitution catches; or the placeholder
    is spelled in a shape the pattern does not match -- lowercase, hyphenated,
    dotted -- in which case re.sub simply leaves it verbatim and hands back
    `plow_chat:${whatever}` as if it were a chat id. The second is the quieter of
    the two, so the result is checked rather than the input."""
    if spec is None:
        return None
    def sub(match):
        name = match.group(1)
        # Absent and blank are different faults with different remedies, and the
        # widened pattern makes the first reachable: a typo in JOBS is now
        # identifier-shaped enough to arrive here, and answering it with the
        # credential message sends the operator to re-mint a token when the real
        # defect is a misspelling three lines up.
        if name not in env:
            raise SystemExit(
                f"refusing to register: delivery target names ${{{name}}}, which "
                "is not a variable this container sets -- check the spelling in "
                "the JOBS table."
            )
        value = env[name].strip()
        if not value:
            raise SystemExit(
                f"refusing to register: delivery target needs {name}, which is "
                "empty in this container's environment. It is minted by "
                "`agent-mgr activate` and lives in the instance's own dotenv."
            )
        return value
    out = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", sub, spec)
    if "${" in out:
        raise SystemExit(
            f"refusing to register: delivery target {spec!r} still holds an "
            "unexpanded placeholder after substitution -- it would be sent to "
            "hermes as a literal chat id and fail only when the job fires."
        )
    return out


def existing_names(runner):
    """The set of job names already registered. Aborts rather than guessing.

    The seed learned the abort: treating a failed list as an empty list
    re-registers everything.

    Returns parsed names, not the raw blob, and that is the whole point. Every
    prompt in JOBS below literally contains its own producer name ("Run the
    ld-weather producer now..."), and `hermes cron list` renders job fields; a
    dedup key of "does this string appear anywhere in the output" is therefore
    one wording change away from matching a job's own prompt and silently
    skipping a registration. Reading the Name: field makes the key the field it
    is supposed to be."""
    proc = runner([HERMES, "cron", "list", "--all"])
    if proc.returncode != 0:
        raise SystemExit(
            "refusing to register: `hermes cron list` errored, and an empty "
            "snapshot read as 'nothing exists' would duplicate every job.\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
    names = set(re.findall(r"^\s*Name:\s*(\S+)\s*$", proc.stdout, re.M))
    # The parse needs the same floor the exit code has. `hermes cron list` has no
    # machine-readable mode, so this reads a rendering nothing pins: a table, a
    # relabelled column, a lowercase `name:` all yield an empty set from a
    # SUCCESSFUL call -- which is the "nothing exists" reading that duplicates
    # every job, arriving through the door the returncode check does not cover.
    # Output with no parseable name is a format change, not an empty schedule.
    if not names and not EMPTY_LISTING.search(proc.stdout):
        raise SystemExit(
            "refusing to register: `hermes cron list` succeeded but its output "
            "holds neither a `Name:` field nor the empty-schedule notice -- the "
            "format changed, and reading that as 'nothing exists' would "
            "duplicate every job.\n"
            f"{proc.stdout}"
        )
    return names


def is_present(names, name):
    """Exact membership. `ld-weather` is not satisfied by a stale
    `ld-weather-v2` or `ld-weather.v2` -- a near-miss counted as present skips
    the real registration and the card never updates."""
    return name in names


def create_argv(job, env):
    argv = [HERMES, "cron", "create", job["schedule"], job["prompt"], "--name", job["name"]]
    if job["skill"]:
        argv += ["--skill", job["skill"]]
    deliver = resolve_deliver(job["deliver"], env)
    if deliver:
        argv += ["--deliver", deliver]
    return argv


def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True)


def main(argv=None, runner=_run, env=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be registered and change nothing")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env

    for job in BLOCKED:
        print(f"blocked, not registered: {job['name']} ({job['schedule']}) -- {job['blocked']}")

    if not shutil.which(HERMES) and not os.path.exists(HERMES):
        raise SystemExit(f"{HERMES} not found -- run this inside the agent container")

    # Always listed, even for --dry-run. `hermes cron list` is read-only, and a
    # preview that skips it reports "would register" for a job that is already
    # there -- the opposite of what the real run does, from the one mode whose
    # entire job is to say what the real run will do. It also means a broken
    # `cron list` shows a clean plan and then aborts for real.
    names = existing_names(runner)

    for job in LIVE:
        if is_present(names, job["name"]):
            print(f"already present, {'would skip' if args.dry_run else 'skipped'}: {job['name']}")
            continue
        argv_ = create_argv(job, env)
        if args.dry_run:
            print(f"would register: {job['name']} ({job['schedule']})")
            continue
        proc = runner(argv_)
        if proc.returncode != 0:
            raise SystemExit(
                f"could not register {job['name']}:\n{proc.stdout}\n{proc.stderr}"
            )
        print(f"registered: {job['name']} ({job['schedule']})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
