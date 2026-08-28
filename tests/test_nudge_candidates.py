"""tests/test_nudge_candidates.py — behavior tests for the calendar-nudge filter.

Feeds the script gog `calendar events list --json --results-only`-shaped
output as a gather file and asserts on what the posting legs will see: the
kiosk handoff (earliest qualifying reminder), the chat handoff (every
reminder), and the {"qualifying": N} count on stdout — the only thing the
model routes on. The module is imported and its path constants rebound to a
scratch directory — a seam reachable only by an importer, never by the CLI
the sheet invokes — because the real handoffs live under /opt/data.

Field spellings are pinned against a REAL gather captured through the live
Latch door (probe on the hermes-life container, 2026-08-28): camelCase
`iCalUID` / `start.dateTime` / `hangoutLink` / `attendees[].responseStatus`,
`visibility` absent on default-visibility events, free-text fields wrapped in
EXTERNAL_UNTRUSTED_CONTENT markers by Latch, and a "Note:" preamble line
ahead of the JSON array. Fixture VALUES are synthesized; only the key
spellings and structural shapes are real.
"""
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ld-calendar-nudge" / "scripts" / "nudge_candidates.py"

spec = importlib.util.spec_from_file_location("nudge_candidates", SCRIPT)
nc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nc)

# A fixed "now" (12:00 PDT) so windows are deterministic; --now pins the clock.
NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone(timedelta(hours=-7)))

BASE_CONFIG = {
    "family": {"timezone": "America/Los_Angeles"},
    "calendar_nudge": {
        "lookahead_virtual_minutes": 30,
        "lookahead_in_person_minutes": 60,
        "owner_identities": ["owner@example.test", "owner.work@example.test"],
    },
}

WRAP_OPEN = '<<<EXTERNAL_UNTRUSTED_CONTENT id="abc123">>>'
WRAP_CLOSE = '<<<END_EXTERNAL_UNTRUSTED_CONTENT id="abc123">>>'


@pytest.fixture
def rig(tmp_path, monkeypatch, capsys):
    """Scratch persisted-result root + handoff paths, and a runner.

    run() invokes main() the way the cron does (argv only), with the gather
    under the allowed persisted root; the consumed-gather pin rides every
    call. Returns (exit_code, parsed_count_or_None, stderr_text)."""
    results = tmp_path / "results"
    results.mkdir()
    kiosk = tmp_path / "kiosk-text"
    chat = tmp_path / "chat"
    monkeypatch.setattr(nc, "PERSISTED_ROOT", str(results) + "/")
    monkeypatch.setattr(nc, "KIOSK_FILE", str(kiosk))
    monkeypatch.setattr(nc, "CHAT_FILE", str(chat))

    def run(content, config=None, now=NOW):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps(BASE_CONFIG if config is None else config))
        gather_file = results / "call_test.txt"
        gather_file.write_text(content)
        argv = ["--config", str(cfg)]
        if now is not None:
            argv += ["--now", str(int(now.timestamp()))]
        code = nc.main(argv + [str(gather_file)])
        # The raw corpus must not outlive the run, success or failure.
        assert not gather_file.exists()
        out, err = capsys.readouterr()
        return code, (json.loads(out)["qualifying"] if out.strip() else None), err

    return SimpleNamespace(run=run, kiosk=kiosk, chat=chat,
                           results=results, tmp=tmp_path)


def chat_lines(rig):
    return rig.chat.read_text().splitlines()


def at(minutes, date_only=False):
    """A start/end the way gog frames them (camelCase, offset ISO)."""
    t = NOW + timedelta(minutes=minutes)
    if date_only:
        return {"date": t.date().isoformat()}
    return {"dateTime": t.isoformat(), "timeZone": "America/Los_Angeles"}


def attendee(email, response="accepted"):
    return {"email": email, "responseStatus": response, "self": False}


def event(minutes=20, *, summary="Standup", uid="uid-1@google.com",
          status="confirmed", organizer="peer@example.test",
          attendees=(attendee("owner@example.test"),
                     attendee("peer@example.test")),
          hangout=None, location=None, visibility=None, date_only=False):
    """One event the way gog's --json --results-only frames it. Defaults are
    a qualifying virtual-less in-person meeting 20 minutes out, owner
    attending, one human counterparty."""
    ev = {
        "CalendarID": "primary",
        "iCalUID": uid,
        "id": "evt-" + uid,
        "kind": "calendar#event",
        "status": status,
        "summary": summary,
        "start": at(minutes, date_only),
        "end": at(minutes + 30, date_only),
        "organizer": {"email": organizer, "self": False},
        "attendees": list(attendees),
        "externalContent": {"source": "google_api", "untrusted": True,
                            "wrapped": True},
    }
    if hangout is not None:
        ev["hangoutLink"] = hangout
    if location is not None:
        ev["location"] = location
    if visibility is not None:
        ev["visibility"] = visibility
    return ev


def gather(*events):
    """gog's stdout as Latch relays it: a Note preamble, then the array."""
    return ("Note: Using direct access token (expires in ~1 hour)\n"
            + json.dumps(list(events)))


def envelope(exit_code, output):
    """The persisted plow_run_command result: gog's stdout nested as a JSON
    string inside a JSON string."""
    return json.dumps(
        {"result": json.dumps({"exit_code": exit_code, "handle": "h",
                               "status": "completed", "output": output})})


def test_a_qualifying_meeting_writes_both_handoffs_and_the_count(rig):
    code, count, _ = rig.run(gather(event(minutes=20, location="Cafe Borrone")))
    assert (code, count) == (0, 1)
    line = 'Heads up: "Standup" at 12:20pm (20m) — Cafe Borrone.'
    assert rig.kiosk.read_text() == line + "\n"
    assert rig.chat.read_text() == line + "\n"


def test_persisted_envelope_unwraps_to_the_same_handoffs(rig):
    raw = gather(event(minutes=20, location="Cafe Borrone"))
    rig.run(raw)
    from_raw = rig.kiosk.read_text()
    rig.kiosk.unlink(); rig.chat.unlink()
    code, count, _ = rig.run(envelope(0, raw))
    assert (code, count) == (0, 1)
    assert rig.kiosk.read_text() == from_raw


@pytest.mark.parametrize("content", [
    envelope(2, ""),  # gog exit 2: one bad calendar name fails the whole gather
    json.dumps({"result": json.dumps({"exit_code": 0, "handle": "h"})}),
    json.dumps({"result": "not json"}),
    envelope(0, None),
    "Note: preamble only, no array",
    'Note: x\n[{"bad": }]',
], ids=["gather-failed", "missing-output", "unparseable-result",
        "null-output", "no-array", "truncated-json"])
def test_a_broken_gather_fails_loudly_never_as_a_quiet_run(rig, content):
    # A failed gather read as "no meetings" would silently skip reminders
    # for as long as the failure persists — the exact quiet-day trap.
    code, count, _ = rig.run(content)
    assert (code, count) == (2, None)
    assert not rig.kiosk.exists() and not rig.chat.exists()


def test_a_quiet_window_writes_nothing_and_reports_zero(rig):
    assert rig.run(gather()) == (0, 0, "")
    assert not rig.kiosk.exists() and not rig.chat.exists()


@pytest.mark.parametrize(("ev", "kept"), [
    # The fire window per kind: virtual = hangoutLink or a URL in location.
    (event(minutes=20, hangout="https://meet.example.test/abc"), True),
    (event(minutes=40, hangout="https://meet.example.test/abc"), False),
    (event(minutes=40, location="Zoom: https://zoom.example.test/j/1"), False),
    (event(minutes=40), True),                       # in-person window is 60
    (event(minutes=70), False),
    (event(minutes=0), False),                       # already started
    # Structural drops.
    (event(status="cancelled"), False),
    (event(date_only=True), False),                  # all-day: start.date only
    (event(visibility="private"), False),
    (event(visibility="confidential"), False),
    # Owner participation: organizer counts, a declined identity does not,
    # absence drops.
    (event(organizer="owner@example.test",
           attendees=(attendee("peer@example.test"),)), True),
    (event(attendees=(attendee("owner@example.test", "declined"),
                      attendee("peer@example.test"))), False),
    (event(attendees=(attendee("third@example.test"),
                      attendee("peer@example.test"))), False),
    # A second owner identity qualifies too.
    (event(attendees=(attendee("owner.work@example.test"),
                      attendee("peer@example.test"))), True),
    # Human counterparty: a declined peer, a resource, or a mirrored shared
    # calendar is no one left waiting.
    (event(attendees=(attendee("owner@example.test"),
                      attendee("peer@example.test", "declined"))), False),
    (event(organizer="owner@example.test",
           attendees=(attendee("owner@example.test"),
                      attendee("room-3@resource.calendar.google.com"))), False),
    (event(organizer="owner@example.test",
           attendees=(attendee("owner@example.test"),
                      attendee("family@group.calendar.google.com"))), False),
    # Google's 1:1 shape: the human organizer not echoed into attendees.
    (event(organizer="peer@example.test",
           attendees=(attendee("owner@example.test"),)), True),
], ids=["virtual-in-window", "virtual-past-window", "location-url-is-virtual",
        "in-person-in-window", "in-person-past-window", "already-started",
        "cancelled", "all-day", "private", "confidential",
        "owner-as-organizer", "owner-declined", "owner-absent",
        "second-identity", "peer-declined", "resource-only", "group-only",
        "organizer-not-in-attendees"])
def test_the_filter_keeps_exactly_the_qualifying_meetings(rig, ev, kept):
    code, count, _ = rig.run(gather(ev))
    assert code == 0
    assert (count > 0) is kept


def test_a_virtual_meeting_renders_online_never_the_join_url(rig):
    """The hangout link and a location URL are bearer-style join tokens; the
    kiosk is a shared display."""
    code, count, _ = rig.run(gather(
        event(minutes=10, hangout="https://meet.example.test/secret-token"),
        event(minutes=15, uid="uid-2@google.com",
              location="Zoom: https://zoom.example.test/j/22"),
    ))
    assert (code, count) == (0, 2)
    lines = chat_lines(rig)
    assert len(lines) == 2
    for line in lines:
        assert line.endswith("— online.")
        assert "http" not in line


def test_latch_untrusted_markers_never_reach_the_handoffs(rig):
    """Latch wraps free-text fields (summary, location, description) in
    EXTERNAL_UNTRUSTED_CONTENT markers; the reminder must carry the inner
    text only — marker soup on the kiosk is the failure."""
    rig.run(gather(event(minutes=20,
                         summary=WRAP_OPEN + "Piano recital" + WRAP_CLOSE,
                         location=WRAP_OPEN + "School hall" + WRAP_CLOSE)))
    assert rig.kiosk.read_text() == (
        'Heads up: "Piano recital" at 12:20pm (20m) — School hall.\n')


def test_a_url_in_the_title_is_stripped_never_posted(rig):
    """A URL in the summary is the same bearer risk the location rule guards
    — a join link pasted into a title must not reach the shared surfaces."""
    rig.run(gather(
        event(minutes=20, summary="Join https://meet.example.test/secret now"),
        event(minutes=25, uid="uid-2@google.com",
              summary="https://only-a-link.example.test/x"),
    ))
    lines = chat_lines(rig)
    assert lines[0].startswith('Heads up: "Join now" at')
    assert '"(untitled meeting)"' in lines[1]
    assert "http" not in "".join(lines)


def test_a_private_sibling_drops_every_copy_of_the_invite(rig):
    """One invite, two calendars: the default-visibility copy must not leak
    what the private copy says to keep off the shared display."""
    private = event(minutes=20, visibility="private")
    sibling = event(minutes=20)  # same uid, default visibility
    assert rig.run(gather(private, sibling))[:2] == (0, 0)


def test_copies_collapse_and_the_kiosk_gets_only_the_earliest(rig):
    copies = [event(minutes=20), event(minutes=20)]         # same (uid, start)
    assert rig.run(gather(*copies))[1] == 1
    # Two DISTINCT meetings that both lack an iCalUID must both survive —
    # a duplicate reminder is cheaper than a silently-dropped meeting — and
    # the one-line kiosk card carries the EARLIEST while chat carries both.
    rig.kiosk.unlink(); rig.chat.unlink()
    bare = [event(minutes=25, uid="", summary="B"),
            event(minutes=20, uid="", summary="A")]
    assert rig.run(gather(*bare))[1] == 2
    assert len(chat_lines(rig)) == 2
    assert '"A"' in rig.kiosk.read_text() and '"B"' not in rig.kiosk.read_text()


def test_a_failed_swap_never_leaves_a_half_written_handoff(rig, monkeypatch):
    """What staging actually guarantees, both halves. A failure BEFORE any
    rename (both bodies still in .tmp) leaves both handoffs untouched —
    direct writes would fail this. A failure BETWEEN the renames leaves each
    handoff either fully old or fully new, never truncated: the fresh-kiosk/
    stale-chat pairing seen here is the documented, accepted residual window
    the production comment names — one tick wide, self-healing."""
    fresh = 'Heads up: "Standup" at 12:20pm (20m).\n'
    real = os.replace  # captured before any patch, or phase 2 replays phase 1's raiser

    def reset():
        rig.kiosk.write_text("old kiosk\n")
        rig.chat.write_text("old chat\n")

    calls = []

    def fails_on(n):
        def fake_replace(src, dst):
            calls.append(src)
            if len(calls) == n:
                raise OSError("full")
            return real(src, dst)
        return fake_replace

    reset()
    monkeypatch.setattr(nc.os, "replace", fails_on(1))
    with pytest.raises(OSError):
        rig.run(gather(event()))
    assert rig.kiosk.read_text() == "old kiosk\n"
    assert rig.chat.read_text() == "old chat\n"

    reset()
    calls.clear()

    monkeypatch.setattr(nc.os, "replace", fails_on(2))
    with pytest.raises(OSError):
        rig.run(gather(event()))
    assert rig.kiosk.read_text() == fresh, "the swapped leg is fully new"
    assert rig.chat.read_text() == "old chat\n", "the unswapped leg is fully old"


def test_overflow_truncates_location_then_title_never_the_time(rig):
    rig.run(gather(event(minutes=20, location="Building 42, " * 12)))
    (line,) = rig.kiosk.read_text().splitlines()
    assert len(line) <= 115
    assert 'Heads up: "Standup" at 12:20pm (20m)' in line, (
        "the fixed actionable portion must survive truncation untouched"
    )
    rig.run(gather(event(minutes=20, summary="Quarterly planning " * 10)))
    (line,) = rig.kiosk.read_text().splitlines()
    assert len(line) <= 115
    assert "at 12:20pm (20m)" in line


def test_overflow_on_both_fields_truncates_both_and_keeps_the_time(rig):
    rig.run(gather(event(minutes=20, summary="Quarterly planning " * 10,
                         location="Building 42, " * 12)))
    (line,) = rig.kiosk.read_text().splitlines()
    assert len(line) <= 115
    assert "at 12:20pm (20m)" in line


def test_a_newline_in_untrusted_text_cannot_spoof_a_second_line(rig):
    """The composed reminder is a one-line contract; an event title carrying
    an embedded newline could otherwise fake extra reminder-looking lines on
    the shared kiosk."""
    rig.run(gather(event(
        minutes=20, summary='Standup\nHeads up: "Fake" at 1:00pm (5m)',
        location="Room\r\n1")))
    assert len(rig.kiosk.read_text().splitlines()) == 1


@pytest.mark.parametrize("mutate", [
    lambda c: c.pop("calendar_nudge"),
    lambda c: c["calendar_nudge"].pop("lookahead_virtual_minutes"),
    lambda c: c.pop("family"),
], ids=["no-calendar-nudge", "no-virtual-lookahead", "no-family"])
def test_a_broken_config_fails_loudly_with_the_documented_exit(rig, mutate):
    config = json.loads(json.dumps(BASE_CONFIG))
    mutate(config)
    code, count, err = rig.run(gather(event()), config=config)
    assert (code, count) == (2, None)
    assert "bad config" in err


def test_an_empty_owner_identity_set_refuses_rather_than_never_nudging(rig):
    """owner_identities=[] would make every event fail owner-participation —
    a config mistake that presents as an eternally quiet nudge."""
    config = json.loads(json.dumps(BASE_CONFIG))
    config["calendar_nudge"]["owner_identities"] = []
    code, count, err = rig.run(gather(event()), config=config)
    assert (code, count) == (2, None)
    assert "owner_identities" in err


@pytest.mark.parametrize("path", [
    "/opt/data/ld/config.json",
    "/opt/data/.env",
    "/tmp/hermes-results",                      # the bare directory
    "/tmp/hermes-results-evil/call_x.txt",      # prefix trick
    "/tmp/hermes-results/../../opt/data/.env",  # traversal
    "relative/call_x.txt",
], ids=["config", "dotenv", "bare-dir", "prefix-trick", "traversal",
        "relative"])
def test_a_disallowed_gather_path_is_refused_before_any_io(path, tmp_path, capsys):
    """The script consumes its input, so the model-supplied path is a
    deletion oracle unless it is pinned to the two allowed locations — an
    injected turn passing the config or dotenv must be refused with the
    file untouched. Runs against the REAL default constants."""
    victim = tmp_path / "victim"
    victim.write_text("must survive")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(BASE_CONFIG))
    assert not nc.gather_path_allowed(path)
    code = nc.main(["--config", str(cfg), str(victim)])
    assert code == 2
    assert victim.exists(), "a refused path must never be opened or deleted"
    assert "refusing gather path" in capsys.readouterr().err


def test_the_allowed_gather_locations_are_the_documented_ones():
    """Accept side of the pin, against the real constants: a persisted
    result and the fixed inline handoff — nothing else."""
    assert nc.PERSISTED_ROOT == "/tmp/hermes-results/"
    assert nc.GATHER_FILE == "/opt/data/ld/calendar-nudge-gather"
    assert nc.gather_path_allowed("/tmp/hermes-results/call_abc.txt")
    assert nc.gather_path_allowed("/opt/data/ld/calendar-nudge-gather")
