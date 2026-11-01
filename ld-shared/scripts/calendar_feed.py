#!/usr/bin/env python3
"""calendar_feed.py — publish the kiosk's calendar strip, with no model in it.

The kiosk's cards are composed by producers a model drives. The calendar
strip is not: it is a straight translation of the owner's Google calendar into
the viewer's `/api/calendar` contract, so there is nothing for a model to add
and every turn it would cost is a turn that can go wrong on private calendar
text. This script is the whole producer — gather, filter, normalize, POST.

Everything it reads is fixed; nothing arrives on argv:

  - `/var/lib/hermes/ld/config.json` for the account, the calendar ids and the
    household zone — the same `calendar.account` + `calendar.sources` shape
    every other calendar producer here reads;
  - latch, logged in with the agent's own credential and acting for the home
    chat (latch_cli.py), which the service reads from the root-owned container
    environment;
  - the kiosk endpoint through post_to_kiosk's own constants, so a
    dotenv-sourced one is held to the Pi's own message API on the household
    network before anything is sent to it.

The gather goes through any online host with Google on, `cloud` included, so
it never waits on the owner's Mac. The delivery cannot: this container is not
on the Pi's LAN, so the body is shipped from the owner's Mac, in the two calls
ld-shared/references/latch-delivery.md documents — put the file, then curl it.
That is where every household is: mint_wall_token.py writes
`DASHBOARD_DELIVERY=latch` unconditionally.

A consequence worth naming: the wall's bearer is never read here at all. It
lives in ~/Plow/ld/dashboard.hdr on the Mac and `curl -H @` reads it there, so
the token never crosses latch and this script never holds it.

Every failure is a one-line stand-down and exit 0. This runs unattended: a
non-zero exit buys nothing because nobody is reading, while a traceback
carrying calendar text is a leak. The Mac asleep is the common case, and the
next tick re-gathers and re-delivers. A refusal from the Mac's gatekeeper
stands down the same way, naming the refusal.

Event text is UNTRUSTED. Private and confidential occurrences — and every
sibling copy sharing their identity — are dropped first; then Latch's
untrusted-content markers and every URI-shaped token are stripped from what is
left. nudge_candidates.py filters through the same `visible_events` and
`event_key`, so the kiosk's two calendar surfaces cannot disagree on privacy.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from datetime import time as datetime_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import post_to_kiosk  # noqa: E402
from external_content import strip_markers  # noqa: E402
from latch_cli import LatchError, google, host_with, latch, mac, put, run  # noqa: E402
from runtime_env import AGENT_DOTENV, agent_values  # noqa: E402

CONFIG_FILE = "/var/lib/hermes/ld/config.json"
# ld-setup mints one bearer for the viewer's message API; the calendar API is
# its sibling behind the same bearer. Deriving it keeps ONE address in the
# dotenv and one thing for setup to re-point when the Pi moves.
MESSAGE_SUFFIX = "/api/message"
CALENDAR_SUFFIX = "/api/calendar"
# The Mac-side staging path, a sibling of the cards' ~/Plow/ld/card-<n>.json.
LATCH_BODY_PATH = "~/Plow/ld/calendar.json"
WINDOW_DAYS = 7
MAX_EVENTS = 250
# gog's own event fields, which is what latch answers with when the fields are
# named: unnamed, it returns a compact scheduling view with no visibility,
# iCalUID or status, which the privacy filter below cannot work without.
EVENT_FIELDS = "id,iCalUID,status,visibility,summary,location,start,end"

# Deliberately broad, the same trade nudge_candidates.py's redaction makes:
# native join links use schemes other than http, and stripping an ordinary
# letter-led colon token costs less than publishing a join credential to a
# display anyone in the house can read.
_URI_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:\S+")


class FeedError(Exception):
    """A safe, credential-free reason this run stood down."""


def redact(value):
    """Strip gog's markers and every URI-shaped token; collapse whitespace.

    Newlines included: the strip is a one-row-per-event contract, so a title
    carrying a newline could otherwise spoof a row.
    """
    return " ".join(_URI_TOKEN.sub("", strip_markers(value)).split())


def event_key(event):
    """One occurrence's cross-calendar identity — the nudge filter's key.

    Start only. Two copies sharing an iCalUID and a start ARE the same
    occurrence, so an end time can only make two views of it fail to match —
    and the copy that fails to match a private sibling is the one that
    publishes its title. All-day boundaries stay their date string; timed ones
    become UTC instants, so copies written with different offsets compare
    equal.
    """
    start = event["start"]
    if "dateTime" not in start:
        return (event.get("iCalUID"), start.get("date"))
    instant = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
    if instant.utcoffset() is None:
        raise ValueError("timed event start has no UTC offset")
    return (event.get("iCalUID"), instant.astimezone(timezone.utc))


def visible_events(events):
    """Drop cancelled, private, and private-sibling copies.

    An invite appears once per calendar it is on, all copies sharing an
    iCalUID. ANY private or confidential copy means "do not surface this", so
    the whole occurrence goes — dropping only the private copy would publish
    the title through a default-visibility sibling.
    """
    def private(event):
        return event.get("visibility") in ("private", "confidential")

    private_keys = {
        event_key(event) for event in events
        if private(event) and event.get("iCalUID")
    }
    return [
        event for event in events
        if event["status"] != "cancelled"
        and not private(event)
        and event_key(event) not in private_keys
    ]


def kiosk_config(dotenv):
    """The kiosk's calendar URL, or (None, a stand-down reason).

    Goes through post_to_kiosk's constants rather than re-deriving them, so the
    agent-writable dotenv is held to the same endpoint shape and the same
    household-network refusal a card post is. No bearer: the Mac holds it.
    """
    def optional(file_path, env_name):
        # File, then the dotenv — deliberately NOT the process env. The unit
        # loads no EnvironmentFile, so nothing has laundered a dotenv line into
        # os.environ where it would read as trusted and skip the validator
        # below. Provenance is the whole gate.
        try:
            file_value = Path(file_path).read_text().strip()
        except OSError:
            file_value = ""
        if file_value:
            return file_value, "file"
        return dotenv.get(env_name, "").strip(), "dotenv"

    url, source = optional(post_to_kiosk.ENDPOINT_FILE, post_to_kiosk.ENDPOINT_ENV)
    if not url:
        return None, "kiosk is not configured"
    if not url.startswith(post_to_kiosk.REQUIRED_URL_PREFIXES):
        return None, "kiosk URL is not http(s)"
    if source == "dotenv":
        # Exits non-zero, unlike everything else here. The dotenv is
        # agent-writable at runtime, so an endpoint that is not the Pi's own
        # message API is an injected host about to be handed a bearer — the
        # one condition that must be loud rather than a stand-down line.
        post_to_kiosk._validate_dotenv_endpoint(url)
    if not url.endswith(MESSAGE_SUFFIX):
        return None, "kiosk URL does not end with /api/message"
    return url[: -len(MESSAGE_SUFFIX)] + CALENDAR_SUFFIX, None


def read_config():
    """The account, its calendar ids and the household zone, or FeedError."""
    try:
        config = json.loads(Path(CONFIG_FILE).read_text())
        account = str(config["calendar"]["account"]).strip()
        zone = str(config["family"]["timezone"]).strip()
        calendar_ids = [str(s["calendar_id"]).strip()
                        for s in config["calendar"]["sources"]]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise FeedError("calendar config unavailable") from exc
    if not account or not zone or not calendar_ids or not all(calendar_ids):
        raise FeedError("calendar config incomplete")
    return account, calendar_ids, zone


def command_args(account, calendar_ids):
    """The gather: the next seven days of the chosen calendars, gog's fields."""
    return [
        "calendar", "events",
        f"--account={account}",
        f"--calendars={','.join(calendar_ids)}",
        f"--days={WINDOW_DAYS}", "--sort=start", f"--max={MAX_EVENTS}",
        f"--select={EVENT_FIELDS}",
    ]


def normalize_events(events, zone):
    """gog's event shape as the viewer's contract, earliest first."""
    try:
        local = ZoneInfo(zone)
        normalized = []
        seen = set()
        for event in visible_events(events):
            start_data, end_data = event["start"], event["end"]
            all_day = "dateTime" not in start_data
            start = start_data["date"] if all_day else start_data["dateTime"]
            end = end_data["date"] if all_day else end_data["dateTime"]
            # An empty iCalUID never dedupes: two rows for one meeting cost
            # less than one dropped meeting, the trade the nudge also makes.
            key = event_key(event)
            uid = event.get("iCalUID")
            if isinstance(uid, str) and uid:
                if key in seen:
                    continue
                seen.add(key)
            if all_day:
                instant = datetime.combine(
                    date.fromisoformat(start), datetime_time.min, local)
            else:
                instant = datetime.fromisoformat(start.replace("Z", "+00:00"))
                if instant.utcoffset() is None:
                    raise ValueError("timed event start has no UTC offset")
            location = redact(event.get("location"))
            normalized.append((instant, {
                "uid": event["id"],
                "title": redact(event.get("summary")),
                "start": start,
                "end": end,
                "isAllDay": all_day,
                "location": location or None,
            }))
    except (KeyError, TypeError, ValueError) as exc:
        raise FeedError("malformed calendar event") from exc
    if not all(isinstance(payload["uid"], str) for _, payload in normalized):
        raise FeedError("malformed calendar event")
    normalized.sort(key=lambda item: item[0])
    return [payload for _, payload in normalized]


def gather(host, args):
    """The events latch answered with, each still naming its account."""
    events = google(*args, host=host).get("items")
    if not isinstance(events, list):
        raise FeedError("gather answered with no event list")
    return events


def curl_argv(calendar_url):
    """The Mac-side POST, verbatim in the shape latch-delivery.md documents.

    One fixed literal per household. The bearer is not in it and must not be:
    it is in ~/Plow/ld/dashboard.hdr, written once by ld-setup, and `-H @`
    reads it there rather than putting it on an argv the Mac records.
    """
    return ["sh", "-c",
            "curl -fsS -H @$HOME/Plow/ld/dashboard.hdr "
            "-H 'Content-Type: application/json' "
            f"--data-binary @$HOME/Plow/ld/calendar.json {calendar_url}"]


def deliver_via_latch(host, calendar_url, feed):
    """Ship the body from the owner's Mac: put the file, then curl it.

    The two calls latch-delivery.md documents, in that order, made here rather
    than handed to a model -- there is no model in this run. The run is not
    done until the curl exited 0, which `-f` ties to a 2xx.
    """
    put(host, LATCH_BODY_PATH, json.dumps(feed))
    run(host, curl_argv(calendar_url), network=True)


def main(*, now=None):
    now = int(time.time()) if now is None else now
    # The agent's own file, for the wall's endpoint.
    dotenv = agent_values(AGENT_DOTENV)

    try:
        account, calendar_ids, zone = read_config()
    except FeedError as exc:
        print(f"calendar feed not configured: {exc}")
        return 0

    calendar_url, reason = kiosk_config(dotenv)
    if calendar_url is None:
        print(f"calendar feed not configured: {reason}")
        return 0

    try:
        status = latch("status")
        events = normalize_events(
            gather(host_with("google", status),
                   command_args(account, calendar_ids)), zone)
        feed = {
            "generated_at": datetime.fromtimestamp(now, timezone.utc)
            .isoformat().replace("+00:00", "Z"),
            "window_days": WINDOW_DAYS,
            "events": events,
        }
        deliver_via_latch(mac(status), calendar_url, feed)
    except (FeedError, LatchError) as exc:
        # The next tick is the retry; nothing is remembered between them. A
        # sleeping Mac is the common case and is not an error worth more.
        print(f"calendar feed failed: {exc}")
        return 0

    print(f"calendar feed: {len(events)} events; shipped through the Mac")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
