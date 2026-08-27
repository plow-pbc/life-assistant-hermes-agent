"""The cron spec is data, so it is checked like data.

Every assertion here is about a failure that is quiet at registration time and
only shows up as a dashboard behaving wrongly hours later -- a job that fires on
the wrong clock, a blocked producer registered against a data source it does not
have, a delivery target that would be silently dropped because the machinery to
expand it was deleted as unreachable.
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def spec():
    path = ROOT / "ld-dashboard" / "scripts" / "register_crons.py"
    loader = importlib.util.spec_from_file_location("register_crons", path)
    mod = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(mod)
    return mod


LIVE_NAMES = {"ld-weather", "ld-sports"}


# The whole job contract, as one table. name -> (card, type, is-blocked).
#
# This is what a stack of Markdown parsers used to reach for indirectly: the
# viewer's card map read out of kiosk-protocol.md, SKILL.md's and README's
# restatements of it, and the pointers between those documents. All of that
# guarded non-executable operator prose with test machinery, and made ordinary
# documentation edits fail the suite. The prose is for humans; this is the part
# that decides which tile a producer overwrites, so this is the part pinned.
#
# 1=alert, 2=affirmation, 3=weather, 4=digest, 5=sports is the viewer's mapping
# (ld-shared/references/kiosk-protocol.md) -- the viewer's wire contract, which
# this table restates. Nothing guards that restatement, and nothing needs to:
# both upstreams are being archived, which is the whole reason these files came
# in-tree, so there is no re-vendor to drift from. Card 1 is the only one a
# producer shares, and only because triage and calendar-nudge are both alerts.
JOB_CONTRACT = {
    "ld-weather":         (3, "weather",     False),
    "ld-sports":          (5, "sports",      False),
    "ld-morning-updates": (2, "affirmation", True),
    "ld-morning-triage":  (1, "alert",       True),
    "ld-weekly-digest":   (4, "digest",      True),
    "ld-calendar-nudge":  (1, "alert",       True),
}


def test_the_job_contract_is_exactly_this():
    """One assertion for the whole spec: which producers exist, which slot each
    writes, and which can run.

    An exact set, so it catches a renumbered card (a producer silently
    overwriting another's tile), a retyped one (the right slot rendering the
    wrong way), a producer added or dropped, and a blocker cleared or
    introduced -- the things every separate check here used to cover between
    them, without reading a single Markdown file."""
    assert {
        (j["name"], j["card"], j["type"], bool(j["blocked"])) for j in spec().JOBS
    } == {(name, card, type_, blocked) for name, (card, type_, blocked) in JOB_CONTRACT.items()}


def test_exactly_the_two_producers_with_a_public_data_source_are_live():
    """The other four read Gmail, Google Calendar or Slack, and plow-connectors
    is dropped. Registering one would schedule a turn that cannot succeed, and
    its 06:00 failure would read as a producer bug rather than a missing
    connector."""
    mod = spec()
    assert {j["name"] for j in mod.LIVE} == LIVE_NAMES
    assert len(mod.JOBS) == 6, "all six stay in the spec; only two are registered"
    assert len(mod.BLOCKED) == 4


@pytest.mark.parametrize("job", spec().BLOCKED, ids=lambda j: j["name"])
def test_every_blocked_producer_names_why(job):
    """A blocked entry with no reason is indistinguishable from an oversight, and
    the next person to read this has to rediscover which of the two blockers it
    is waiting on."""
    reason = job["blocked"]
    assert reason and reason.strip()
    assert "latch#183" in reason or "iMessage" in reason, (
        f"{job['name']} must name its blocker: latch#183 for the Google "
        f"producers, the iMessage rewrite for triage -- got {reason!r}"
    )


@pytest.mark.parametrize("job", spec().JOBS, ids=lambda j: j["name"])
def test_no_schedule_carries_a_timezone(job):
    """`hermes cron create` takes no per-job zone -- jobs fire in the container's,
    which is agent-mgr's AGENT_TZ. A tz written into a schedule here is not
    rejected by anything; it is simply ignored, so the job runs at the wrong hour
    while the spec claims otherwise."""
    schedule = job["schedule"]
    assert not re.search(r"[A-Za-z]+/[A-Za-z_]+", schedule), (
        f"{job['name']} schedule {schedule!r} names a timezone; the container's "
        "AGENT_TZ is the only zone there is"
    )
    assert re.fullmatch(r"[\d*,/\- ]+", schedule), (
        f"{job['name']} schedule {schedule!r} is not a plain cron expression"
    )


def _cfg(tmp_path, zone="America/Los_Angeles"):
    """An ld-config for main(), agreeing with the container zone its caller passes.

    Every main() test supplies one: registration refuses outright when the
    config's family.timezone is not the container's, and a test that did not
    would be asserting that refusal instead of the behaviour it names."""
    config = tmp_path / "ld-config.json"
    config.write_text(json.dumps({"family": {"timezone": zone}}))
    return config


def _jobs_file(tmp_path, jobs):
    """A jobs.json the way hermes writes it."""
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps({"jobs": list(jobs), "updated_at": "2026-08-27T00:00:00Z"}))
    return path


def test_a_missing_jobs_file_is_an_empty_schedule_not_an_unreadable_one(tmp_path):
    """A fresh instance has no jobs.json, and its absence READS as an empty
    schedule, and NOTHING distinguishes that from a wrong JOBS_FILE -- both
    raise the same ENOENT. That is a decision: one operator-run instance, one
    path, so a wrong one is a code edit rather than a configuration mistake, and
    the guards that told them apart cost more than the fault they fenced.

    What reading the file buys over the listing it replaced is elsewhere: the
    listing could not tell an empty schedule from a format it could not parse,
    and the notice-sniffing that told them apart was itself pinned to a
    rendering."""
    assert spec().registered_jobs(tmp_path / "nope.json") == {}


def test_a_paused_job_counts_as_registered_but_not_as_runnable(tmp_path):
    """Two different answers the caller needs to tell apart: re-registering a
    paused job duplicates it, and silently skipping it leaves a card that never
    updates again."""
    mod = spec()
    path = _jobs_file(tmp_path, [
        {"name": "ld-weather", "enabled": True, "paused_at": None},
        {"name": "ld-sports", "enabled": True, "paused_at": "2026-08-26T12:00:00Z"},
        {"name": "ld-old", "enabled": False, "paused_at": None},
    ])
    assert mod.registered_jobs(path) == {
        "ld-weather": True, "ld-sports": False, "ld-old": False
    }


def test_each_live_job_attaches_its_own_skill():
    """Without --skill the scheduled turn has to find the producer by name in a
    directory of skills, and a near-miss posts nothing rather than failing."""
    for job in spec().LIVE:
        assert job["skill"] == job["name"]
        assert (ROOT / job["skill"] / "SKILL.md").is_file()


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class FakeHermes:
    """Records every argv, and WRITES what it creates into the jobs file.

    Writing is not decoration. register_crons reads back the first job it
    creates to prove the path it is reading is the path hermes writes -- so a
    fake that only records argv would fail that check, and a fake that made the
    check pass by not having one would hide the defect it exists to catch. The
    round that removed FakeHermes' fabricated `cron list` rendering learned the
    general form: a fake of a tool that does not quite exist is where defects go
    to hide."""

    def __init__(self, jobs_path, create_rc=0):
        self.jobs_path = Path(jobs_path)
        self.create_rc = create_rc
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        if self.create_rc == 0 and "create" in argv:
            name = argv[argv.index("--name") + 1]
            existing = (
                json.loads(self.jobs_path.read_text())["jobs"]
                if self.jobs_path.exists() else []
            )
            existing.append({"name": name, "enabled": True, "paused_at": None})
            self.jobs_path.write_text(
                json.dumps({"jobs": existing, "updated_at": "2026-08-27T00:00:00Z"})
            )
        return _Proc(self.create_rc, "", "boom" if self.create_rc else "")

    @property
    def created(self):
        return [a[a.index("--name") + 1] for a in self.calls if "create" in a]


def test_a_run_registers_only_the_live_jobs_that_are_missing(monkeypatch, capsys, tmp_path):
    mod = spec()
    monkeypatch.setattr(mod.shutil, "which", lambda _: mod.HERMES)
    path = _jobs_file(tmp_path, [{"name": "ld-weather", "enabled": True}])
    fake = FakeHermes(path)
    mod.main([], runner=fake, jobs_path=path, config_path=_cfg(tmp_path), env={"TZ": "America/Los_Angeles"})
    assert fake.created == ["ld-sports"], "the already-registered job must be skipped"
    assert "already present, skipped: ld-weather" in capsys.readouterr().out


def test_a_paused_job_is_warned_about_rather_than_skipped_or_duplicated(
    monkeypatch, capsys, tmp_path
):
    """Exiting 0 on a paused producer reports a clean run over a card that never
    updates again; re-registering it duplicates the job. Neither is right, so it
    says so and leaves it alone."""
    mod = spec()
    monkeypatch.setattr(mod.shutil, "which", lambda _: mod.HERMES)
    path = _jobs_file(tmp_path, [
        {"name": "ld-weather", "enabled": True, "paused_at": "2026-08-26T12:00:00Z"}
    ])
    fake = FakeHermes(path)
    # Non-zero, and only at the END: the other producers still get registered,
    # but an unattended re-provision must not read this run as success -- the
    # exit code is the only signal that reaches one.
    with pytest.raises(SystemExit) as exit_:
        mod.main([], runner=fake, jobs_path=path, config_path=_cfg(tmp_path), env={"TZ": "America/Los_Angeles"})
    assert "PAUSED" in str(exit_.value) and "ld-weather" in str(exit_.value)
    assert fake.created == ["ld-sports"], "a paused job must not be re-registered"
    out = capsys.readouterr().out
    assert "PAUSED" in out and "hermes cron resume ld-weather" in out


def test_no_blocked_job_is_ever_created(monkeypatch, tmp_path):
    """A blocked producer's body is not in this repo; scheduling it would fire a
    turn that cannot succeed, and the 06:00 failure would read as a producer bug
    rather than a missing connector."""
    mod = spec()
    monkeypatch.setattr(mod.shutil, "which", lambda _: mod.HERMES)
    path = tmp_path / "none.json"
    fake = FakeHermes(path)
    mod.main([], runner=fake, jobs_path=path, config_path=_cfg(tmp_path), env={"TZ": "America/Los_Angeles"})
    assert set(fake.created) == LIVE_NAMES
    blocked = {j["name"] for j in mod.BLOCKED}
    assert not blocked & set(fake.created)


def test_a_failed_create_aborts_rather_than_continuing(monkeypatch, tmp_path):
    """Registering one of a pair and reporting success leaves a half-configured
    dashboard that looks configured."""
    mod = spec()
    monkeypatch.setattr(mod.shutil, "which", lambda _: mod.HERMES)
    with pytest.raises(SystemExit):
        mod.main([], runner=FakeHermes(tmp_path / "none.json", create_rc=1),
                 jobs_path=tmp_path / "none.json", config_path=_cfg(tmp_path), env={"TZ": "America/Los_Angeles"})


@pytest.mark.parametrize("job", spec().JOBS, ids=lambda j: j["name"])
def test_no_prompt_names_a_card_other_than_the_one_the_spec_assigns(job):
    """The card number lived only as prose inside a prompt string, so a producer
    renumbered onto a card another one already owns would go unnoticed. It is
    data now, and this keeps the prompt agreeing with it.

    Conditional, because upstream is not uniform: five prompts name their card
    and ld-calendar-nudge's does not -- it says "post a kiosk reminder" and
    leaves the slot to its SKILL.md. Requiring a card in every prompt would mean
    rewording a blocked producer's instruction to satisfy a test, which is the
    tail wagging the dog. Disagreement is the real risk, and that is what this
    catches."""
    named = {int(n) for n in re.findall(r"\bcard (\d+)", job["prompt"])}
    assert named <= {job["card"]}, (
        f"{job['name']} is card {job['card']} in the spec but its prompt names "
        f"{sorted(named)}"
    )
    # The type is the other half, and promoting only the number left the same
    # prose-drift one field over: "card 3, type sports" satisfied the number
    # check while writing the wrong tile.
    typed = set(re.findall(r"\btype (\w+)", job["prompt"]))
    assert typed <= {job["type"]}, (
        f"{job['name']} is type {job['type']!r} in the spec but its prompt names "
        f"{sorted(typed)}"
    )
    if named:
        assert f"card {job['card']}, type {job['type']}" in job["prompt"], (
            f"{job['name']} names a card, so it must name the pair the viewer keys on"
        )


FIXTURE = ROOT / "tests" / "fixtures" / "hermes-cron-jobs.json"


def test_the_reader_handles_a_real_captured_jobs_file():
    """Against bytes a live hermes actually wrote, not a shape this repo invented.

    `name` is the only field whose absence raises -- registered_jobs() keys on
    it. `enabled` and `paused_at` are what the answer is COMPUTED from, and
    both default silently, so losing either returns a silently wrong answer --
    a paused producer coming back runnable -- rather than a refusal. Every
    other test here builds its fixtures from those same names, so they would
    all agree with each other and with nothing else; only this one can tell
    whether the reader reads hermes.

    Captured from Hermes Agent v0.19.0 (2026.7.20), values scrubbed because the
    field names are the contract. See tests/fixtures/README.md."""
    registered = spec().registered_jobs(FIXTURE)
    assert registered == {"<scrubbed-name>": True}


def test_the_captured_fixture_still_carries_the_fields_the_reader_needs():
    """If a re-capture from a newer hermes drops one of these, this says so in
    one line -- rather than the reader aborting at 06:00 on a real instance with
    'the format changed' and nobody knowing which field went."""
    entry = json.loads(FIXTURE.read_text())["jobs"][0]
    for field in ("name", "enabled", "paused_at"):
        assert field in entry, (
            f"the captured hermes format has no {field!r} -- registered_jobs() "
            "reads it, and for enabled/paused_at that is a silently wrong answer "
            "rather than a raise"
        )


def test_a_paused_job_is_not_runnable(tmp_path):
    """`enabled` and `paused_at` are both in the captured fixture, so this reads
    what hermes writes rather than guessing. A paused producer reported as
    healthy is the stale card the WARNING exists to catch."""
    mod = spec()
    path = _jobs_file(tmp_path, [
        {"name": "by-paused-at", "enabled": True, "paused_at": "2026-08-26T12:00:00Z"},
        {"name": "by-enabled", "enabled": False, "paused_at": None},
        {"name": "running", "enabled": True, "paused_at": None, "state": "scheduled"},
    ])
    assert mod.registered_jobs(path) == {
        "by-paused-at": False, "by-enabled": False, "running": True
    }


def test_no_live_job_needs_a_delivery_target():
    """The tripwire that replaced the resolver.

    Both live producers post their card over the kiosk POST -- the card IS the
    delivery -- so the ${VAR} expansion machinery `ld-calendar-nudge` needed was
    reachable only from a blocked row. It was deleted rather than carried as
    roadmap inventory, and this is what fires on the day a job carrying a
    delivery target goes live: whoever unblocks it writes the resolver then,
    against a requirement they can see, instead of inheriting an unreachable one
    nobody has run."""
    # Every row, not just the live ones. The deleted tests asserted no row
    # carried a literal chat id, and this repo is shared by more than one
    # person -- so a blocked row picking up `plow_chat:cht_realuid` would
    # commit one owner's chat into the tree with the suite still green.
    for job in spec().JOBS:
        deliver = job["deliver"]
        assert deliver is None or "${" in deliver, (
            f"{job['name']} names a literal delivery target {deliver!r} -- a chat "
            "uid belongs to one instance's activation, never to this tree"
        )
    for job in spec().LIVE:
        assert job["deliver"] is None, (
            f"{job['name']} is live and carries deliver={job['deliver']!r}, but "
            "create_argv() has no --deliver arm -- the target would be silently "
            "dropped. Add the expansion back (see git history for the deleted "
            "resolve_deliver) rather than removing this assertion."
        )


def test_the_blocked_nudge_still_records_the_target_it_will_need():
    """Deleting the resolver must not delete the requirement."""
    nudge = next(j for j in spec().JOBS if j["name"] == "ld-calendar-nudge")
    assert nudge["blocked"] and nudge["deliver"] == "plow_chat:${PLOW_CHAT_CHAT_UID}"


def test_a_config_zone_that_is_not_the_containers_refuses_to_register(tmp_path):
    """The gate calls a non-blank family.timezone valid, and it is -- structurally.

    What nothing checked is agreement. Every schedule here is a bare cron
    expression and `hermes cron create` takes no per-job zone, so a perfectly
    valid America/Chicago config on a Los_Angeles container puts the 06:00 cards
    on the wall at 08:00 family time, silently, while all three SKILL.md files
    promise 06:00. That is wrong in exactly the place a life assistant exists
    for, and it is the invariant the issue asks for by name."""
    mod = spec()
    config = tmp_path / "ld-config.json"
    config.write_text(json.dumps({"family": {"timezone": "America/Chicago"}}))

    with pytest.raises(SystemExit) as excinfo:
        mod.require_timezone_agreement(config, {"TZ": "America/Los_Angeles"})
    message = str(excinfo.value)
    assert "America/Chicago" in message and "America/Los_Angeles" in message
    assert "AGENT_TZ" in message, "the message has to name what to fix"

    # Agreement passes.
    mod.require_timezone_agreement(config, {"TZ": "America/Chicago"})


def test_the_container_zone_comes_from_TZ_not_etc_localtime(tmp_path):
    """A zone no host machine will have as its local time.

    Measured in the live container: /etc/localtime points at Etc/UTC while TZ
    carries America/Los_Angeles, and TZ is what Python and cron honour -- so
    reading the symlink would refuse every correct config. Pinning that with a
    real zone would prove nothing: America/Los_Angeles agrees with an LA
    developer's /etc/localtime and disagrees with a UTC CI container, so the
    assertion would be about the HOST rather than about which source was read.
    Antarctica/Troll is nobody's local time, so agreement here is only possible
    if TZ was consulted."""
    mod = spec()
    config = tmp_path / "ld-config.json"
    config.write_text(json.dumps({"family": {"timezone": "Antarctica/Troll"}}))
    mod.require_timezone_agreement(config, {"TZ": "Antarctica/Troll"})

    with pytest.raises(SystemExit) as excinfo:
        mod.require_timezone_agreement(config, {"TZ": ""})
    assert "AGENT_TZ" in str(excinfo.value)


def test_main_refuses_before_creating_anything_when_the_zones_disagree(
    monkeypatch, tmp_path
):
    """That main() CALLS the check, which its other tests cannot show.

    They all pass a config that agrees, so they only prove it passes -- delete
    the call and every one of them stays green while registration quietly goes
    back to creating wrong-hour schedules. This is the wiring, and it is the
    whole value of the guard."""
    mod = spec()
    monkeypatch.setattr(mod.shutil, "which", lambda _: mod.HERMES)
    path = tmp_path / "none.json"
    fake = FakeHermes(path)

    with pytest.raises(SystemExit) as excinfo:
        mod.main([], runner=fake, jobs_path=path,
                 config_path=_cfg(tmp_path, "America/Chicago"),
                 env={"TZ": "America/Los_Angeles"})
    assert "America/Chicago" in str(excinfo.value)
    assert fake.created == [], "nothing may be registered against a wrong clock"


def test_a_missing_ld_config_refuses_rather_than_registering(tmp_path):
    """The producers read their location and teams from it, so a schedule
    registered without it fires into a failure every morning."""
    with pytest.raises(SystemExit) as excinfo:
        spec().require_timezone_agreement(tmp_path / "nope.json", {"TZ": "UTC"})
    assert "is missing" in str(excinfo.value)


@pytest.mark.parametrize("content", [
    "{not json",
    '{"schedules": []}',
    "",
    # A well-formed file whose ENTRY has no name. The docstring's claim that
    # `name` is the only field whose absence raises is load-bearing and was
    # asserted by nothing: swap job["name"] for job.get("name") and a nameless
    # entry becomes a None key -- it vanishes from the answer instead of
    # stopping the run, which is the invariant this test is named for.
    '{"jobs": [{"enabled": true, "paused_at": null}]}',
], ids=["malformed", "wrong-shape", "empty", "entry-without-a-name"])
def test_an_unreadable_schedule_is_not_read_as_an_empty_one(tmp_path, content):
    """The seed installer's one invariant, and the only one worth keeping.

    Reading "I could not tell what is registered" as "nothing is" re-registers
    every job and duplicates all of them. Only FileNotFoundError means a fresh
    instance; everything else propagates and stops the run, so this asserts the
    absence of a handler rather than the wording of one."""
    path = tmp_path / "jobs.json"
    path.write_text(content)
    with pytest.raises(Exception) as excinfo:
        spec().registered_jobs(path)
    assert not isinstance(excinfo.value, FileNotFoundError)


@pytest.mark.parametrize("config", [{}, {"family": {}}, {"family": "America/LA"},
                                    {"family": {"timezone": 42}}, []],
                         ids=["no-family", "no-timezone", "family-not-a-dict",
                              "timezone-not-a-string", "top-level-array"])
def test_a_config_without_a_usable_timezone_refuses_by_name(tmp_path, config):
    """register_crons.py does not call ld_config_gate.py, so nothing upstream in
    this script has checked the config's shape -- every one of these reaches the
    read directly. Without the full exception tuple they escape as a raw
    traceback or an AttributeError instead of the refusal that names what to
    fix."""
    path = tmp_path / "ld-config.json"
    path.write_text(json.dumps(config))
    with pytest.raises(SystemExit) as excinfo:
        spec().require_timezone_agreement(path, {"TZ": "America/Los_Angeles"})
    assert "family.timezone" in str(excinfo.value)
