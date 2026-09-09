#!/usr/bin/env python3
"""calendar_list.py -- the owner's calendars, as JSON a turn can act on.

Reads the gather file written by

    plow_run_command(argv=["plow-gog", "calendar", "calendars", "--json", "--results-only", "--account", account])

and prints ONE object on stdout:

    {"account": "<authenticated address supplied by the caller, or null>",
     "candidates": ["<authenticated address>", ...],
     "calendars": [{"id": ..., "display": ..., "accessRole": ...}, ...]}

Everything here exists because the alternative was a model parsing this by
eye, and each step of that parse has a way to go quietly wrong:

  * The output is NOT JSON. plow-gog prints `Note: Using direct access token ...`
    before the array, so json.loads() on the whole thing fails on a working
    call -- and a turn that treats that as "no calendars" reports the wrong
    thing to the owner. The array is found by its first bracket.
  * A large result comes back as a persisted envelope naming a file instead of
    the text. read_gather() (ld-shared) already unwraps that shape and refuses
    a nonzero exit_code, so it is reused rather than re-implemented.
  * The names arrive wrapped. The runtime fences text it fetched from Google
    in `<<<EXTERNAL_UNTRUSTED_CONTENT ...>>>` markers, so `summary` is a
    five-line block with the calendar's actual name on the inside.
    unwrap_external() (ld-shared) takes it off, because the alternative is the
    model lifting the name out by eye -- and a listing where some rows are
    fenced and some are not is exactly the shape an eye normalises
    inconsistently.
  * The authenticated account comes from `plow-gog accounts`, never calendar
    ownership or a primary calendar ID. The caller supplies that account.

Unwrapping is a display concern and NOT a promise about the content. The name
inside the markers is the same attacker-controlled text it was outside them;
the markers said so, and dropping them drops the label, not the risk. Which is
why the rule below is unconditional rather than something the markers relax.

`display` is a display string and nothing else. It comes off calendars other
people own, so it is attacker-controlled text: it may be cached by the background discovery service and shown to the owner
in the pick message, but it must never reach a shell command or the household
config. Only `id` is stored in calendar.sources.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"))

from external_content import unwrap_external  # noqa: E402
from gather_result import GatherError, read_gather  # noqa: E402


def extract_array(text):
    """The JSON array in `text`, whatever precedes it.

    Anchored on the first `[` rather than a regex over the preamble: the note
    line's wording is plow-gog's to change, and a parser that knows it by heart
    breaks on the next release for no reason a reader could guess.
    """
    start = text.find("[")
    if start < 0:
        raise GatherError("no JSON array in the calendar listing")
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError as e:
        raise GatherError(f"calendar listing is not valid JSON: {e}") from e


def normalize(entries, *, account=None):
    """{account, candidates, calendars} from plow-gog's calendar list."""
    if not isinstance(entries, list):
        raise GatherError("calendar listing is not a list")
    calendars = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise GatherError("calendar listing holds a non-object entry")
        cid = entry.get("id")
        if not isinstance(cid, str) or not cid.strip():
            raise GatherError("a calendar in the listing has no id")
        # summaryOverride is the owner's own rename and wins when present --
        # it is what they see in Google Calendar, so it is what they will
        # recognise being read back to them.
        display = (unwrap_external(entry.get("summaryOverride"))
                   or unwrap_external(entry.get("summary"))
                   or cid)
        role = str(entry.get("accessRole") or "")
        calendars.append({"id": cid, "display": str(display), "accessRole": role})
    return {"account": account,
            "candidates": [account] if account else [],
            "calendars": calendars}


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
