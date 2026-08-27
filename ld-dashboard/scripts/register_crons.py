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
    undeliverable at 06:00, which is the failure this refuses to create."""
    if spec is None:
        return None
    def sub(match):
        name = match.group(1)
        value = env.get(name, "").strip()
        if not value:
            raise SystemExit(
                f"refusing to register: delivery target needs {name}, which is "
                "empty in this container's environment. It is minted by "
                "`agent-mgr activate` and lives in the instance's own dotenv."
            )
        return value
    return re.sub(r"\$\{([A-Z][A-Z0-9_]*)\}", sub, spec)


def existing_names(runner):
    """Job names already registered. Aborts rather than guessing.

    The seed learned this one: treating a failed list as an empty list
    re-registers everything."""
    proc = runner([HERMES, "cron", "list"])
    if proc.returncode != 0:
        raise SystemExit(
            "refusing to register: `hermes cron list` errored, and an empty "
            "snapshot read as 'nothing exists' would duplicate every job.\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
    return proc.stdout


def is_present(listing, name):
    """Whole-word match, never a substring -- `ld-weather` must not be satisfied
    by a stale `ld-weather-v2`."""
    return re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", listing) is not None


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

    if not args.dry_run and not shutil.which(HERMES) and not os.path.exists(HERMES):
        raise SystemExit(f"{HERMES} not found -- run this inside the agent container")

    listing = "" if args.dry_run else existing_names(runner)

    for job in LIVE:
        if not args.dry_run and is_present(listing, job["name"]):
            print(f"already present, skipped: {job['name']}")
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
