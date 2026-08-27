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


def test_a_run_registers_only_the_live_jobs_that_are_missing(monkeypatch, capsys, tmp_path):
    mod = spec()
    monkeypatch.setattr(mod.shutil, "which", lambda _: mod.HERMES)
    path = _jobs_file(tmp_path, [{"name": "ld-weather", "enabled": True}])
    fake = FakeHermes(path)
    mod.main([], runner=fake, jobs_path=path)
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
        mod.main([], runner=fake, jobs_path=path)
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
    mod.main([], runner=fake, jobs_path=path)
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
                 jobs_path=tmp_path / "none.json")


def test_dry_run_creates_nothing_and_still_reports_what_is_already_there(
    monkeypatch, capsys, tmp_path
):
    """The preview must agree with the real run. It used to skip the listing
    entirely and print `would register` for a job that already existed."""
    mod = spec()
    monkeypatch.setattr(mod.shutil, "which", lambda _: mod.HERMES)
    path = _jobs_file(tmp_path, [{"name": "ld-weather", "enabled": True}])
    fake = FakeHermes(path)
    mod.main(["--dry-run"], runner=fake, jobs_path=path)
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
def test_both_restatements_of_the_spec_still_agree_with_it(job):
    """Two documents restate JOBS for human readers, and a restatement drifts.

    SKILL.md carries the full table — name, schedule, card and type. README.md
    carries only the four dark producers, to say what an owner has lost, so it
    is checked only for those rows and only for the fields it prints.

    Asserting the ROW in both, not the fields separately. A bare backticked
    ld-weather appears in prose anywhere in a 300-line README and passes while
    the table beside it is wrong; and three independent substring checks against
    SKILL.md all pass on a row whose name was copied onto a neighbour's schedule,
    since every asserted string is still somewhere in the file. The card map has
    been in four hand-kept copies before; two are pinned here and the third is
    parsed straight out of kiosk-protocol.md."""
    skill = (ROOT / "ld-dashboard" / "SKILL.md").read_text()
    skill_row = f"| `{job['name']}` | `{job['schedule']}` | {job['card']} · {job['type']} |"
    assert skill_row in skill, (
        f"SKILL.md has no row {skill_row!r} -- three separate substring checks "
        "would pass on a row whose name was copied onto a neighbour's schedule"
    )
    # The blocker column too. It restates latch#183 vs the iMessage rewrite, and
    # runtime/config.yaml was off by one on exactly that split a round ago.
    if job["blocked"]:
        # Extracted from the row's own text, and FAILING when it cannot be. A
        # two-way guess (anything without latch#NNN must be the iMessage
        # rewrite) holds only for today's four rows: a producer blocked on a
        # third thing -- a missing key, a retired upstream -- would be silently
        # checked for the wrong string and then misnamed in the failure message,
        # which is the hand-kept split this test exists to catch.
        tokens = re.findall(r"latch#\d+|iMessage", job["blocked"])
        assert tokens, (
            f"{job['name']} is blocked on something this check cannot name "
            f"({job['blocked']!r}) -- teach it the new blocker rather than "
            "letting the row go unchecked"
        )
        # `in`, not startswith, and a default. The assertion above proves the row
        # is somewhere in the text, not that a LINE begins with it -- indent the
        # table into a list item or a fenced block and startswith matches
        # nothing, so a bare next() raises StopIteration with no message and
        # throws away every crafted failure below.
        row_line = next((l for l in skill.splitlines() if skill_row in l), "")
        # ALL of them, not the leftmost. "needs the iMessage rewrite, tracked in
        # latch#183" is a plausible next edit, and a leftmost match would then
        # check the row for "iMessage" and report it missing its blocker while
        # the row names the one the spec records.
        for token in tokens:
            assert token in row_line, (
                f"SKILL.md's row for {job['name']} does not name {token}, which "
                f"its blocked text records ({job['blocked']!r})"
            )

        # README prints the dark producers; every blocked one needs its row. The
        # converse -- no row for anything else -- is
        # test_the_readme_dark_table_lists_the_blocked_producers_and_nothing_else.
        readme = (ROOT / "README.md").read_text()
        row = f"| `{job['name']}` | {job['card']} · {job['type']} |"
        assert row in readme, (
            f"README's dark-producer table has no row {row!r} -- it is the only "
            "place an owner reads what they lost, and nothing else checks it"
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
        mod.main([], runner=LyingHermes(), jobs_path=tmp_path / "wrong.json")
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
    assert mod.main(["--dry-run"], runner=FakeHermes(path), jobs_path=path) == 0
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


def test_the_paste_instruction_survives_in_every_document_that_promises_it():
    """The README's token check is load-bearing on a sentence in another file.

    A turn does not propagate the script's exit code, so the only thing carrying
    a refusal across that gap is SKILL.md telling the agent to paste the output
    verbatim instead of summarising — and README tells the operator to grep that
    output for `refusing to register` / `WARNING` / `PAUSED` on the strength of
    it. Reword SKILL.md (it has been rewritten in three of the last four
    commits) and README keeps promising a guarantee that no longer exists, with
    the operator grepping a summary. This is the newest cross-document
    dependency and was the one restatement nothing covered."""
    skill = (ROOT / "ld-dashboard" / "SKILL.md").read_text()
    assert "paste its output verbatim and report its exit status" in skill, (
        "SKILL.md no longer instructs the agent to paste the script's output -- "
        "README's `refusing to register` / WARNING / PAUSED check greps a "
        "summary without it, and the justfile points at README for the same"
    )

    readme = (ROOT / "README.md").read_text()
    assert "paste the output verbatim + exit code" in readme, (
        "README's bring-up turn no longer asks for the output VERBATIM -- and "
        "'paste the output' alone is satisfied by 'the output showed one job "
        "already present', which is the summary the whole instruction exists to "
        "prevent"
    )


def test_the_cross_document_pointers_still_land_somewhere():
    """Documents were deduplicated into pointers; nothing pinned the targets.

    The justfile keeps no copy of three README sections and names each by title;
    README and SKILL.md both link SKILL.md's Unattended-runs section as a
    rendered anchor. Retitle a heading or repoint a link and they dangle
    silently -- deleting a duplicate is only an improvement while the pointer
    that replaced it resolves.

    LITERALS, deliberately, after five rounds of the alternative. Deriving an
    anchor from a heading means modelling GitHub's slugifier, and every
    refinement of that model found another divergence from it. Exact strings
    make no claim about those rules, so they cannot drift from them: change a
    heading and its assertion fails, change a pointer and its own does, and
    whoever does either updates the literal here -- the test working, not
    friction.

    To add a justfile->README pointer, add a tuple to the loop. Do not rebuild
    the deriver, and do not write a pair out by hand beside it."""
    readme = (ROOT / "README.md").read_text()
    skill = (ROOT / "ld-dashboard" / "SKILL.md").read_text()
    # Comment continuations un-wrapped once, so the literals below pin the
    # POINTER and not the line wrap. Reflowing that block -- what happens the
    # moment anyone edits the surrounding sentence -- would otherwise fail
    # against a justfile whose pointer is intact and correctly worded.
    recipe = (ROOT / "justfile").read_text().replace("\n# ", " ")

    # Every README section the justfile names, having deleted its copies of all
    # of them. An unpinned pointer dangles silently, which is the failure this
    # test is named for.
    for heading, pointer in (
        ("## Bring-up", 'README "Bring-up"'),
        ("## Migrating `rowan`", 'README "Migrating rowan"'),
        ("## No connectors, and what that costs",
         'README "No connectors, and what that costs"'),
    ):
        assert heading in readme.splitlines(), (
            f"README's {heading!r} was retitled or moved, and the justfile points "
            "at it by name -- its reader is sent to a section that no longer exists"
        )
        assert pointer in recipe, (
            f"the justfile no longer carries {pointer!r} -- it keeps no copy of "
            "what that section says, so without the pointer it names nothing"
        )

    assert "## Unattended runs" in skill.splitlines(), (
        "SKILL.md's Unattended-runs heading moved or was retitled; README and "
        "SKILL.md both link #unattended-runs, and both are now dead"
    )
    assert "](ld-dashboard/SKILL.md#unattended-runs)" in readme, (
        "README's link into SKILL.md's Unattended-runs section is gone or "
        "repointed -- it is the only invocation the bring-up reader has for "
        "verifying the crons landed"
    )
    assert "](#unattended-runs)" in skill, (
        "SKILL.md's own link to its Unattended-runs section is gone or repointed"
    )


def test_the_readme_dark_table_lists_the_blocked_producers_and_nothing_else():
    """Membership, not just presence — the stale row is the likelier failure.

    The per-job check above proves every blocked producer has a row. The gap is
    the converse, and it is not hypothetical: latch#183 is what unblocks three of
    these, and the edit that flips `blocked` to None is not the edit that
    remembers to delete a README row. A table that keeps listing a producer as
    dark over-reports what the owner has lost, which is the one thing it exists
    to say."""
    # Delimited by explicit markers, after five rounds of positional anchors --
    # whole file, then the section, then the header row, then the header row
    # asserted unique. Each closed one false-red window and opened a smaller one,
    # because every positional anchor is a guess about where a future editor puts
    # a second table. Markers make no guess: immune to a second table anywhere,
    # to a column rename, to indentation, and to the header being quoted in prose
    # or a fence. Two residuals, accepted rather than traded for a sixth anchor:
    # an invisible pair a README editor can delete (which fails here loudly,
    # naming them), and a future README documenting the marker convention in a
    # fence that quotes BOTH markers above the table -- the first-match span
    # would then be the example, and a correct README would go red. Quoting only
    # the opening marker just widens the span over the intervening prose, which
    # is harmless unless that prose carries a row-shaped example line -- which a
    # fence documenting the convention plausibly would.
    readme = (ROOT / "README.md").read_text()
    # ONE search across both markers. `split(close, 1)[0]` never raises -- index
    # 0 always exists -- so a missing CLOSING marker silently returned the rest
    # of the file and reverted this to the whole-file scan the marker fence
    # replaced, while the guard's message claimed both were checked. Deleting the
    # closing one alone is also the likelier edit of the two.
    fenced = re.search(r"<!-- dark-table -->(.*?)<!-- /dark-table -->", readme, re.S)
    assert fenced, (
        "README's <!-- dark-table --> / <!-- /dark-table --> pair is broken -- "
        "one or both markers are missing. They delimit the dark-producer table "
        "so this test does not have to guess at its position; put them back "
        "around it."
    )
    # [\s>]* because the comment above claims immunity to indentation, and a
    # blockquote prefixes rows with "> " -- `>` is not \s. The
    # marker move dropped only half of what carried it: indent the table into a
    # list item or blockquote and a column-0 anchor matches nothing, under a
    # message about a stale row.
    rows = re.findall(r"^[\s>]*\| `(ld-[\w-]+)` \| \d+ · \w+ \|", fenced[1], re.M)
    found = sorted(rows)
    expected = sorted(j["name"] for j in spec().BLOCKED)
    assert found == expected, (
        f"README's dark-producer table lists {found}, but the blocked "
        f"producers are {expected} -- a row left behind after a blocker cleared "
        "tells the owner they lost something they have back"
    )
