#!/usr/bin/env python3
"""Register the life-dashboard producer crons, idempotently, from a versioned spec.

Why this exists at all. `hermes cron` persists jobs to /opt/data/cron/jobs.json,
which `agent-mgr restore` does NOT replay -- so a rebuilt instance comes up with
a wall screen that never updates and nothing to diff against. Keeping the six
definitions here means "set up the life dashboard crons" replays a reviewed spec
instead of improvising six schedules from a sentence.

Schedules and prompts are ported from the retired seed's CRON_JOBS table
(seed-life-dashboard-hermes-agent@678c7b17, ref/install-skills.sh:366-410), so
they are the ones that have been running, along with the invariant that table
learned: never read "I could not tell what is registered" as "nothing is",
because that duplicates every job.

How that invariant is kept has changed, and the mechanism is the whole design.
The seed shelled out to `hermes cron list` and matched on its text. Doing the
same here cost three review rounds of guards -- whole-word matching, a name
regex, a floor for when it parsed nothing, a positively-recognised
empty-schedule notice, `--all` for paused jobs, a second floor for partial
parses -- each one correct and none of them reaching the bottom, because a
human-readable listing is not a data structure. This reads jobs.json instead:
hermes's own state, where a name is a field, `enabled`/`paused_at` are fields,
an absent file is unambiguously an empty schedule, and a malformed one raises.
See registered_jobs().

It runs INSIDE the container, where hermes is on PATH and that file lives.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

HERMES = "/opt/hermes/bin/hermes"
# Where `hermes cron` persists its jobs -- the same file the issue names as the
# reason this skill exists, because `agent-mgr restore` does not replay it.
JOBS_FILE = "/opt/data/cron/jobs.json"

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
        # One message for absent and blank alike. Splitting them looked like
        # better attribution and was worse: before `agent-mgr activate` runs,
        # PLOW_CHAT_CHAT_UID is not a key at all, so the un-activated
        # instance -- the case this refusal exists for -- took the "check your
        # spelling" branch. Whether blank is even reachable depends on
        # agent-mgr's compose template, which this repo does not own. A typo in
        # JOBS is a static defect and is caught by a test instead.
        value = env.get(name, "").strip()
        if not value:
            raise SystemExit(
                f"refusing to register: delivery target needs {name}, which this "
                "container's environment does not supply. It is minted by "
                "`agent-mgr activate` and lives in the instance's own dotenv -- "
                "or, if the instance IS activated, check the spelling in JOBS."
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


def registered_jobs(jobs_path=JOBS_FILE):
    """What is already scheduled, from hermes's own persisted state.

    THE SEAM. This used to shell out to `hermes cron list` and parse the
    rendering, and three review rounds ran on the consequences without ever
    reaching the bottom of them: substring-vs-whole-word matching, a name regex
    coupled to a format nothing pins, a floor for when that regex returned
    nothing, a positively-recognised empty-schedule notice so a fresh instance
    was not mistaken for a broken one, `--all` so paused jobs counted, and then a
    second floor for a PARTIAL parse. Six guards, each correct, all defending the
    same thing: that a human-readable listing is not a data structure.

    jobs.json is the file `hermes cron` actually writes -- the issue that asked
    for this skill names it as the reason the skill has to exist, since
    `agent-mgr restore` does not replay it. Reading it deletes the whole class:
    a name is a field, `enabled` and `paused_at` are fields, an absent file is
    unambiguously an empty schedule, and a malformed one raises instead of
    quietly parsing to zero.

    Returns {name: is_runnable}. A job that exists but will never fire is a
    DIFFERENT answer from one that does not exist, and the caller needs both:
    re-registering the first duplicates it, skipping it silently leaves a card
    that never updates again.
    """
    path = pathlib.Path(jobs_path)
    if not path.exists():
        # A fresh instance, before anything has been scheduled. Unambiguous --
        # which is the point; the notice-sniffing this replaces existed only
        # because an empty listing and an unreadable one looked identical.
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f"refusing to register: could not read {path} ({exc}). Treating an "
            "unreadable schedule as an empty one duplicates every job."
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise SystemExit(
            f"refusing to register: {path} is not the expected "
            '{"jobs": [...]} shape -- the format changed, and reading that as '
            "'nothing exists' would duplicate every job."
        )
    out = {}
    for job in data["jobs"]:
        name = (job or {}).get("name")
        if name:
            out[name] = bool(job.get("enabled", True)) and not job.get("paused_at")
    return out


def is_present(registered, name):
    """Exact membership on a real field. No near-miss to guard against."""
    return name in registered


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


def main(argv=None, runner=_run, env=None, jobs_path=JOBS_FILE):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be registered and change nothing")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env

    for job in BLOCKED:
        print(f"blocked, not registered: {job['name']} ({job['schedule']}) -- {job['blocked']}")

    if not shutil.which(HERMES) and not os.path.exists(HERMES):
        raise SystemExit(f"{HERMES} not found -- run this inside the agent container")

    # Read for --dry-run too. A preview that skips the check reports "would
    # register" for a job that is already there -- the opposite of what the real
    # run does, from the one mode whose entire job is to say what the real run
    # will do.
    registered = registered_jobs(jobs_path)

    for job in LIVE:
        if is_present(registered, job["name"]):
            if registered[job["name"]]:
                print(f"already present, {'would skip' if args.dry_run else 'skipped'}: {job['name']}")
            else:
                # Exiting 0 on this would report a clean run over a card that
                # never updates again -- the quiet failure this file is
                # organised around. Re-registering it instead would duplicate
                # the job, so neither action is right: say so.
                print(
                    f"WARNING: {job['name']} is registered but PAUSED -- it will "
                    "never fire, and this leaves it alone rather than "
                    f"duplicating it. Resume it: hermes cron resume {job['name']}"
                )
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
