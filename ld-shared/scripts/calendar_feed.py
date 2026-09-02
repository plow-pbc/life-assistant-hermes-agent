#!/usr/bin/env python3
"""calendar_feed.py — publish the kiosk's calendar strip, with no model in it.

The kiosk's five cards are composed by producers a model drives. The calendar
strip is not: it is a straight translation of what gog returns into the
viewer's `/api/calendar` contract, so there is nothing for a model to add and
every turn it would cost is a turn that can go wrong on private calendar text.
This script is the whole producer — gather, filter, normalize, POST.

Everything it reads is fixed; nothing arrives on argv:

  - `/var/lib/hermes/ld/config.json` for the account, the calendar ids and the
    household zone — the same `calendar.account` + `calendar.sources` shape
    every other calendar producer here reads;
  - the Plow relay credential (`DOMO_DEVICE_UID`, `DOMO_MCP_TOKEN`) from the
    Hermes dotenv, the pair `just check-latch` probes. The relay ORIGIN is a
    literal here, as it is in `runtime/config.yaml` and that recipe: the
    dotenv is agent-writable, and an injected base would walk the relay bearer
    to another host with nothing to refuse it;
  - the kiosk endpoint through post_to_kiosk's own constants, so a
    dotenv-sourced one is held to the Pi's own message API on the household
    network before anything is sent to it.

ONE delivery path: the body is shipped from the owner's Mac, in the two fixed
calls ld-shared/references/latch-delivery.md documents — write the file, then
curl it. That is where every household is: mint_wall_token.py writes
`DASHBOARD_DELIVERY=latch` unconditionally, because this container is not on
the Pi's LAN. This producer makes those two calls ITSELF, over the same relay
it gathers through; the card producers hand them to a model only because a
model is already in their loop.

A consequence worth naming: the wall's bearer is never read here at all. It
lives in ~/Plow/ld/dashboard.hdr on the Mac and `curl -H @` reads it there, so
the token never crosses the relay and this script never holds it.

The gather argv is byte-identical to the seven-day one ld-weekly-digest
already uses, deliberately: Latch always-allow keys on the exact argv, README
"Bring-up" has the owner approve each of the 1-, 3- and 7-day shapes once, and
a fourth shape would strand every unattended run on an approval card nobody
answers (plow-pbc/latch#181). `--days=7` is already relative to the moment of
the call, so there is nothing for a computed timestamp to add.

Every failure is a one-line stand-down and exit 0. This runs unattended: a
non-zero exit buys nothing because nobody is reading, while a traceback
carrying calendar text is a leak. Three consecutive failures back off for an
hour, so a dead Pi or a revoked relay token is not retried every five minutes
forever.

Event text is UNTRUSTED. Private and confidential occurrences — and every
sibling copy sharing their identity — are dropped first; then Latch's
untrusted-content markers and every URI-shaped token are stripped from what is
left. The helpers for that are inline rather than lifted out of
nudge_candidates.py, which keeps its own: sharing them means editing a live
filter, and that is its own change.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from datetime import time as datetime_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import post_to_kiosk  # noqa: E402
from bearer_http import open_no_redirect  # noqa: E402
from external_content import strip_markers  # noqa: E402
from runtime_env import AGENT_DOTENV, agent_values  # noqa: E402

CONFIG_FILE = "/var/lib/hermes/ld/config.json"
# Fixed, never from the dotenv. runtime/config.yaml and the justfile's
# check-latch both name this origin outright; taking it from the
# agent-writable dotenv instead would hand an injected turn the relay bearer.
RELAY_ORIGIN = "https://api.plow.co"
# ld-setup mints one bearer for the viewer's message API; the calendar API is
# its sibling behind the same bearer. Deriving it keeps ONE address in the
# dotenv and one thing for setup to re-point when the Pi moves.
MESSAGE_SUFFIX = "/api/message"
CALENDAR_SUFFIX = "/api/calendar"
# The Mac-side staging path, a sibling of the cards' ~/Plow/ld/card-<n>.json.
# Anything under ~/Plow auto-approves on the Mac; the curl argv below is one
# more fixed shape the owner approves once, like each gather window.
LATCH_BODY_PATH = "~/Plow/ld/calendar.json"
WINDOW_DAYS = 7
MAX_EVENTS = 250

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


def decode_events(raw):
    """The JSON event array that follows Latch's optional preamble line."""
    match = re.search(r"^\[", raw, re.MULTILINE)
    if not match:
        raise FeedError("no event array in gather output")
    try:
        events = json.loads(raw[match.start():])
    except json.JSONDecodeError as exc:
        raise FeedError("malformed gog json") from exc
    if not isinstance(events, list):
        raise FeedError("gather payload is not an array")
    return events


def relay_config(dotenv):
    """The Plow relay endpoint and bearer, or (None, the missing name).

    The device uid names WHICH Mac, the token authorises reaching it — the
    same pair `just check-latch` probes. Returned rather than exited on: an
    instance that has not minted a relay credential is unconfigured, not
    broken.
    """
    uid = dotenv.get("DOMO_DEVICE_UID", "").strip()
    token = dotenv.get("DOMO_MCP_TOKEN", "").strip()
    for name, present in (("DOMO_DEVICE_UID", uid), ("DOMO_MCP_TOKEN", token)):
        if not present:
            return None, name
    return (f"{RELAY_ORIGIN}/v1/relay/devices/{uid}/mcp", token), None


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


def command_argv(account, calendar_ids):
    """The one fixed gather argv — see the module docstring on always-allow."""
    return [
        "gog", "calendar", "events", "list",
        f"--account={account}",
        f"--calendars={','.join(calendar_ids)}",
        f"--days={WINDOW_DAYS}", "--json", "--results-only",
        "--sort=start", f"--max={MAX_EVENTS}",
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


def _decode_command_response(result):
    """The command's stdout out of a plow_run_command result, or FeedError.

    Every layer is checked by name — the content block, the payload status, a
    non-zero exit_code — because each of those is a FAILED call that would
    otherwise read as success: a failed gather decodes to zero events and
    publishes an empty week, and a failed curl leaves a stale strip on the wall
    while the run reports fine.
    """
    try:
        text = next(block["text"] for block in result["content"]
                    if block.get("type") == "text")
        payload = json.loads(text)
    except FeedError:
        raise
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        raise FeedError("malformed relay response") from exc
    if payload.get("status") != "completed" or payload.get("exit_code") != 0:
        raise FeedError("relay command did not complete")
    output = payload.get("output")
    if not isinstance(output, str):
        raise FeedError("relay command returned no output")
    return output


def _post_json(url, token, body, label):
    request = urllib.request.Request(
        url=url, method="POST", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    try:
        with open_no_redirect(request, timeout=30) as response:
            return response.read(), response.status
    except urllib.error.HTTPError as exc:
        # The code, never the body: an error body can echo the argv back.
        raise FeedError(f"{label} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise FeedError(f"{label} request failed") from exc


def relay(url, token, name, arguments):
    """One MCP tools/call on the owner's Mac; returns the result object."""
    body, _ = _post_json(url, token, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }, "relay")
    try:
        envelope = json.loads(body)
    except json.JSONDecodeError as exc:
        raise FeedError("relay returned malformed JSON") from exc
    if "error" in envelope:
        # The Mac asleep or Latch not running lands here. One line, and the
        # next tick re-gathers and re-delivers -- latch-delivery.md's own rule.
        raise FeedError(f"relay returned an MCP error for {name}")
    try:
        result = envelope["result"]
    except (KeyError, TypeError) as exc:
        raise FeedError("malformed relay response") from exc
    if result.get("isError") is True:
        raise FeedError(f"relay {name} failed")
    return result


def gather(url, token, argv):
    return decode_events(_decode_command_response(
        relay(url, token, "plow_run_command", {"argv": argv})))


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


def deliver_via_latch(relay_url, relay_token, calendar_url, feed):
    """Ship the body from the owner's Mac: write the file, then curl it.

    The two calls latch-delivery.md documents, in that order, made here rather
    than handed to a model -- there is no model in this run. The run is not
    done until the curl returned 2xx, which is what _decode_command_response
    holds the second call to.
    """
    relay(relay_url, relay_token, "plow_write_file",
          {"path": LATCH_BODY_PATH, "content": json.dumps(feed)})
    _decode_command_response(
        relay(relay_url, relay_token, "plow_run_command",
              {"argv": curl_argv(calendar_url), "network": True}))


def main(*, now=None):
    now = int(time.time()) if now is None else now
    # The agent's own file: the wall's endpoint and the relay pair are the
    # agent's to record; the tenant's credential is not, and is not in a file.
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

    relay, missing = relay_config(dotenv)
    if relay is None:
        print(f"calendar feed not configured: {missing} missing")
        return 0
    relay_url, relay_token = relay

    try:
        events = normalize_events(
            gather(relay_url, relay_token,
                   command_argv(account, calendar_ids)), zone)
        feed = {
            "generated_at": datetime.fromtimestamp(now, timezone.utc)
            .isoformat().replace("+00:00", "Z"),
            "window_days": WINDOW_DAYS,
            "events": events,
        }
        deliver_via_latch(relay_url, relay_token, calendar_url, feed)
    except FeedError as exc:
        # The next tick is the retry; nothing is remembered between them. A
        # sleeping Mac is the common case and is not an error worth more.
        print(f"calendar feed failed: {exc}")
        return 0

    print(f"calendar feed: {len(events)} events; shipped through the Mac")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
