"""tests/test_nudge_candidates.py — behavior tests for the calendar-nudge filter.

Feeds the script latch's all-account `calendar events --all` fan-out answer
as the gather file and asserts on what post_nudge.py will see: the one
handoff (every qualifying reminder, earliest first) and the
{"qualifying": N} count on stdout — the only thing the model routes on.
The module is imported and its path constants rebound to a scratch
directory — a seam reachable only by an importer, never by the CLI the
sheet invokes — because the real handoff lives under /var/lib/hermes.

Field spellings are pinned against a real gather captured through latch
with the sheet's --select: camelCase `CalendarID` / `iCalUID` /
`start.dateTime` / `hangoutLink` / `attendees[].responseStatus`, `visibility`
absent on default-visibility events, free-text fields wrapped in
EXTERNAL_UNTRUSTED_CONTENT markers, and the fan-out's
{items[+account], accounts[{account, status}]} answer. Fixture VALUES are
synthesized; only the key spellings and structural shapes are real.
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ld-calendar-nudge" / "scripts" / "nudge_candidates.py"

spec = importlib.util.spec_from_file_location("nudge_candidates", SCRIPT)
nc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nc)

# A fixed "now" (12:00 PDT) so windows are deterministic; main()'s now= kwarg
# pins the clock (an importer-only seam — the CLI deliberately has no flag).
NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone(timedelta(hours=-7)))

BASE_CONFIG = {
    "family": {"timezone": "America/Los_Angeles"},
    # The kitchen wall's calendars; event() puts every meeting on this one.
    "calendar": {"sources": [{"calendar_id": "owner@example.test"}]},
    "calendar_nudge": {
        "lookahead_virtual_minutes": 30,
        "lookahead_in_person_minutes": 60,
        "owner_identities": ["owner@example.test", "owner.work@example.test"],
    },
}

# The real gog 0.36 shape: the open marker is followed by a `Source:` metadata
# line and a `---` rule. A fixture without them lets "Source: google_api ---"
# through to the kiosk, so both belong in the wrapper under test.
WRAP_OPEN = '<<<EXTERNAL_UNTRUSTED_CONTENT id="abc123">>>\nSource: google_api\n---\n'
WRAP_CLOSE = '\n<<<END_EXTERNAL_UNTRUSTED_CONTENT id="abc123">>>'


@pytest.fixture
def rig(tmp_path, monkeypatch, capsys):
    """Scratch gather + handoff paths, and a runner.

    run() invokes main() the way the cron does (no arguments), with the
    gather where the sheet redirects latch's answer; the consumed-gather pin
    rides every call. Returns (exit_code, parsed_count_or_None, stderr_text)."""
    gather_file = tmp_path / "calendar-nudge-gather"
    handoff = tmp_path / "calendar-nudge.json"
    monkeypatch.setattr(nc, "GATHER_FILE", str(gather_file))
    monkeypatch.setattr(nc, "HANDOFF", str(handoff))
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(nc, "CONFIG_FILE", str(cfg))

    def run(content, config=None, now=NOW):
        cfg.write_text(json.dumps(BASE_CONFIG if config is None else config))
        gather_file.write_text(content)
        code = nc.main([], now=None if now is None else int(now.timestamp()))
        # The raw corpus must not outlive the run, success or failure.
        assert not gather_file.exists()
        out, err = capsys.readouterr()
        return code, (json.loads(out)["qualifying"] if out.strip() else None), err

    return SimpleNamespace(run=run, handoff=handoff, tmp=tmp_path)


def lines(rig):
    return json.loads(rig.handoff.read_text())["chat"]


def card(rig):
    return json.loads(rig.handoff.read_text())["card"]


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
    """One event the way latch answers with the sheet's --select. Defaults are
    a qualifying virtual-less in-person meeting 20 minutes out, owner
    attending, one human counterparty."""
    ev = {
        "CalendarID": "owner@example.test",
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


def gather(*events, failed=()):
    """latch's fan-out answer, as the redirect writes it: every account's
    events, each tagged with the account latch read it through (the owner's
    own unless the event already names one), and an outcome per account."""
    return json.dumps({
        "items": [{"account": "owner@example.test", **ev} for ev in events],
        "accounts": [{"account": "owner@example.test", "status": "ok"},
                     *failed]})


def test_a_qualifying_meeting_writes_the_handoff_and_the_count(rig):
    code, count, _ = rig.run(gather(event(minutes=20, location="Cafe Borrone")))
    assert (code, count) == (0, 1)
    line = 'Heads up: "Standup" at 12:20pm (20m) — Cafe Borrone.'
    assert lines(rig) == [line] and card(rig) == line


def test_every_connected_account_is_the_owner(rig):
    """An invite to any account Latch read through is the owner's meeting,
    config or not, and a meeting between two of them waits on nobody."""
    work = "sam@work.test"
    code, count, _ = rig.run(gather(
        {**event(minutes=20, summary="Board prep", uid="uid-work@google.com",
                 attendees=(attendee(work), attendee("peer@example.test"))),
         "account": work},
        event(minutes=25, summary="Just me", uid="uid-solo@google.com",
              organizer="owner@example.test",
              attendees=(attendee("owner@example.test"), attendee(work)))))
    assert (code, count) == (0, 1)
    assert '"Board prep"' in lines(rig)[0]


def test_only_a_meeting_on_a_wall_calendar_reaches_the_kitchen_screen(rig):
    """The wall shows the household's calendar.sources and the nudge watches
    every calendar, so a reminder from any other calendar goes to chat only.
    One live invite on both is on the wall; a cancelled wall copy is not, and
    two meetings with no iCalUID never borrow each other's calendar."""
    work = {**event(minutes=10, summary="Board prep", uid="uid-w@google.com"),
            "CalendarID": "work@example.test"}
    family = event(minutes=25, summary="Recital", uid="uid-f@google.com")
    assert rig.run(gather(work, family))[:2] == (0, 2)
    assert '"Board prep"' in lines(rig)[0]
    assert card(rig) == lines(rig)[1]
    rig.run(gather(work, {**work, "CalendarID": "owner@example.test"}))
    assert '"Board prep"' in card(rig)
    rig.run(gather(work, {**work, "CalendarID": "owner@example.test",
                          "status": "cancelled"}))
    assert card(rig) is None
    rig.run(gather({**event(uid="", summary="Work"), "CalendarID": "work@example.test"},
                   event(uid="", summary="Home")))
    assert len(lines(rig)) == 2 and '"Home"' in card(rig)


def test_a_watch_list_narrows_the_nudge_without_narrowing_the_privacy_prepass(rig):
    """calendar_nudge.calendars keeps only meetings on the named calendars,
    and an empty or absent list watches every one. An unwatched calendar's
    private copy still withholds its watched sibling: the prepass sees every
    calendar the gather read, whatever the owner chose to be nudged about."""
    def on(calendar_id, **kw):
        return {**event(**kw), "CalendarID": calendar_id}

    evs = (on("work@example.test", summary="Watched", uid="uid-a@google.com"),
           on("other@example.test", summary="Unwatched", uid="uid-b@google.com"),
           on("work@example.test", summary="Withheld", uid="uid-c@google.com"),
           on("other@example.test", summary="Withheld", uid="uid-c@google.com",
              visibility="private"))
    config = json.loads(json.dumps(BASE_CONFIG))
    for calendars, kept in ((["work@example.test"], ['"Watched"']),
                            ([], ['"Watched"', '"Unwatched"'])):
        config["calendar_nudge"]["calendars"] = calendars
        code, count, _ = rig.run(gather(*evs), config=config)
        assert (code, count) == (0, len(kept))
        assert all(k in "\n".join(lines(rig)) for k in kept)
        rig.handoff.unlink()


@pytest.mark.parametrize("content", [
    # latch prints nothing on stdout when the call failed: never "no rows".
    "",
    json.dumps({"accounts": []}),
    json.dumps({"items": None, "accounts": []}),
    json.dumps([]),
    "not json",
    '{"items": [',
    # An unread account's private copies are missing from the prepass, so a
    # healthy account's default-visibility sibling would leak their title.
    gather(event(), failed=[{"account": "old@example.test", "status": "failed",
                             "reason": "google_needs_sign_in"}]),
], ids=["latch-failed", "missing-items", "null-items", "not-an-object",
        "unparseable", "truncated-json", "an-account-unread"])
def test_a_broken_gather_fails_loudly_never_as_a_quiet_run(rig, content):
    # A failed gather read as "no meetings" would silently skip reminders
    # for as long as the failure persists — the exact quiet-day trap.
    code, count, _ = rig.run(content)
    assert (code, count) == (2, None)
    assert not rig.handoff.exists()


def test_a_quiet_window_writes_nothing_and_reports_zero(rig):
    assert rig.run(gather()) == (0, 0, "")
    assert not rig.handoff.exists()


@pytest.mark.parametrize(("ev", "kept"), [
    # The fire window per kind: virtual = hangoutLink or a URL in location.
    (event(minutes=20, hangout="https://meet.example.test/abc"), True),
    (event(minutes=40, hangout="https://meet.example.test/abc"), False),
    (event(minutes=40, location="Zoom: https://zoom.example.test/j/1"), False),
    # Single-slash native URIs are join links too — virtual window applies.
    (event(minutes=20, location="msteams:/l/meetup-join/19%3ameeting"), True),
    (event(minutes=40, location="msteams:/l/meetup-join/19%3ameeting"), False),
    # A bare scheme's residue ("http" + ":" + "//") still carries a slash, so
    # it classifies virtual and falls outside the 30m window.
    (event(minutes=40, location="Join at http://"), False),
    # A prose colon in the location must NOT classify virtual — that would
    # silently shrink the window from 60 to 30 and drop this real in-person
    # reminder. Classification requires a slash; redaction stays broad.
    (event(minutes=40, location="Parking note:Floor 3, Bldg:Annex"), True),
    # The accepted residual, pinned: colon-slash shorthand with no space
    # reads as a link and takes the virtual window (requiring // instead
    # would misclassify real single-slash join URIs — the worse direction).
    (event(minutes=40, location="Note:/parking behind Bldg 2"), False),
    (event(minutes=40), True),                       # in-person window is 60
    (event(minutes=70), False),
    (event(minutes=0), False),                       # already started
    # Sub-minute boundary: a meeting 30 seconds out must not floor to 0 and
    # read as already-started — the next tick would be too late.
    (event(minutes=0.5), True),
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
        "single-slash-uri-in-window", "single-slash-uri-past-window",
        "bare-scheme-classifies-virtual", "prose-colon-stays-in-person",
        "colon-slash-shorthand-residual",
        "in-person-in-window", "in-person-past-window", "already-started",
        "sub-minute-boundary-eligible",
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
        event(minutes=12, uid="uid-3@google.com",
              location="zoommtg://zoom.example.test/join?confno=1&pwd=s3cret"),
    ))
    assert (code, count) == (0, 3)
    out = lines(rig)
    assert len(out) == 3
    for line in out:
        assert line.endswith("— online.")
        assert "http" not in line and "zoommtg" not in line and "pwd" not in line


def test_latch_untrusted_markers_never_reach_the_handoffs(rig):
    """Latch wraps free-text fields (summary, location, description) in
    EXTERNAL_UNTRUSTED_CONTENT markers; the reminder must carry the inner
    text only — marker soup on the kiosk is the failure."""
    rig.run(gather(event(minutes=20,
                         summary=WRAP_OPEN + "Piano recital" + WRAP_CLOSE,
                         location=WRAP_OPEN + "School hall" + WRAP_CLOSE)))
    assert lines(rig) == [
        'Heads up: "Piano recital" at 12:20pm (20m) — School hall.']


def test_a_url_in_the_title_is_stripped_never_posted(rig):
    """A URL in the summary is the same bearer risk the location rule guards
    — a join link pasted into a title must not reach the shared surfaces."""
    rig.run(gather(
        event(minutes=20, summary="Join https://meet.example.test/secret now"),
        event(minutes=25, uid="uid-2@google.com",
              summary="https://only-a-link.example.test/x"),
        event(minutes=22, uid="uid-3@google.com",
              summary="Sync zoommtg://zoom.example.test/join?pwd=s3cret today"),
        event(minutes=23, uid="uid-4@google.com",
              summary="Call msteams:/l/meetup-join/19%3ameeting now"),
        # Reality pin: a letter-led prose token with a colon matches the
        # no-slash-required pattern and is stripped — the accepted over-match
        # direction (a stripped word costs less than a leaked credential).
        # Digit-led times ("3:10pm") never match.
        event(minutes=24, uid="uid-5@google.com",
              summary="Re:Budget review at 3:10pm"),
    ))
    out = lines(rig)
    assert out[0].startswith('Heads up: "Join now" at')
    assert '"Sync today"' in out[1]  # native scheme stripped the same way
    assert '"Call now"' in out[2]    # single-slash form stripped too
    assert '"review at 3:10pm"' in out[3]
    assert '"(untitled meeting)"' in out[4]
    joined = "".join(out)
    assert ("http" not in joined and "zoommtg" not in joined
            and "msteams" not in joined and "pwd" not in joined)


def test_a_private_sibling_drops_every_copy_of_the_invite(rig):
    """One invite, two calendars: the default-visibility copy must not leak
    what the private copy says to keep off the shared display."""
    private = event(minutes=20, visibility="private")
    sibling = event(minutes=20)  # same uid, default visibility
    assert rig.run(gather(private, sibling))[:2] == (0, 0)
    # The same instant written in another calendar's offset is the same copy.
    utc = (NOW + timedelta(minutes=20)).astimezone(timezone.utc).isoformat()
    assert rig.run(gather({**private, "start": {"dateTime": utc}}, sibling))[:2] == (0, 0)
    # A private meeting with no iCalUID has no siblings to take down with it.
    assert rig.run(gather({**private, "iCalUID": ""},
                          event(minutes=20, uid="", summary="Public")))[:2] == (0, 1)


def test_copies_collapse_and_the_earliest_leads_the_handoff(rig):
    copies = [event(minutes=20), event(minutes=20)]         # same (uid, start)
    assert rig.run(gather(*copies))[1] == 1
    # Two DISTINCT meetings that both lack an iCalUID must both survive —
    # a duplicate reminder is cheaper than a silently-dropped meeting — and
    # the EARLIEST leads the handoff (post_nudge gives line 1 to the kiosk).
    rig.handoff.unlink()
    bare = [event(minutes=25, uid="", summary="B"),
            event(minutes=20, uid="", summary="A")]
    assert rig.run(gather(*bare))[1] == 2
    assert len(lines(rig)) == 2
    assert '"A"' in lines(rig)[0] and '"B"' in lines(rig)[1]


@pytest.mark.parametrize("overflow", [
    {"location": "Building 42, " * 12},
    {"summary": "Quarterly planning " * 10},
    {"summary": "Quarterly planning " * 10, "location": "Building 42, " * 12},
], ids=["location-only", "title-only", "both"])
def test_overflow_truncates_variable_fields_never_the_time(rig, overflow):
    """Location truncates first, then the title; the fixed actionable
    `at <time> (<N>m)` portion always survives, and a location-only
    overflow leaves the title untouched."""
    rig.run(gather(event(minutes=20, **overflow)))
    (line,) = lines(rig)
    assert len(line) <= 115
    if "summary" not in overflow:
        # The untouched title stays contiguous with the fixed portion.
        assert 'Heads up: "Standup" at 12:20pm (20m)' in line
    else:
        assert "at 12:20pm (20m)" in line


def test_a_newline_in_untrusted_text_cannot_spoof_a_second_line(rig):
    """The composed reminder is a one-line contract; an event title carrying
    an embedded newline could otherwise fake extra reminder-looking lines on
    the shared kiosk."""
    rig.run(gather(event(
        minutes=20, summary='Standup\nHeads up: "Fake" at 1:00pm (5m)',
        location="Room\r\n1")))
    assert len(lines(rig)) == 1


@pytest.mark.parametrize("mutate", [
    lambda c: c.pop("calendar_nudge"),
    lambda c: c["calendar_nudge"].pop("lookahead_virtual_minutes"),
    lambda c: c.pop("family"),
    lambda c: c.pop("calendar"),
], ids=["no-calendar-nudge", "no-virtual-lookahead", "no-family",
        "no-wall-calendars"])
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


def test_the_cli_takes_no_path_and_reads_where_the_sheet_redirects(tmp_path):
    """The script consumes its input, so a path the model could name would be
    a delete over the config or the dotenv: the CLI refuses any argument with
    the file untouched, and the one file it reads is the one the sheet's
    latch command writes."""
    victim = tmp_path / "victim"
    victim.write_text("must survive")
    with pytest.raises(SystemExit):
        nc.main([str(victim)])
    assert victim.exists(), "an argument must never be opened or deleted"
    sheet = (REPO / "ld-calendar-nudge" / "SKILL.md").read_text()
    assert f"> {nc.GATHER_FILE}\n" in sheet
