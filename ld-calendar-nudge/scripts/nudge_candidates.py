#!/usr/bin/env python3
"""nudge_candidates.py — filter + compose the calendar gather for ld-calendar-nudge.

Reads the fixed gog `calendar events list --json --results-only` output from
its gather-file argument, deleting the file as it goes, applies the nudge
rules — privacy prepass, per-event filter, dedupe — and writes the composed
≤115-char reminders STRAIGHT to the two fixed handoffs the posting legs
consume: the earliest qualifying reminder to KIOSK_FILE (post_nudge.py's
one-line card) and every reminder to CHAT_FILE (send_nudge_chat.py's
message). stdout carries only {"qualifying": N} — the model routes on the
count and never touches reminder content, so the helper chain takes zero
model-controlled content end to end (the plow#625 shape). Deterministic on
purpose: the rules were 200 lines of sheet prose upstream; now they are code
with a test per rule.

The gather path is the one model-supplied argument, so it is validated
BEFORE any open/unlink: only a runtime-persisted result (under
/tmp/hermes-results/) or the fixed inline handoff
(/opt/data/ld/calendar-nudge-gather) is accepted — anything else exits 2
without touching the file, so an injected turn cannot aim the
consume-on-read unlink at the config or the dotenv.

Field spellings are pinned against a REAL gather through the live Latch door
(camelCase: iCalUID, start.dateTime, hangoutLink, attendees[].responseStatus;
`visibility` absent means default). Latch's injected safety flags wrap every
free-text field (summary, location, description, ...) in
EXTERNAL_UNTRUSTED_CONTENT markers and prepend a "Note:" preamble line to the
output — both are stripped here so marker soup never reaches the kiosk.

Exit 2 on malformed input rather than skipping rows: a half-parsed window is
indistinguishable from a quiet one on both surfaces, so it must fail loudly.
That includes a nonzero envelope exit_code — gog fails the WHOLE gather on
one unrecognized calendar name (measured: exit 2), and a failed gather must
never read as a no-nudge run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

LIMIT = 115
# The only gather locations the model may name (validated before any I/O):
# the runtime's persisted-result directory, and the fixed file the sheet
# writes an inline result to. Trailing slash on the root is load-bearing —
# it is a prefix check, and "/tmp/hermes-results-evil" must not pass.
PERSISTED_ROOT = "/tmp/hermes-results/"
GATHER_FILE = "/opt/data/ld/calendar-nudge-gather"
# The two fixed handoffs this helper writes and the posting legs consume.
KIOSK_FILE = "/opt/data/ld/calendar-nudge-text"
CHAT_FILE = "/opt/data/ld/calendar-nudge-chat"
_MARKERS = re.compile(r'<<<(?:END_)?EXTERNAL_UNTRUSTED_CONTENT id="[^"]*">>>')
_URL = re.compile(r"https?://", re.IGNORECASE)
# Destinations, not people: Google's shared-calendar and booking-resource
# suffixes. A mirrored invite or a room is nobody left waiting.
_NON_HUMAN_SUFFIXES = ("@group.calendar.google.com",
                       "@resource.calendar.google.com")


def unwrap(value):
    """Strip Latch's untrusted-content markers and collapse all whitespace —
    newlines included — to single spaces. The composed reminder is a ONE-line
    contract: an event title carrying an embedded newline could otherwise
    spoof extra reminder-looking lines on the shared kiosk."""
    return " ".join(_MARKERS.sub("", value).split()) if isinstance(value, str) else ""


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--now", type=int, default=None,
                        help="unix seconds; tests only, defaults to time.time()")
    parser.add_argument("gather",
                        help="gather file (raw gog --json --results-only "
                             "output, or the persisted plow_run_command "
                             "result envelope)")
    args = parser.parse_args(argv)

    if not gather_path_allowed(args.gather):
        print(f"refusing gather path {args.gather!r}: only a persisted "
              f"result under {PERSISTED_ROOT} or the fixed {GATHER_FILE} is "
              "accepted -- this script consumes its input, and an arbitrary "
              "path would be an arbitrary delete", file=sys.stderr)
        return 2

    # The gather is consumed FIRST: the raw calendar corpus must not outlive
    # this run whatever the outcome, so it's read and deleted before anything
    # else (a broken config, a bad envelope) gets a chance to abort the run.
    with open(args.gather) as f:
        raw = f.read().strip()
    os.unlink(args.gather)

    # Same exit-2 contract as the gather below: a broken config must fail
    # loudly, never surface as a traceback-with-exit-1 or a quiet run.
    try:
        with open(args.config) as f:
            config = json.load(f)
        tz = ZoneInfo(config["family"]["timezone"])
        nudge_cfg = config["calendar_nudge"]
        lookahead_virtual = nudge_cfg["lookahead_virtual_minutes"]
        lookahead_in_person = nudge_cfg["lookahead_in_person_minutes"]
        identities = {str(e).strip().lower()
                      for e in nudge_cfg["owner_identities"] if str(e).strip()}
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"bad config {args.config}: {e!r}", file=sys.stderr)
        return 2
    if not identities:
        # An empty identity set fails owner-participation on EVERY event — a
        # config mistake that presents as an eternally quiet nudge.
        print("calendar_nudge.owner_identities is empty -- every event would "
              "fail the owner-participation rule and no nudge would ever "
              "fire", file=sys.stderr)
        return 2

    # An oversized plow_run_command result reaches the model as a persisted
    # envelope — {"result": "<json of {exit_code, handle, output, ...}>"} —
    # not as raw gog stdout. gog's output opens with a JSON array (after
    # Latch's "Note:" preamble), never an object, so the sniff cannot misfire.
    if raw.startswith("{"):
        try:
            inner = json.loads(json.loads(raw)["result"])
            if inner["exit_code"] != 0:
                print(f"gather failed: exit_code={inner['exit_code']}",
                      file=sys.stderr)
                return 2
            raw = inner["output"].strip()
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
            print(f"malformed gather envelope: {e}", file=sys.stderr)
            return 2

    # Latch prepends preamble lines ("Note: Using direct access token ...")
    # to the command output; the events are the array that follows.
    match = re.search(r"^\[", raw, re.MULTILINE)
    if not match:
        print("no event array in gather output", file=sys.stderr)
        return 2
    try:
        events = json.loads(raw[match.start():])
    except json.JSONDecodeError as e:
        print(f"malformed gog json: {e}", file=sys.stderr)
        return 2

    now = args.now if args.now is not None else int(time.time())
    now_dt = datetime.fromtimestamp(now, tz=timezone.utc)

    try:
        # Privacy prepass: one invite appears once per calendar it is on, all
        # copies sharing an iCalUID. ANY private/confidential copy means "do
        # not surface this" — drop every copy sharing its key, or a
        # default-visibility sibling would post the title to the shared kiosk.
        private_keys = {
            (ev["iCalUID"], ev["start"].get("dateTime") or ev["start"].get("date"))
            for ev in events
            if ev.get("visibility") in ("private", "confidential")
        }

        survivors = []
        for ev in events:
            key = (ev["iCalUID"],
                   ev["start"].get("dateTime") or ev["start"].get("date"))
            if key in private_keys:
                continue
            if ev["status"] == "cancelled":
                continue
            # All-day events have start.date only; a date parsed as midnight
            # would fire a misleading late-night reminder. They belong to the
            # morning-updates/weekly-digest surfaces.
            start_iso = ev["start"].get("dateTime")
            if not start_iso:
                continue
            start_dt = datetime.fromisoformat(start_iso)
            minutes_until = int((start_dt - now_dt).total_seconds() // 60)

            location = unwrap(ev.get("location"))
            # Virtual = a structured video link OR a URL in the location.
            # Only a real link counts — keyword-matching ("Zoom"/"Meet")
            # false-positives on "Meeting Room". The raw URL is a bearer-style
            # join token and never reaches a surface; compose renders `online`.
            virtual = bool(ev.get("hangoutLink")) or bool(_URL.search(location))
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
    for key, start_dt, minutes_until, virtual, location, ev in survivors:
        if key[0]:
            if key in seen:
                continue
            seen.add(key)
        local_time = (start_dt.astimezone(tz).strftime("%I:%M%p")
                      .lstrip("0").lower())
        where = "online" if virtual else location
        reminders.append(compose(unwrap(ev.get("summary")), local_time,
                                 minutes_until, where))

    # The handoffs are written HERE, never by the model: the kiosk card gets
    # the earliest qualifying reminder (one line, the ≤115 contract enforced
    # above), the chat message gets them all. Each posting leg consumes its
    # own file on success. stdout carries only the count the sheet routes on.
    # Staged then renamed: the fresh-kiosk/stale-chat crash window narrows
    # from two full writes to the instants between two renames — not zero,
    # and deliberately not chased further (the next tick overwrites both,
    # orphaned .tmp files included).
    if reminders:
        pairs = ((KIOSK_FILE, reminders[0] + "\n"),
                 (CHAT_FILE, "\n".join(reminders) + "\n"))
        for path, body in pairs:
            with open(path + ".tmp", "w") as f:
                f.write(body)
        for path, _ in pairs:
            os.replace(path + ".tmp", path)

    json.dump({"qualifying": len(reminders)}, sys.stdout)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
