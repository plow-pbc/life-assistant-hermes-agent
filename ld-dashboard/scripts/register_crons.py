#!/usr/bin/env python3
"""Register the life-dashboard producer crons, idempotently, from a versioned spec.

Why this exists at all. `hermes cron` persists jobs to /var/lib/hermes/cron/jobs.json,
which no rebuild replays -- so a rebuilt agent comes up with
a wall screen that never updates and nothing to diff against. Keeping the seven
rows here means "set up the life dashboard crons" replays a reviewed spec
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
an absent file reads as an empty schedule and a malformed one raises. See registered_jobs().

It runs INSIDE the container, where /opt/hermes/bin/hermes and that file live.
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

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)

HERMES = "/opt/hermes/bin/hermes"
# Where `hermes cron` persists its jobs -- the same file the issue names as the
# reason this skill exists: nothing replays it on a rebuild.
JOBS_FILE = "/var/lib/hermes/cron/jobs.json"
# The producers' own config. Read here for one reason: every schedule below is a
# bare cron expression, and `hermes cron create` takes no per-job timezone.
LD_CONFIG = "/var/lib/hermes/ld/config.json"

# The spec. Seven rows for six producers -- the triage runs on two clocks --
# and every row is live; the blocked/LIVE partition machinery left with the
# last blocked row (git history keeps the pattern if a producer ever loses
# its data source again).
#
# `deliver` is None for every card-only producer: the card IS the delivery,
# over the kiosk POST. The rows that also message the owner take two paths,
# on purpose:
#   - ld-weekly-digest and the two triage rows ride the cron's native
#     --deliver arm: every one of their runs has content (a digest; an alert
#     or the one-line "No alert today."), so relaying the final response IS
#     the chat leg -- the owner's previous assistant texted the triage at
#     07:07 and 18:02 every day, and this keeps that. create_argv() expands
#     the ${VAR} from the container environment, where first boot publishes
#     PLOW_HOME_CHANNEL after asking Plow which chat this agent's owner
#     holds -- and refuses a blank one. Run it from a turn, which inherits
#     that environment from the gateway: a bare `docker exec` carries none
#     of those values.
#   - ld-calendar-nudge does NOT use --deliver: it is half-hourly with quiet
#     no-op runs, and --deliver relays EVERY final response -- its chat leg
#     lives in its committed post_nudge.py coordinator, keeping quiet ticks
#     silent by construction.
#
# No timezone anywhere. `hermes cron create` takes no per-job zone: jobs fire in
# the container's zone, which this image sets at boot from family.timezone.
# require_timezone_agreement() below refuses to register unless that zone equals
# family.timezone; the ld-config gate does not.

# One prompt for two rows: the morning and evening triage are the same
# producer on two clocks, so the text lives once.
TRIAGE_PROMPT = (
    "Run the ld-morning-triage producer now: surface the one most-important "
    "unaddressed inbound -- iMessage and Gmail, both read through Latch -- "
    "from the last 36h, post it to the kiosk as card 1, type alert, and "
    "return the alert text as the final response."
)

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
    },
    {
        "name": "ld-morning-updates",
        "card": 2,
        "type": "affirmation",
        "schedule": "0 7 * * *",
        "prompt": (
            "Run the ld-morning-updates affirmation producer now: gather today's "
            "calendar through Latch gog, compose the morning affirmation, and "
            "post it to the kiosk as card 2, type affirmation."
        ),
        "skill": "ld-morning-updates",
        "deliver": None,
    },
    {
        "name": "ld-morning-triage",
        "card": 1,
        "type": "alert",
        "schedule": "5 7 * * *",
        "prompt": TRIAGE_PROMPT,
        "skill": "ld-morning-triage",
        "deliver": "plow_chat:${PLOW_HOME_CHANNEL}",
    },
    {
        "name": "ld-evening-triage",
        "card": 1,
        "type": "alert",
        # 18:00 on the same clock the previous assistant fired at 18:02.
        "schedule": "0 18 * * *",
        "prompt": TRIAGE_PROMPT,
        # The morning sheet on a second clock: one sheet, one wrapper, two rows.
        "skill": "ld-morning-triage",
        "deliver": "plow_chat:${PLOW_HOME_CHANNEL}",
    },
    {
        "name": "ld-weekly-digest",
        "card": 4,
        "type": "digest",
        "schedule": "0 17 * * 0",
        "prompt": (
            "Run the ld-weekly-digest producer now: gather the week's calendar "
            "through Latch gog, compose the week-ahead digest, post it to the "
            "kiosk as card 4, type digest, and return the digest text as the "
            "final response."
        ),
        "skill": "ld-weekly-digest",
        # Native --deliver, unlike the nudge: the digest is weekly and
        # always has content, so relaying every final response fits; the
        # half-hourly nudge has quiet no-op runs and rides its script leg.
        "deliver": "plow_chat:${PLOW_HOME_CHANNEL}",
    },
    {
        "name": "ld-calendar-nudge",
        "card": 1,
        "type": "alert",
        "schedule": "20,50 * * * *",
        "prompt": (
            "Run the ld-calendar-nudge producer now: gather the next day's "
            "calendar through Latch gog, run the nudge filter, and if a "
            "meeting with other attendees starts within the lookahead window, "
            "post a kiosk reminder and message the owner over Plow Chat; a "
            "quiet tick is a no-op on both surfaces."
        ),
        "skill": "ld-calendar-nudge",
        # deliver stays None ON PURPOSE, unlike the digest: --deliver relays
        # EVERY final response, and this producer runs half-hourly with quiet
        # no-op ticks -- its chat leg lives in its post_nudge.py coordinator,
        # which keeps quiet runs silent by construction.
        "deliver": None,
    },
)


def require_timezone_agreement(config_path=LD_CONFIG, env=None):
    """Refuse to register if the config's zone is not the container's.

    Every schedule here is a bare cron expression and `hermes cron create` takes
    no per-job timezone, so jobs fire in the CONTAINER's zone -- the TZ this
    image set at boot -- while all three SKILL.md files promise 06:00 in
    `family.timezone`. Nothing else compares them: ld_config_gate.py checks only
    that the zone is non-blank, which a valid America/Chicago config satisfies
    while its cards land at 08:00 family time. Silent, and wrong in exactly the
    place a life assistant exists for.

    The env var, not /etc/localtime. Measured in the live container: the image's
    /etc/localtime points at Etc/UTC while TZ carries America/Los_Angeles, which
    is what Python and cron actually honour -- so reading the symlink would
    refuse every correct config.
    """
    env = os.environ if env is None else env
    container = (env.get("TZ") or "").strip()
    if not container:
        raise SystemExit(
            "refusing to register: TZ is empty in this container. The image sets "
            "it at boot from family.timezone, falling back to UTC, so an empty "
            "one means that step did not run and the schedules would fire in a "
            "zone nothing here can name."
        )
    path = pathlib.Path(config_path)
    try:
        family = json.loads(path.read_text())["family"]["timezone"]
    except FileNotFoundError:
        raise SystemExit(
            f"refusing to register: {path} is missing. The producers read their "
            "location and teams from it, and its family.timezone is what these "
            "schedules are written against."
        ) from None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(
            f"refusing to register: could not read family.timezone from {path} "
            f"({exc!r})."
        ) from exc

    # str(), because nothing upstream in this script has validated the config's
    # shape -- it does not call ld_config_gate.py -- so `"timezone": 42` reaches
    # here and would die on .strip() with an AttributeError instead of the
    # refusal that names both values.
    if str(family or "").strip() != container:
        raise SystemExit(
            f"refusing to register: {path} says family.timezone is "
            f"{family!r} but this container runs in {container!r}. Every schedule "
            "here is a bare cron expression and hermes cron create takes no "
            "per-job zone, so the cards would land at the wrong local hour -- "
            "silently. TZ is fixed at boot from family.timezone, so this means "
            "the config changed after the container started: restart it to pick "
            "the new zone up, or fix family.timezone if THAT is what is wrong."
        )


def registered_jobs(jobs_path=JOBS_FILE):
    """What is already scheduled, from hermes's own persisted state.

    Reads the file `hermes cron` writes rather than parsing `hermes cron list`.
    That listing is a human rendering nothing pins, and matching on its text
    needed a new guard every time it was wrong; here a name is a field and so is
    `enabled`/`paused_at` (both confirmed in tests/fixtures/hermes-cron-jobs.json,
    captured from a live agent).

    The one invariant carried from the retired seed installer, because it is the
    one with teeth: never read "I could not tell what is registered" as "nothing
    is" -- that re-registers every job and duplicates all of them. Kept by
    catching only FileNotFoundError, so an unreadable or unexpected file raises
    and stops the run rather than being handled into an empty dict.

    Returns {name: is_runnable}. A paused job is registered but will never fire,
    and the caller needs to tell those apart: re-registering it duplicates it,
    skipping it silently leaves a card that stops updating.
    """
    try:
        jobs = json.loads(pathlib.Path(jobs_path).read_text())["jobs"]
    except FileNotFoundError:
        # A fresh instance with nothing scheduled yet. The ONLY absence that
        # means "nothing is registered" -- every other failure propagates, which
        # is the invariant: an unreadable or unexpected schedule must stop the
        # run, not read as empty and duplicate every job.
        return {}
    # Subscript, not .get with a fallback. Every entry hermes writes carries
    # both -- confirmed against the live agent's jobs.json and pinned by
    # tests/fixtures/hermes-cron-jobs.json -- so a default would be semantics
    # for a shape that does not occur, and the wrong semantics if it ever did:
    # defaulting `enabled` to True reads a disabled producer as runnable,
    # silently. A KeyError here is loud, at bring-up, in front of the operator.
    return {
        job["name"]: bool(job["enabled"]) and not job["paused_at"]
        for job in jobs
    }


def resolve_deliver(deliver, env=None):
    """Expand every ${VAR} in a delivery target from the container environment.

    The chat uid is not this repo's to know -- it is whichever chat the owner
    holds with this agent -- so it can never be a literal here. First boot
    resolves it from the credential the host dropped in and publishes it as
    PLOW_HOME_CHANNEL, which every service and every `with-contenv` caller
    inherits. There is no second source: nothing writes these names to a file
    on this image, and a file the agent can write is not a place to look for
    the chat its own messages are addressed to.

    An unset or blank variable REFUSES loudly: hermes would accept the
    half-expanded or empty target and the digest's chat leg would drop
    silently, every Sunday, in front of nobody -- the exact trap the old
    tripwire test existed to catch.

    An explicit `env` is the test-injection override, taken alone.
    """
    values = os.environ if env is None else env
    source = "the container environment" if env is None else "the injected env"

    def expand(match):
        name = match.group(1)
        value = (values.get(name) or "").strip()
        if not value:
            raise SystemExit(
                f"refusing to register: deliver target {deliver!r} needs {name}, "
                f"which is unset or blank in {source}. Registering without it "
                "would create a chat leg that silently delivers nowhere."
            )
        return value

    return re.sub(r"\$\{(\w+)\}", expand, deliver)


def create_argv(job, env=None):
    """The --deliver arm serves one live shape: a row whose every run has
    content, where relaying the final response IS the chat leg -- the weekly
    digest and the two triage runs. A quiet-run producer must not take it --
    --deliver relays every final response, no-ops included -- which is why
    the nudge's deliver is None and its chat leg lives in post_nudge.py."""
    argv = [HERMES, "cron", "create", job["schedule"], job["prompt"], "--name", job["name"]]
    if job["skill"]:
        argv += ["--skill", job["skill"]]
    if job["deliver"]:
        argv += ["--deliver", resolve_deliver(job["deliver"], env)]
    return argv


def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True)


def main(argv=None, runner=_run, jobs_path=JOBS_FILE, config_path=LD_CONFIG, env=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    if not shutil.which(HERMES) and not os.path.exists(HERMES):
        raise SystemExit(f"{HERMES} not found -- run this inside the agent container")

    require_timezone_agreement(config_path, env)
    registered = registered_jobs(jobs_path)
    paused = []

    for job in JOBS:
        if job["name"] in registered:
            if registered[job["name"]]:
                print(f"already present, skipped: {job['name']}")
            else:
                # Neither action is right -- re-registering duplicates the job,
                # skipping leaves a card that stops updating -- so it is left
                # alone and reported. Collected so the other producers still get
                # registered; the run fails at the end.
                print(
                    f"WARNING: {job['name']} is registered but PAUSED -- it will "
                    "never fire, and this leaves it alone rather than "
                    f"duplicating it. Resume it: {HERMES} cron resume {job['name']}"
                )
                paused.append(job["name"])
            continue
        proc = runner(create_argv(job, env))
        if proc.returncode != 0:
            raise SystemExit(
                f"could not register {job['name']}:\n{proc.stdout}\n{proc.stderr}"
            )
        print(f"registered: {job['name']} ({job['schedule']})")

    if paused:
        raise SystemExit(
            f"registered what was missing, but {len(paused)} producer(s) are "
            f"PAUSED and will never fire: {', '.join(paused)} -- "
            f"{HERMES} cron resume <name>"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
