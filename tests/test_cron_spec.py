"""The cron spec is data, so it is checked like data.

Every assertion here is about a failure that is quiet at registration time and
only shows up as a dashboard behaving wrongly hours later -- a job that fires on
the wrong clock, a delivery target naming someone else's chat, a blocked
producer registered against a data source it does not have.
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


def test_an_unusable_delivery_target_refuses_rather_than_registering():
    """`plow_chat:` and `plow_chat:${...}` are both accepted at create time and
    undeliverable at 06:00, so every way of arriving at one has to refuse while
    someone is watching -- and each fault has to name its own remedy, because
    they have different ones."""
    mod = spec()

    # Set but blank: the credential was never minted.
    with pytest.raises(SystemExit) as blank:
        mod.resolve_deliver("plow_chat:${PLOW_CHAT_CHAT_UID}", {"PLOW_CHAT_CHAT_UID": "  "})
    assert "PLOW_CHAT_CHAT_UID" in str(blank.value) and "activate" in str(blank.value)

    # Absent behaves the same, and deliberately so. Splitting them looked like
    # better attribution and mis-fired on the case that matters: before
    # `agent-mgr activate`, the variable is absent rather than blank, so the
    # un-activated instance took the "check your spelling" branch. One message
    # names both remedies; a typo in JOBS is caught statically below instead.
    with pytest.raises(SystemExit) as absent:
        mod.resolve_deliver("plow_chat:${PLOW_CHAT_CHT_UID}", {"PLOW_CHAT_CHAT_UID": "cht_abc"})
    assert "activate" in str(absent.value) and "spelling" in str(absent.value)

    # Shapes the substitution pattern cannot match at all. Before the result was
    # checked, these came back verbatim and registered a job delivering to a
    # literal ${...}.
    for spelling in ("plow_chat:${chat-uid}", "plow_chat:${a.b}", "plow_chat:${}"):
        with pytest.raises(SystemExit) as unexpanded:
            mod.resolve_deliver(spelling, {})
        assert "unexpanded" in str(unexpanded.value), spelling

    # Lowercase now goes THROUGH the substitution rather than past it.
    assert mod.resolve_deliver("plow_chat:${plow_chat_chat_uid}",
                               {"plow_chat_chat_uid": "cht_low"}) == "plow_chat:cht_low"
    assert mod.resolve_deliver("plow_chat:${PLOW_CHAT_CHAT_UID}",
                               {"PLOW_CHAT_CHAT_UID": "cht_abc"}) == "plow_chat:cht_abc"




def _jobs_file(tmp_path, jobs):
    """A jobs.json the way hermes writes it."""
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps({"jobs": list(jobs), "updated_at": "2026-08-27T00:00:00Z"}))
    return path


def test_a_missing_jobs_file_is_an_empty_schedule_not_an_unreadable_one(tmp_path):
    """A fresh instance has no jobs.json, and its absence READS as an empty
    schedule -- it is not by itself unambiguous, since a wrong path raises the
    same ENOENT. The home and cron-directory checks catch the wrong path before
    anything is created, and verify_landed() catches whatever survives both.

    What reading the file buys over the listing it replaced is elsewhere: the
    listing could not tell an empty schedule from a format it could not parse,
    and the notice-sniffing that told them apart was itself pinned to a
    rendering."""
    assert spec().registered_jobs(tmp_path / "nope.json") == {}


def test_a_malformed_jobs_file_refuses_rather_than_reading_as_empty(tmp_path):
    """Reading "I cannot tell" as "nothing is registered" duplicates every job.
    That is the one invariant carried over from the seed installer, and it is
    the only guard the file-based seam still needs."""
    mod = spec()
    bad = tmp_path / "jobs.json"

    bad.write_text("{not json")
    with pytest.raises(SystemExit) as broken:
        mod.registered_jobs(bad)
    assert "duplicate" in str(broken.value)

    bad.write_text(json.dumps({"schedules": []}))
    with pytest.raises(SystemExit) as shape:
        mod.registered_jobs(bad)
    assert "format changed" in str(shape.value)


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


def test_a_near_miss_name_is_simply_a_different_key(tmp_path):
    """The whole-word matching this replaced existed because a substring search
    over a rendering could confuse `ld-weather-v2` with `ld-weather`, and because
    every prompt in the spec contains its own producer name. On a parsed field
    neither is expressible."""
    mod = spec()
    path = _jobs_file(tmp_path, [
        {"name": "ld-weather-v2", "enabled": True},
        {"name": "other", "enabled": True,
         "prompt": "Run the ld-weather producer now: ... card 3, type weather."},
    ])
    registered = mod.registered_jobs(path)
    assert not mod.is_present(registered, "ld-weather")
    assert mod.is_present(registered, "ld-weather-v2")


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


ENV = {"PLOW_CHAT_CHAT_UID": "cht_test"}


def test_a_run_registers_only_the_live_jobs_that_are_missing(monkeypatch, capsys, tmp_path):
    mod = spec()
    monkeypatch.setattr(mod.shutil, "which", lambda _: mod.HERMES)
    path = _jobs_file(tmp_path, [{"name": "ld-weather", "enabled": True}])
    fake = FakeHermes(path)
    mod.main([], runner=fake, env=ENV, jobs_path=path)
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
        mod.main([], runner=fake, env=ENV, jobs_path=path)
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
    mod.main([], runner=fake, env=ENV, jobs_path=path)
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
                 env=ENV, jobs_path=tmp_path / "none.json")


def test_dry_run_creates_nothing_and_still_reports_what_is_already_there(
    monkeypatch, capsys, tmp_path
):
    """The preview must agree with the real run. It used to skip the listing
    entirely and print `would register` for a job that already existed."""
    mod = spec()
    monkeypatch.setattr(mod.shutil, "which", lambda _: mod.HERMES)
    path = _jobs_file(tmp_path, [{"name": "ld-weather", "enabled": True}])
    fake = FakeHermes(path)
    mod.main(["--dry-run"], runner=fake, env=ENV, jobs_path=path)
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


def viewer_slots():
    """The card->type map, PARSED from kiosk-protocol.md's Card map table.

    Restating it here would have been the fourth hand-kept copy of the same
    pairing -- the protocol doc, this constant, each job's `type`, and SKILL.md's
    `3 · weather` column -- which is the prose drift promoting `type` to data was
    meant to end, moved one file over. Renumber the protocol and this goes red.

    Parses without asserting: this runs at import, so a bare assert here would
    fail COLLECTION of the whole module and read as "the tests are broken"
    rather than "the protocol table changed". The count is checked by
    test_the_protocol_card_map_still_parses instead, which goes red alone."""
    table = (ROOT / "ld-shared" / "references" / "kiosk-protocol.md").read_text()
    rows = re.findall(r"^\|\s*(\d)\s*\|\s*`(\w+)`\s*\|", table, re.M)
    return {int(card): type_ for card, type_ in rows}


VIEWER_SLOTS = viewer_slots()


def test_the_protocol_card_map_still_parses():
    """A reformatted table must fail loudly, not yield an empty map that agrees
    with everything -- an empty VIEWER_SLOTS makes every pinned-map assertion
    below vacuous."""
    assert len(VIEWER_SLOTS) == 5, (
        f"expected 5 card rows in kiosk-protocol.md's Card map, parsed "
        f"{len(VIEWER_SLOTS)} -- the table was reformatted and this map is blind"
    )



def test_the_spec_uses_the_viewers_slot_map_and_shares_only_the_alert_card():
    """Triage and calendar-nudge deliberately share card 1 -- both are alerts --
    and that is the only sharing there is. Every other producer owns its slot, so
    a renumbering silently overwrites another producer's tile."""
    mod = spec()
    by_card = {}
    for job in mod.JOBS:
        assert VIEWER_SLOTS[job["card"]] == job["type"], (
            f"{job['name']} claims card {job['card']} as {job['type']!r}, but the "
            f"viewer renders that slot as {VIEWER_SLOTS[job['card']]!r}"
        )
        by_card.setdefault(job["card"], []).append(job["name"])
    assert sorted(by_card) == sorted(VIEWER_SLOTS)
    shared = {c: n for c, n in by_card.items() if len(n) > 1}
    assert shared == {1: ["ld-morning-triage", "ld-calendar-nudge"]}


@pytest.mark.parametrize("job", spec().JOBS, ids=lambda j: j["name"])
def test_the_skill_table_still_agrees_with_the_spec(job):
    """SKILL.md restates the schedules for a human reader, and a restatement
    drifts. Name and schedule only -- mechanical enough not to break on prose."""
    table = (ROOT / "ld-dashboard" / "SKILL.md").read_text()
    assert f"`{job['name']}`" in table
    assert f"`{job['schedule']}`" in table
    # The card/type column too -- names and schedules alone let the slot half
    # drift, which is how the protocol map came to have four copies.
    assert f"{job['card']} · {job['type']}" in table


# The half this repo does not declare, because agent-mgr writes it rather than
# the operator: everything else is parsed out of .env.example below. Named by
# hand because there is no file here to parse it from -- each with its origin,
# so a reader can check the claim.
SET_BY_AGENT_MGR = {
    "PLOW_CHAT_BASE_URL",       # derived by `agent-mgr activate`
    "PLOW_CHAT_HOME_CHANNEL",   # derived by `agent-mgr activate`
    "TZ",                       # agent-mgr templates/compose.yml, from AGENT_TZ
    "AGENT_TZ",                 # the instance dotenv, read after the home resolves
}


def supplied_by_the_environment():
    """What a ${VAR} in JOBS may name.

    The declared half is PARSED from .env.example rather than restated -- this
    is the only typo guard left, since the runtime absent-branch was removed in
    favour of catching it statically, and a hand-kept copy of an environment
    contract is the drift this file spent a round eliminating elsewhere.

    Erring restrictive is the failure mode that bites: a name this set omits
    refuses a LEGITIMATE target at registration. DASHBOARD_ENDPOINT_URL and
    DASHBOARD_TOKEN were exactly that until they were declared in .env.example,
    which is where they belong anyway -- post_to_kiosk.py reads both from the
    container environment."""
    declared = re.findall(r"^([A-Z][A-Z0-9_]*)=", (ROOT / ".env.example").read_text(), re.M)
    assert declared, ".env.example declares no keys -- has the format changed?"
    return set(declared) | SET_BY_AGENT_MGR


@pytest.mark.parametrize("job", spec().JOBS, ids=lambda j: j["name"])
def test_every_placeholder_in_the_spec_names_a_real_variable(job):
    """A misspelled ${VAR} is a static defect, so it fails here rather than at
    registration -- which is what lets the runtime message speak plainly about
    the credential instead of hedging between two causes."""
    for name in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", job["deliver"] or ""):
        assert name in supplied_by_the_environment(), (
            f"{job['name']} delivers to ${{{name}}}, which nothing supplies -- "
            "typo, or add it to .env.example and to this set"
        )


def test_a_create_that_does_not_land_in_the_jobs_file_aborts(monkeypatch, tmp_path):
    """The floor under "absent file means fresh instance".

    Nothing pins JOBS_FILE -- not a fixture, not a hermes version -- so a moved
    or wrong path looks exactly like an empty schedule, on every run, forever,
    and registers duplicates each time in silence. Reading back the first job
    actually created turns that into a loud failure on run one. A runner that
    reports success without writing is precisely the wrong-path case."""
    mod = spec()
    monkeypatch.setattr(mod.shutil, "which", lambda _: mod.HERMES)

    class LyingHermes:
        def __call__(self, argv):
            return _Proc(0, "", "")

    with pytest.raises(SystemExit) as excinfo:
        mod.main([], runner=LyingHermes(), env=ENV, jobs_path=tmp_path / "wrong.json")
    assert "not where this hermes persists jobs" in str(excinfo.value)


@pytest.mark.parametrize("jobs,expected", [
    ([{"job_name": "ld-weather", "enabled": True}], "no string `name`"),
    ([{"name": "", "enabled": True}], "no string `name`"),
    (["ld-weather"], "no string `name`"),
    ([{"name": "ld-weather", "state": "paused"}], "neither `enabled` nor `paused_at`"),
])
def test_a_renamed_field_aborts_rather_than_parsing_to_nothing(tmp_path, jobs, expected):
    """A container-only shape check leaves the entries open, and both renames
    are silent in the worst direction: dropping every entry hands back an empty
    map from a readable file -- register everything, again -- and losing the
    pause fields defaults a paused producer to runnable, which is the stale card
    the WARNING exists to catch."""
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps({"jobs": jobs, "updated_at": "2026-08-27T00:00:00Z"}))
    with pytest.raises(SystemExit) as excinfo:
        spec().registered_jobs(path)
    assert expected in str(excinfo.value)


FIXTURE = ROOT / "tests" / "fixtures" / "hermes-cron-jobs.json"


def test_the_reader_handles_a_real_captured_jobs_file():
    """Against bytes a live hermes actually wrote, not a shape this repo invented.

    The reader aborts on any entry missing `name`, or missing both `enabled` and
    `paused_at` -- a hard refusal resting on three field names. Every other test
    here builds its fixtures from those same names, so they would all agree with
    each other and with nothing else; only this one can tell whether the reader
    reads hermes. Captured from Hermes Agent v0.19.0 (2026.7.20), values scrubbed
    because the field names are the contract. See tests/fixtures/README.md."""
    registered = spec().registered_jobs(FIXTURE)
    assert registered == {"<scrubbed-name>": True}


def test_the_captured_fixture_still_carries_the_fields_the_reader_needs():
    """If a re-capture from a newer hermes drops one of these, this says so in
    one line -- rather than the reader aborting at 06:00 on a real instance with
    'the format changed' and nobody knowing which field went."""
    entry = json.loads(FIXTURE.read_text())["jobs"][0]
    for field in ("name", "enabled", "paused_at"):
        assert field in entry, (
            f"the captured hermes format has no {field!r} -- register_crons.py "
            "reads it, and the abort it raises would be correct but unexplained"
        )
    # Not an abort field -- the reader treats it as the second candidate pause
    # encoding -- but a re-capture that drops it deletes half the pause detection
    # silently, with every other test still green.
    assert "state" in entry, (
        "the captured hermes format has no 'state' -- register_crons.py reads it "
        "as the second pause encoding, and losing it is silent"
    )


def test_the_absent_branch_is_only_for_a_genuinely_missing_file(tmp_path):
    """Path.exists() swallows every OSError on 3.12+, which `just test` pins, so a
    permission denial on the cron directory came back False and read as a fresh
    instance -- registering every job again, silently. Only FileNotFoundError
    means "nothing scheduled yet".

    Proven with a DIRECTORY rather than a chmod. A chmod(0o000) row was the
    obvious way to write this and is uid-dependent: under any root runner -- a
    container, most CI images -- root bypasses the mode bits, the read succeeds,
    and the test goes red for a reason unrelated to the code while leaving this
    branch covered by nothing. IsADirectoryError is an OSError and never a
    FileNotFoundError, whoever runs it."""
    with pytest.raises(SystemExit) as excinfo:
        spec().registered_jobs(tmp_path)
    assert "duplicates every job" in str(excinfo.value)


def test_an_unmounted_home_refuses_before_creating_anything(tmp_path):
    """The pre-create half of the wrong-path check.

    verify_landed only fires AFTER a create has landed, so a retry there adds a
    second copy of that job each attempt -- "duplicate everything forever"
    becomes "duplicate one per attempt", which is smaller but not fixed.

    This is the FIRST of two levels: /opt/data is agent-mgr's one template mount,
    so its absence means the container is not wired correctly. The second level
    is the cron directory under it -- see
    test_a_wrong_cron_directory_under_a_good_home_also_refuses -- which catches
    the likelier fault of JOBS_FILE naming the wrong subdirectory under a home
    that mounted fine."""
    with pytest.raises(SystemExit) as excinfo:
        spec().registered_jobs(tmp_path / "no-home" / "cron" / "jobs.json")
    assert "agent home is not mounted" in str(excinfo.value)


def test_a_paused_job_is_recognised_under_either_encoding(tmp_path):
    """The capture settles the field NAMES; it came from a running job, so it
    cannot settle which field pausing moves. Reading both costs one term and
    keeps a paused producer from reporting as healthy -- the stale card the
    WARNING exists to catch -- if hermes flips `state` rather than `paused_at`."""
    mod = spec()
    path = _jobs_file(tmp_path, [
        {"name": "by-paused-at", "enabled": True, "paused_at": "2026-08-26T12:00:00Z"},
        {"name": "by-state", "enabled": True, "paused_at": None, "state": "paused"},
        # Casing, which is the half of the value guess that costs one token to
        # cover -- and the failure it would leave is the fail-open one, a paused
        # producer reported healthy. Without this row, reverting the casefold
        # leaves the suite green.
        {"name": "by-state-cased", "enabled": True, "paused_at": None, "state": "Paused"},
        # A non-string state must not raise on its way through casefold.
        {"name": "odd-state", "enabled": True, "paused_at": None, "state": 7},
        {"name": "by-enabled", "enabled": False, "paused_at": None},
        {"name": "running", "enabled": True, "paused_at": None, "state": "scheduled"},
    ])
    assert mod.registered_jobs(path) == {
        "by-paused-at": False, "by-state": False, "by-state-cased": False,
        "by-enabled": False, "odd-state": True, "running": True
    }


def test_a_dry_run_does_not_fail_over_a_paused_job_it_did_not_create(
    monkeypatch, capsys, tmp_path
):
    """A preview's contract is "change nothing", and its exit code is what a
    provisioning script gates on -- so failing here makes a clean preview
    indistinguishable from a failed real run."""
    mod = spec()
    monkeypatch.setattr(mod.shutil, "which", lambda _: mod.HERMES)
    path = _jobs_file(tmp_path, [
        {"name": "ld-weather", "enabled": True, "paused_at": "2026-08-26T12:00:00Z"}
    ])
    assert mod.main(["--dry-run"], runner=FakeHermes(path), env=ENV, jobs_path=path) == 0
    out = capsys.readouterr().out
    assert "would leave 1 paused producer(s) alone: ld-weather" in out


def test_a_wrong_cron_directory_under_a_good_home_also_refuses(tmp_path):
    """The likelier typo, and the one a home-only check sails past.

    /opt/data/crons/jobs.json passes the mounted-home stat, reads as an empty
    schedule, registers the first live job, and only then trips verify_landed --
    which is "duplicate one per attempt", recurring on every retry. That is the
    cost the pre-create check exists to remove, so both levels are checked."""
    home = tmp_path / "data"
    (home / "cron").mkdir(parents=True)
    with pytest.raises(SystemExit) as excinfo:
        spec().registered_jobs(home / "crons" / "jobs.json")
    msg = str(excinfo.value)
    assert "is mounted but" in msg and "wrong one" in msg
