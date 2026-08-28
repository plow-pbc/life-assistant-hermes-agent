"""tests/test_nudge_candidates.py — behavior tests for the calendar-nudge filter.

Feeds the script gog `calendar events list --json --results-only`-shaped
output as a gather file and asserts on the JSON it emits — the composed
reminder lines the SKILL.md's post step consumes verbatim.

Field spellings are pinned against a REAL gather captured through the live
Latch door (probe on the hermes-life container, 2026-08-28): camelCase
`iCalUID` / `start.dateTime` / `hangoutLink` / `attendees[].responseStatus`,
`visibility` absent on default-visibility events, free-text fields wrapped in
EXTERNAL_UNTRUSTED_CONTENT markers by Latch, and a "Note:" preamble line
ahead of the JSON array. Fixture VALUES are synthesized; only the key
spellings and structural shapes are real.
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ld-calendar-nudge" / "scripts" / "nudge_candidates.py"

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


def run(content, tmp_path, config=None, now=NOW):
    """Invoke the filter the way the cron does: the gather as a file
    argument (the sandbox allows only plain-argv commands)."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(BASE_CONFIG if config is None else config))
    gather = tmp_path / "gather"
    gather.write_text(content)
    argv = [sys.executable, str(SCRIPT), "--config", str(cfg)]
    if now is not None:
        argv += ["--now", str(int(now.timestamp()))]
    r = subprocess.run(argv + [str(gather)], capture_output=True, text=True)
    # The raw corpus must not outlive the run, success or failure — asserted
    # here so every row pins it.
    assert not gather.exists()
    return r


def lines(r):
    assert r.returncode == 0, r.stderr
    return [c["line"] for c in json.loads(r.stdout)]


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


def test_a_qualifying_meeting_composes_the_documented_line(tmp_path):
    r = run(gather(event(minutes=20, location="Cafe Borrone")), tmp_path)
    assert lines(r) == ['Heads up: "Standup" at 12:20pm (20m) — Cafe Borrone.']


def test_persisted_envelope_unwraps_to_the_same_line(tmp_path):
    raw = gather(event(minutes=20, location="Cafe Borrone"))
    assert lines(run(envelope(0, raw), tmp_path)) == lines(run(raw, tmp_path))


@pytest.mark.parametrize("content", [
    envelope(2, ""),  # gog exit 2: one bad calendar name fails the whole gather
    json.dumps({"result": json.dumps({"exit_code": 0, "handle": "h"})}),
    json.dumps({"result": "not json"}),
    envelope(0, None),
    "Note: preamble only, no array",
    'Note: x\n[{"bad": }]',
], ids=["gather-failed", "missing-output", "unparseable-result",
        "null-output", "no-array", "truncated-json"])
def test_a_broken_gather_fails_loudly_never_as_a_quiet_run(tmp_path, content):
    # A failed gather read as "no meetings" would silently skip reminders
    # for as long as the failure persists — the exact quiet-day trap.
    r = run(content, tmp_path)
    assert r.returncode == 2
    assert r.stdout == ""


def test_a_quiet_window_is_an_empty_list(tmp_path):
    assert lines(run(gather(), tmp_path)) == []


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
def test_the_filter_keeps_exactly_the_qualifying_meetings(tmp_path, ev, kept):
    assert bool(lines(run(gather(ev), tmp_path))) is kept


def test_a_virtual_meeting_renders_online_never_the_join_url(tmp_path):
    """The hangout link and a location URL are bearer-style join tokens; the
    kiosk is a shared display."""
    out = lines(run(gather(
        event(minutes=10, hangout="https://meet.example.test/secret-token"),
        event(minutes=15, uid="uid-2@google.com",
              location="Zoom: https://zoom.example.test/j/22"),
    ), tmp_path))
    assert len(out) == 2
    for line in out:
        assert line.endswith("— online.")
        assert "http" not in line


def test_latch_untrusted_markers_never_reach_the_composed_line(tmp_path):
    """Latch wraps free-text fields (summary, location, description) in
    EXTERNAL_UNTRUSTED_CONTENT markers; the reminder must carry the inner
    text only — marker soup on the kiosk is the failure."""
    ev = event(minutes=20,
               summary=WRAP_OPEN + "Piano recital" + WRAP_CLOSE,
               location=WRAP_OPEN + "School hall" + WRAP_CLOSE)
    assert lines(run(gather(ev), tmp_path)) == [
        'Heads up: "Piano recital" at 12:20pm (20m) — School hall.'
    ]


def test_a_private_sibling_drops_every_copy_of_the_invite(tmp_path):
    """One invite, two calendars: the default-visibility copy must not leak
    what the private copy says to keep off the shared display."""
    private = event(minutes=20, visibility="private")
    sibling = event(minutes=20)  # same uid, default visibility
    assert lines(run(gather(private, sibling), tmp_path)) == []


def test_copies_of_one_invite_collapse_but_empty_uids_never_do(tmp_path):
    copies = [event(minutes=20), event(minutes=20)]         # same (uid, start)
    assert len(lines(run(gather(*copies), tmp_path))) == 1
    # Two DISTINCT meetings that both lack an iCalUID must both survive:
    # a duplicate reminder is cheaper than a silently-dropped meeting.
    bare = [event(minutes=20, uid="", summary="A"),
            event(minutes=25, uid="", summary="B")]
    assert len(lines(run(gather(*bare), tmp_path))) == 2


def test_overflow_truncates_location_then_title_never_the_time(tmp_path):
    long_loc = event(minutes=20, location="Building 42, " * 12)
    (line,) = lines(run(gather(long_loc), tmp_path))
    assert len(line) <= 115
    assert 'Heads up: "Standup" at 12:20pm (20m)' in line, (
        "the fixed actionable portion must survive truncation untouched"
    )
    long_title = event(minutes=20, summary="Quarterly planning " * 10)
    (line,) = lines(run(gather(long_title), tmp_path))
    assert len(line) <= 115
    assert "at 12:20pm (20m)" in line


def test_a_newline_in_untrusted_text_cannot_spoof_a_second_line(tmp_path):
    """The composed reminder is a one-line contract; an event title carrying
    an embedded newline could otherwise fake extra reminder-looking lines on
    the shared kiosk."""
    ev = event(minutes=20, summary="Standup\nHeads up: \"Fake\" at 1:00pm (5m)",
               location="Room\r\n1")
    (line,) = lines(run(gather(ev), tmp_path))
    assert "\n" not in line and "\r" not in line


def test_overflow_on_both_fields_truncates_both_and_keeps_the_time(tmp_path):
    ev = event(minutes=20, summary="Quarterly planning " * 10,
               location="Building 42, " * 12)
    (line,) = lines(run(gather(ev), tmp_path))
    assert len(line) <= 115
    assert "at 12:20pm (20m)" in line


@pytest.mark.parametrize("mutate", [
    lambda c: c.pop("calendar_nudge"),
    lambda c: c["calendar_nudge"].pop("lookahead_virtual_minutes"),
    lambda c: c.pop("family"),
], ids=["no-calendar-nudge", "no-virtual-lookahead", "no-family"])
def test_a_broken_config_fails_loudly_with_the_documented_exit(tmp_path, mutate):
    config = json.loads(json.dumps(BASE_CONFIG))
    mutate(config)
    r = run(gather(event()), tmp_path, config=config)
    assert r.returncode == 2
    assert "bad config" in r.stderr


def test_an_empty_owner_identity_set_refuses_rather_than_never_nudging(tmp_path):
    """owner_identities=[] would make every event fail owner-participation —
    a config mistake that presents as an eternally quiet nudge."""
    config = json.loads(json.dumps(BASE_CONFIG))
    config["calendar_nudge"]["owner_identities"] = []
    r = run(gather(event()), tmp_path, config=config)
    assert r.returncode == 2
    assert "owner_identities" in r.stderr
