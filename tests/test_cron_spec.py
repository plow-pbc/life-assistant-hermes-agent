"""The cron spec is data, so it is checked like data.

Every assertion here is about a failure that is quiet at registration time and
only shows up as a dashboard behaving wrongly hours later -- a job that fires on
the wrong clock, a delivery target naming someone else's chat, a blocked
producer registered against a data source it does not have.
"""
import importlib.util
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


@pytest.mark.parametrize("job", spec().JOBS, ids=lambda j: j["name"])
def test_no_delivery_target_is_a_literal_chat_id(job):
    """A chat uid is minted by one instance's activation. A literal here would
    message whoever the spec was written for -- on a repo explicitly shared by
    more than one person."""
    deliver = job["deliver"]
    if deliver is None:
        return
    assert "${" in deliver, (
        f"{job['name']} delivers to {deliver!r}, which pins one instance's chat"
    )
    assert not re.search(r"cht_[A-Za-z0-9_-]+", deliver)


def test_a_blank_delivery_variable_refuses_rather_than_registering():
    """`plow_chat:` is accepted at create time and undeliverable at 06:00. The
    refusal has to happen while someone is watching."""
    mod = spec()
    with pytest.raises(SystemExit) as excinfo:
        mod.resolve_deliver("plow_chat:${PLOW_CHAT_CHAT_UID}", {"PLOW_CHAT_CHAT_UID": "  "})
    assert "PLOW_CHAT_CHAT_UID" in str(excinfo.value)
    assert mod.resolve_deliver("plow_chat:${PLOW_CHAT_CHAT_UID}",
                               {"PLOW_CHAT_CHAT_UID": "cht_abc"}) == "plow_chat:cht_abc"


def test_dedup_reads_the_name_field_not_the_whole_listing():
    """Two ways a near-miss silently skips a real registration.

    A stale `ld-weather-v2` or `ld-weather.v2` counted as `ld-weather` present
    skips the real job and the card never updates. And every prompt in the spec
    contains its own producer name ("Run the ld-weather producer now..."), so a
    dedup key of "appears anywhere in the listing" matches a job's own prompt --
    which is why the listing is parsed into names rather than searched."""
    mod = spec()
    listing = "  7bf1 [active]\n    Name:      ld-weather-v2\n    Schedule:  0 6 * * *\n"
    assert mod.existing_names(lambda a: _Proc(0, listing)) == {"ld-weather-v2"}
    assert not mod.is_present({"ld-weather-v2"}, "ld-weather")
    assert not mod.is_present({"ld-weather.v2"}, "ld-weather")
    assert mod.is_present({"ld-weather"}, "ld-weather")

    prompt_only = "    Name:      other\n    Prompt: Run the ld-weather producer now\n"
    assert not mod.is_present(mod.existing_names(lambda a: _Proc(0, prompt_only)), "ld-weather")


def test_a_failed_cron_list_aborts_instead_of_registering_everything():
    """The invariant the seed installer learned: an empty snapshot read as
    "nothing exists" duplicates all six jobs."""
    mod = spec()
    with pytest.raises(SystemExit) as excinfo:
        mod.existing_names(lambda argv: _Proc(1, "", "connection refused"))
    assert "duplicate" in str(excinfo.value)


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
    """Records every argv, answers `cron list` with a canned listing.

    main() carries runner/env seams so what it DECIDES can be tested, not just
    its leaves. The dry-run defect that shipped in the first draft -- a preview
    that never checked what was already registered -- lived entirely in the
    orchestration, which every leaf test passed straight through."""

    def __init__(self, registered=(), create_rc=0):
        self.listing = "".join(f"  abc [active]\n    Name:      {n}\n" for n in registered)
        self.create_rc = create_rc
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[1:3] == ["cron", "list"]:
            return _Proc(0, self.listing)
        return _Proc(self.create_rc, "", "boom" if self.create_rc else "")

    @property
    def created(self):
        return [a[a.index("--name") + 1] for a in self.calls if "create" in a]


ENV = {"PLOW_CHAT_CHAT_UID": "cht_test"}


def test_a_run_registers_only_the_live_jobs_that_are_missing(monkeypatch, capsys):
    mod = spec()
    monkeypatch.setattr(mod.shutil, "which", lambda _: mod.HERMES)
    fake = FakeHermes(registered=["ld-weather"])
    mod.main([], runner=fake, env=ENV)
    assert fake.created == ["ld-sports"], "the already-registered job must be skipped"
    assert "already present, skipped: ld-weather" in capsys.readouterr().out


def test_no_blocked_job_is_ever_created(monkeypatch):
    """A blocked producer's body is not in this repo; scheduling it would fire a
    turn that cannot succeed, and the 06:00 failure would read as a producer bug
    rather than a missing connector."""
    mod = spec()
    monkeypatch.setattr(mod.shutil, "which", lambda _: mod.HERMES)
    fake = FakeHermes()
    mod.main([], runner=fake, env=ENV)
    assert set(fake.created) == LIVE_NAMES
    blocked = {j["name"] for j in mod.BLOCKED}
    assert not blocked & set(fake.created)


def test_a_failed_create_aborts_rather_than_continuing(monkeypatch):
    """Registering one of a pair and reporting success leaves a half-configured
    dashboard that looks configured."""
    mod = spec()
    monkeypatch.setattr(mod.shutil, "which", lambda _: mod.HERMES)
    with pytest.raises(SystemExit):
        mod.main([], runner=FakeHermes(create_rc=1), env=ENV)


def test_dry_run_creates_nothing_and_still_reports_what_is_already_there(monkeypatch, capsys):
    """The preview must agree with the real run. It used to skip the listing
    entirely and print `would register` for a job that already existed."""
    mod = spec()
    monkeypatch.setattr(mod.shutil, "which", lambda _: mod.HERMES)
    fake = FakeHermes(registered=["ld-weather"])
    mod.main(["--dry-run"], runner=fake, env=ENV)
    assert fake.created == []
    out = capsys.readouterr().out
    assert "already present, would skip: ld-weather" in out
    assert "would register: ld-sports" in out


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


def test_the_card_map_matches_the_viewer_and_only_the_alert_slot_is_shared():
    """1=alert, 2=affirmation, 3=weather, 4=digest, 5=sports is the viewer's
    pinned mapping. Triage and calendar-nudge deliberately share card 1 -- both
    are alerts -- and that is the only sharing there is."""
    mod = spec()
    by_card = {}
    for job in mod.JOBS:
        by_card.setdefault(job["card"], []).append(job["name"])
    assert sorted(by_card) == [1, 2, 3, 4, 5]
    shared = {c: n for c, n in by_card.items() if len(n) > 1}
    assert shared == {1: ["ld-morning-triage", "ld-calendar-nudge"]}


@pytest.mark.parametrize("job", spec().JOBS, ids=lambda j: j["name"])
def test_the_skill_table_still_agrees_with_the_spec(job):
    """SKILL.md restates the schedules for a human reader, and a restatement
    drifts. Name and schedule only -- mechanical enough not to break on prose."""
    table = (ROOT / "ld-dashboard" / "SKILL.md").read_text()
    assert f"`{job['name']}`" in table
    assert f"`{job['schedule']}`" in table
