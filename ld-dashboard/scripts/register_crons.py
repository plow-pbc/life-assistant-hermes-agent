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
an absent file reads as an empty schedule and a malformed one raises. See registered_jobs().

It runs INSIDE the container, where /opt/hermes/bin/hermes and that file live.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys

HERMES = "/opt/hermes/bin/hermes"
# Where `hermes cron` persists its jobs -- the same file the issue names as the
# reason this skill exists, because `agent-mgr restore` does not replay it.
JOBS_FILE = "/opt/data/cron/jobs.json"
# The producers' own config. Read here for one reason: every schedule below is a
# bare cron expression, and `hermes cron create` takes no per-job timezone.
LD_CONFIG = "/opt/data/ld/config.json"

# The spec. One row per producer, live or not.
#
# `deliver` is None for every card-only producer: the card IS the delivery, over
# the kiosk POST. ld-calendar-nudge also messages the owner, and it is blocked --
# so its target is recorded as the ${VAR} it will need and nothing expands it.
# The resolver that used to live here was reachable only from this one blocked
# row; see create_argv() for why it went, and
# test_no_live_job_needs_a_delivery_target for what fires when it is needed.
#
# No timezone anywhere. `hermes cron create` takes no per-job zone: jobs fire in
# the container's zone, which is agent-mgr's AGENT_TZ.
# require_timezone_agreement() below refuses to register unless that zone equals
# family.timezone; the ld-config gate does not.
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


def require_timezone_agreement(config_path=LD_CONFIG, env=None):
    """Refuse to register if the config's zone is not the container's.

    Every schedule here is a bare cron expression and `hermes cron create` takes
    no per-job timezone, so jobs fire in the CONTAINER's zone -- agent-mgr's
    AGENT_TZ -- while all three SKILL.md files promise 06:00 in
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
            "refusing to register: TZ is empty in this container. agent-mgr sets "
            "it from AGENT_TZ at create time, so an empty one means the schedules "
            "would fire in a zone nothing here can name."
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
            "silently. Fix whichever is wrong: AGENT_TZ in the instance dotenv "
            "(after `restore`, before `up` -- the zone reaches the container at "
            "create time), or family.timezone in the config."
        )


def require_handoff_dir_writable(config_path=LD_CONFIG, geteuid=os.geteuid):
    """Refuse to register if the agent cannot write beside its own config.

    The producers do not compose their tiles in the wrapper: the AGENT writes
    the composed HTML to /opt/data/ld/<bundle>-text with its file tool, and the
    wrapper reads that path and unlinks it on a successful send. So this
    directory has to be writable BY THE AGENT, not merely present -- and unlike
    the world-writable /tmp it replaced, nothing guarantees that. Root-owned
    directories under the home bind are a measured hazard, not a hypothetical:
    the runtime creates missing mountpoints inside the bind's source as root
    (plow-pbc/agent-mgr#44).

    Two steps, and the first is what makes the second mean anything. Refuse to
    run as root, then just TRY THE WRITE. A probe answers the question the agent
    will actually ask -- it covers the write bit, the execute bit a directory
    needs before anything can be created in it, a read-only mount, a full disk
    and an ACL, in one syscall and without enumerating any of them. Inspecting
    uid and mode instead means re-deriving that list by hand and getting it
    wrong; the version this replaced checked S_IWUSR without S_IXUSR, so an
    agent-owned 0600 passed while nothing could be created.

    The root refusal is not incidental. Root's probe succeeds on a directory the
    agent cannot write, so without it the check goes green on exactly the setup
    it exists to catch. `docker exec` without --user lands as uid 0, which is
    how an operator gets here; bring-up is documented through `agent-mgr agent`,
    which runs as the agent. Refusing turns a silent wrong answer into a loud
    one.
    """
    if geteuid() == 0:
        raise SystemExit(
            "refusing to register: this is running as root, and root can write "
            "directories the agent cannot -- so every check below would pass on "
            "the setup it exists to catch. Run it as the agent: `agent-mgr agent "
            "<name>` (a turn, the documented path), or `docker exec --user`."
        )
    handoff = pathlib.Path(config_path).parent
    probe = handoff / ".ld-handoff-probe"
    try:
        probe.touch()
        probe.unlink()
    except OSError as exc:
        raise SystemExit(
            f"refusing to register: the agent cannot create files in {handoff} "
            f"({exc.strerror}). The producers write their composed tile there, "
            "so every card would fail to post -- or worse, fall back to the "
            "shell and post intermittently, which looks fine from the kiosk. "
            f"Fix the directory so the agent owns it and can write it: as root "
            f"(`docker exec --user root`, or the host against the bind source) "
            f"`chown -R $HERMES_UID:$HERMES_GID {handoff}`, then `chmod u+wx "
            f"{handoff}`."
        ) from exc


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


def create_argv(job):
    """No --deliver arm, because no job that reaches here has one.

    Only LIVE jobs are ever created, and both of them post their card over the
    kiosk POST -- the card IS the delivery. `ld-calendar-nudge` is the one
    producer that also messages the owner, and it is blocked, so the expansion
    machinery its `deliver` field needed was unreachable code. The field stays as
    DATA on the blocked row so the requirement is not lost, and
    test_no_live_job_needs_a_delivery_target fires the day one becomes live."""
    argv = [HERMES, "cron", "create", job["schedule"], job["prompt"], "--name", job["name"]]
    if job["skill"]:
        argv += ["--skill", job["skill"]]
    return argv


def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True)


def main(
    argv=None,
    runner=_run,
    jobs_path=JOBS_FILE,
    config_path=LD_CONFIG,
    env=None,
    geteuid=os.geteuid,
):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    for job in BLOCKED:
        print(f"blocked, not registered: {job['name']} ({job['schedule']}) -- {job['blocked']}")

    if not shutil.which(HERMES) and not os.path.exists(HERMES):
        raise SystemExit(f"{HERMES} not found -- run this inside the agent container")

    require_timezone_agreement(config_path, env)
    require_handoff_dir_writable(config_path, geteuid)
    registered = registered_jobs(jobs_path)
    paused = []

    for job in LIVE:
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
        proc = runner(create_argv(job))
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
