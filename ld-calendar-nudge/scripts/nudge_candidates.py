#!/usr/bin/env python3
"""nudge_candidates.py — filter + compose the calendar gather for ld-calendar-nudge.

Reads the fixed all-account plow-gog `calendar events list --all` fan-out from
its gather-file argument, deleting the file as it goes, applies the nudge
rules — privacy prepass, per-event filter, dedupe — and writes every
composed ≤115-char reminder, earliest first, STRAIGHT to the one fixed
handoff post_nudge.py consumes -- every reminder for chat, and the earliest
one on a kitchen-wall calendar (`calendar.sources`) as the kiosk card. stdout carries only {"qualifying": N} — the
model routes on the count and never touches reminder content, so the helper
chain takes zero model-controlled content end to end (the plow#625 shape).
Deterministic on purpose: the rules were 200 lines of sheet prose upstream;
now they are code with a test per rule.

The gather path is the one model-supplied argument, so it is validated
BEFORE any open/unlink: only a runtime-persisted result (under
/tmp/hermes-results/) or the fixed inline handoff
(/var/lib/hermes/ld/calendar-nudge-gather) is accepted — anything else exits 2
without touching the file, so an injected turn cannot aim the
consume-on-read unlink at the config or the dotenv.

Field spellings are pinned against a REAL gather through the live Latch door
(camelCase: iCalUID, start.dateTime, hangoutLink, attendees[].responseStatus;
`visibility` absent means default). Latch's injected safety flags wrap every
free-text field (summary, location, description, ...) in
EXTERNAL_UNTRUSTED_CONTENT markers — stripped here so marker soup never
reaches the kiosk.

Exit 2 on malformed input rather than skipping rows: a half-parsed window is
indistinguishable from a quiet one on both surfaces, so it must fail loudly.
That includes a call that never completed (an unanswered approval card) and
any account Latch could not read, and a failed gather must never read as a
no-nudge run.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)
from calendar_feed import event_key, visible_events  # noqa: E402
from external_content import strip_markers  # noqa: E402
from gather_result import GatherError, read_fanout  # noqa: E402

LIMIT = 115
# The only gather locations the model may name (validated before any I/O):
# the runtime's persisted-result directory, and the fixed file the sheet
# writes an inline result to. Trailing slash on the root is load-bearing —
# it is a prefix check, and "/tmp/hermes-results-evil" must not pass.
PERSISTED_ROOT = "/tmp/hermes-results/"
GATHER_FILE = "/var/lib/hermes/ld/calendar-nudge-gather"
# The one fixed handoff this helper writes and post_nudge.py consumes:
# {"chat": [every reminder], "card": the kitchen wall's reminder or null}.
HANDOFF = "/var/lib/hermes/ld/calendar-nudge.json"
# The shared ld-config, fixed here rather than taken as a flag: the model
# builds the argv, and a steerable --config could point at a model-written
# JSON whose identities/lookaheads make a non-qualifying event publish.
CONFIG_FILE = "/var/lib/hermes/ld/config.json"
# Two patterns for two opposite risk profiles.
#
# REDACTION (title stripping) uses the broad one: any URI, not just http(s)
# — native join links carry the same bearer-style credentials, and no slash
# is required after the colon. Letter-led prose tokens with a colon
# ("note:parking", "Re:Budget") DO match and get stripped — over-stripping
# fails SAFE on a shared display (a stripped word costs less than a leaked
# credential); digit-led times ("3:10pm") stay untouched.
_URL_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:\S+")
# CLASSIFICATION (virtual vs in-person) must not over-match: flipping a
# prose-colon location ("Floor:3") to virtual would silently shrink its
# window from 60 to 30 minutes and drop real reminders. It requires a slash
# directly after the colon, which every real join link has — double
# (zoommtg://...) or single (msteams:/l/meetup-join/...) — and ordinary
# prose colons do not. Residual, accepted and pinned: a colon-slash
# shorthand with no space ("Note:/parking") still reads as a link; requiring
# // would misclassify real single-slash joins, the worse direction.
_LINK_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:/\S+")
# Destinations, not people: Google's shared-calendar and booking-resource
# suffixes. A mirrored invite or a room is nobody left waiting.
_NON_HUMAN_SUFFIXES = ("@group.calendar.google.com",
                       "@resource.calendar.google.com")


def unwrap(value):
    """Strip Latch's untrusted-content markers and collapse all whitespace —
    newlines included — to single spaces. The composed reminder is a ONE-line
    contract: an event title carrying an embedded newline could otherwise
    spoof extra reminder-looking lines on the shared kiosk."""
    return " ".join(strip_markers(value).split())


def is_human_external(email, identities):
    email = (email or "").lower()
    return bool(email) and email not in identities and not email.endswith(
        _NON_HUMAN_SUFFIXES)


def compose(summary, local_time, minutes_until, where):
    """The documented line, truncating variable fields first — location, then
    title — never the fixed `at <time> (<N>m)` portion (the actionable part)."""
    def render(s, w):
        core = f'Heads up: "{s}" at {local_time} ({minutes_until}m)'
        return core + (f" — {w}." if w else ".")

    line = render(summary, where)
    if len(line) <= LIMIT:
        return line
    if where:
        keep = len(where) - (len(line) - LIMIT) - 1
        where = (where[:keep] + "…") if keep > 0 else "…"
        line = render(summary, where)
        if len(line) <= LIMIT:
            return line
    keep = len(summary) - (len(line) - LIMIT) - 1
    return render((summary[:keep] + "…") if keep > 0 else "…", where)


def gather_path_allowed(path):
    """Whether a model-supplied gather path may be opened (and consumed).

    Checked BEFORE any open/unlink: this script deletes its input as it
    reads, so an unvalidated path hands an injected turn a deletion oracle
    over anything the container user can write — the config and the dotenv
    being the obvious targets."""
    normalized = os.path.normpath(path)
    # normpath collapses ../ traversal, and the trailing slash on
    # PERSISTED_ROOT makes this a directory-prefix check, so neither
    # "/tmp/hermes-results-evil" nor the bare directory itself passes.
    return normalized == GATHER_FILE or normalized.startswith(PERSISTED_ROOT)


def main(argv=None, now=None) -> int:
    """CLI surface: the gather path, nothing else. `now` (unix seconds) is
    reachable only by an importer — as a flag it was a model-steerable clock
    an injected invite could use to publish an out-of-window title, and
    --config the same class of knob (a steered path to a model-written JSON
    could widen the windows or the identity set). Both are pinned."""
    parser = argparse.ArgumentParser()
    parser.add_argument("gather",
                        help="gather file (the plow_run_command fan-out "
                             "result, persisted or written inline)")
    args = parser.parse_args(argv)

    if not gather_path_allowed(args.gather):
        print(f"refusing gather path {args.gather!r}: only a persisted "
              f"result under {PERSISTED_ROOT} or the fixed {GATHER_FILE} is "
              "accepted -- this script consumes its input, and an arbitrary "
              "path would be an arbitrary delete", file=sys.stderr)
        return 2

    # Consume-first + the fan-out payload live in ld-shared/scripts/gather_result.py.
    try:
        events, degraded = read_fanout(args.gather)
    except GatherError as e:
        print(e, file=sys.stderr)
        return 2

    # Same exit-2 contract as the gather below: a broken config must fail
    # loudly, never surface as a traceback-with-exit-1 or a quiet run.
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        tz = ZoneInfo(config["family"]["timezone"])
        nudge_cfg = config["calendar_nudge"]
        lookahead_virtual = nudge_cfg["lookahead_virtual_minutes"]
        lookahead_in_person = nudge_cfg["lookahead_in_person_minutes"]
        identities = {str(e).strip().lower()
                      for e in nudge_cfg["owner_identities"] if str(e).strip()}
        # The owner's watch list, by calendar id: empty or absent is every
        # connected calendar (its shape is ld_config_gate.py's check 9).
        # Applied here rather than in the gather, so the approved argv never
        # changes when the owner changes their mind.
        watched = set(nudge_cfg.get("calendars", []))
        # The kitchen wall's calendars are the household's, not the nudge's:
        # the nudge watches everything the owner attends, and only a meeting
        # on one of these may put its title on the shared screen.
        wall = {s["calendar_id"] for s in config["calendar"]["sources"]}
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"bad config {CONFIG_FILE}: {e!r}", file=sys.stderr)
        return 2
    if not identities:
        # An empty identity set fails owner-participation on EVERY event — a
        # config mistake that presents as an eternally quiet nudge.
        print("calendar_nudge.owner_identities is empty -- every event would "
              "fail the owner-participation rule and no nudge would ever "
              "fire", file=sys.stderr)
        return 2

    now = int(time.time()) if now is None else now
    now_dt = datetime.fromtimestamp(now, tz=timezone.utc)

    try:
        # Any account Latch could not read fails the run: its private copies
        # are missing from the prepass below, so a default-visibility sibling
        # read through a healthy account would post the title they withhold.
        # Account and reason are Latch's text, never the calendar's.
        if degraded:
            names = ", ".join(f"{d['account']} ({d['reason']})" for d in degraded)
            print(f"not every account answered the gather: {names}",
                  file=sys.stderr)
            return 2

        # Every account Latch read through is one the owner connected, so it
        # is theirs: an invite to any of them is the owner's meeting, and one
        # between two of them waits on nobody. The Mac writes the tag after
        # the fetch, so event text cannot forge one; the config still carries
        # the addresses no account is connected as.
        identities |= {ev["account"].strip().lower() for ev in events}

        # The calendar strip's privacy prepass, shared: cancelled copies,
        # private ones, and every sibling of a private copy are gone before
        # anything below reads a title -- or a default-visibility sibling
        # would post what the private copy withholds.
        events = visible_events(events)
        # A live invite on a wall calendar and a work one is on the wall; a
        # meeting with no iCalUID has no siblings, so only its own copy counts.
        wall_keys = {event_key(ev) for ev in events
                     if ev["CalendarID"] in wall and ev.get("iCalUID")}

        survivors = []
        for ev in events:
            key = event_key(ev)
            # After the prepass, never before it: a private copy on a calendar
            # the owner does not watch still withholds its watched sibling.
            # `CalendarID` is gog's own tag on an --all read (eventWithCalendar).
            if watched and ev["CalendarID"] not in watched:
                continue
            # All-day events have start.date only; a date parsed as midnight
            # would fire a misleading late-night reminder. They belong to the
            # morning-updates/weekly-digest surfaces.
            start_iso = ev["start"].get("dateTime")
            if not start_iso:
                continue
            start_dt = datetime.fromisoformat(start_iso)
            # ceil, not floor: a meeting 30 seconds out floors to 0 and gets
            # rejected as already-started — and the next tick is too late.
            # Ceiling reads it as "1 minute until", eligible and truthful.
            minutes_until = math.ceil((start_dt - now_dt).total_seconds() / 60)

            location = unwrap(ev.get("location"))
            # Virtual = a structured video link OR a link in the location.
            # Only a slashed link counts (_LINK_TOKEN, not the broad redaction
            # pattern): keyword-matching false-positives on "Meeting Room",
            # and a prose colon ("Floor:3") must not shrink the window. The
            # raw link is a bearer-style join token and never reaches a
            # surface; compose renders `online`.
            virtual = bool(ev.get("hangoutLink")) or bool(_LINK_TOKEN.search(location))
            lookahead = lookahead_virtual if virtual else lookahead_in_person
            if not (0 < minutes_until <= lookahead):
                continue

            attendees = ev.get("attendees", [])
            organizer_email = (ev.get("organizer", {}).get("email") or "").lower()
            # Owner participates: the owner has one identity per connected
            # calendar; organizer counts, a declined identity does not.
            participates = organizer_email in identities or any(
                (a.get("email") or "").lower() in identities
                and a.get("responseStatus") != "declined"
                for a in attendees
            )
            if not participates:
                continue
            # At least one human counterparty who has not declined — the
            # nudge exists to keep someone from being left waiting.
            counterparties = [
                a for a in attendees
                if is_human_external(a.get("email"), identities)
                and a.get("responseStatus") != "declined"
            ]
            # Google's 1:1 shape: the human organizer is often not echoed
            # into attendees when the owner is the invitee.
            if (is_human_external(organizer_email, identities)
                    and organizer_email not in {
                        (a.get("email") or "").lower() for a in attendees}):
                counterparties.append(ev["organizer"])
            if not counterparties:
                continue

            survivors.append((key, start_dt, minutes_until, virtual, location, ev))
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        # Never echo the event itself: it may hold private calendar text.
        print(f"malformed event: {e!r}", file=sys.stderr)
        return 2

    # Dedupe by (iCalUID, start) — RFC 5545 identity shared across every
    # calendar's copy; the start tiebreaker keeps a tight recurring series
    # from collapsing two occurrences. An EMPTY iCalUID never dedupes: two
    # reminders for one meeting cost less than one dropped meeting.
    survivors.sort(key=lambda s: s[1])
    seen = set()
    reminders = []
    card = None
    for key, start_dt, minutes_until, virtual, location, ev in survivors:
        if key[0]:
            if key in seen:
                continue
            seen.add(key)
        local_time = (start_dt.astimezone(tz).strftime("%I:%M%p")
                      .lstrip("0").lower())
        where = "online" if virtual else location
        # A URL in the TITLE is the same bearer risk the location rule
        # guards: strip it rather than post it to a shared surface.
        summary = " ".join(_URL_TOKEN.sub("", unwrap(ev.get("summary"))).split())
        reminders.append(compose(summary or "(untitled meeting)", local_time,
                                 minutes_until, where))
        if card is None and (ev["CalendarID"] in wall or key in wall_keys):
            card = reminders[-1]

    # The handoff is written HERE, never by the model: every qualifying
    # reminder, earliest first (each line ≤115, enforced above), for chat, and
    # the earliest wall-calendar one for the kiosk, or null. post_nudge owns
    # consume-on-success. stdout carries only the count the sheet routes on.
    # A direct write, on purpose: posting only begins after this process
    # exits 0, a mid-write crash exits nonzero and the sheet stops, and the
    # next tick overwrites the file — the staged-rename variant defended an
    # unobserved failure mode and was deleted (operator ruling, PR #26).
    if reminders:
        with open(HANDOFF, "w") as f:
            json.dump({"chat": reminders, "card": card}, f)

    json.dump({"qualifying": len(reminders)}, sys.stdout)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
