#!/usr/bin/env python3
"""calendar_list.py -- the owner's calendars, as JSON a turn can act on.

Reads the gather file written by

    plow_run_command(argv=["gog", "calendar", "calendars", "--json", "--results-only"])

and prints ONE object on stdout:

    {"account": "<the primary entry's id>",
     "calendars": [{"id": ..., "display": ..., "accessRole": ...}, ...]}

Everything here exists because the alternative was a model parsing this by
eye, and each step of that parse has a way to go quietly wrong:

  * The output is NOT JSON. gog prints `Note: Using direct access token ...`
    before the array, so json.loads() on the whole thing fails on a working
    call -- and a turn that treats that as "no calendars" reports the wrong
    thing to the owner. The array is found by its first bracket.
  * A large result comes back as a persisted envelope naming a file instead of
    the text. read_gather() (ld-shared) already unwraps that shape and refuses
    a nonzero exit_code, so it is reused rather than re-implemented.
  * The account is the `primary` entry's id, NOT `dataOwner`. Calendars shared
    into an account keep their own owner -- a real listing had three distinct
    dataOwner values across nine calendars -- so deriving the account from it
    picks whichever calendar happened to be read last.

`display` is a display string and nothing else. It comes off calendars other
people own, so it is attacker-controlled text: it may be shown to the owner in
the pick message, and it must never reach a shell command or the config. Only
`id` is written anywhere durable.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"))

from gather_result import GatherError, read_gather  # noqa: E402


def extract_array(text):
    """The JSON array in `text`, whatever precedes it.

    Anchored on the first `[` rather than a regex over the preamble: the note
    line's wording is gog's to change, and a parser that knows it by heart
    breaks on the next release for no reason a reader could guess.
    """
    start = text.find("[")
    if start < 0:
        raise GatherError("no JSON array in the calendar listing")
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError as e:
        raise GatherError(f"calendar listing is not valid JSON: {e}") from e


def normalize(entries):
    """{account, calendars} from gog's calendar list."""
    if not isinstance(entries, list):
        raise GatherError("calendar listing is not a list")
    calendars, account = [], None
    for entry in entries:
        if not isinstance(entry, dict):
            raise GatherError("calendar listing holds a non-object entry")
        cid = entry.get("id")
        if not isinstance(cid, str) or not cid.strip():
            raise GatherError("a calendar in the listing has no id")
        # summaryOverride is the owner's own rename and wins when present --
        # it is what they see in Google Calendar, so it is what they will
        # recognise being read back to them.
        display = entry.get("summaryOverride") or entry.get("summary") or cid
        calendars.append({"id": cid,
                          "display": str(display),
                          "accessRole": str(entry.get("accessRole") or "")})
        if entry.get("primary") is True:
            if account is not None and account != cid:
                raise GatherError("two calendars claim to be primary")
            account = cid
    if not calendars:
        raise GatherError("no calendars in the listing")
    if account is None:
        # Without a primary there is no account to write, and guessing one
        # from the first row would put a shared calendar's address into
        # calendar.account, where every producer would then authenticate as
        # somebody else.
        raise GatherError("no calendar is flagged primary, so the account is unknown")
    return {"account": account, "calendars": calendars}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        raise SystemExit("usage: calendar_list.py <gather-file>")
    try:
        result = normalize(extract_array(read_gather(argv[0])))
    except GatherError as exc:
        raise SystemExit(f"refusing to list calendars: {exc}") from None
    except OSError as exc:
        raise SystemExit(f"refusing to list calendars: {exc}") from None
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
