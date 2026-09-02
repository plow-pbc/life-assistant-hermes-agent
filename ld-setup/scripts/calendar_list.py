#!/usr/bin/env python3
"""calendar_list.py -- the owner's calendars, as JSON a turn can act on.

Reads the gather file written by

    plow_run_command(argv=["gog", "calendar", "calendars", "--json", "--results-only"])

and prints ONE object on stdout:

    {"account": "<the owner's address, or null when it cannot be derived>",
     "candidates": ["<owner-role dataOwner>", ...],
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
  * The names arrive wrapped. The runtime fences text it fetched from Google
    in `<<<EXTERNAL_UNTRUSTED_CONTENT ...>>>` markers, so `summary` is a
    five-line block with the calendar's actual name on the inside. Unwrapped
    here, deterministically, because the alternative is the model lifting the
    name out by eye -- and a listing where some rows are fenced and some are
    not is exactly the shape an eye normalises inconsistently.
  * The account is derived, not assumed. `primary: true` is the clearest
    signal and is used when it is there -- but it was seen on ONE Mac and
    nothing documents that gog always emits it, so its absence is a case and
    not an error. Falling back on `dataOwner` needs care: calendars shared
    into an account keep their own owner (one real listing carried three
    distinct values across nine calendars), so only the owner-role calendars
    are considered, and only when they agree. When nothing decides it the
    account is `null` and the caller asks the owner, which is the honest
    answer -- guessing here writes a shared calendar's address into
    `calendar.account` and every producer authenticates as somebody else.
    `candidates` carries the owner-role addresses so that question can be
    asked with the answers in it rather than as an open one; deriving them a
    second time by eye is the parse this script exists to prevent.

Unwrapping is a display concern and NOT a promise about the content. The name
inside the markers is the same attacker-controlled text it was outside them;
the markers said so, and dropping them drops the label, not the risk. Which is
why the rule below is unconditional rather than something the markers relax.

`display` is a display string and nothing else. It comes off calendars other
people own, so it is attacker-controlled text: it may be shown to the owner in
the pick message, and it must never reach a shell command or the config. Only
`id` is written anywhere durable.
"""
from __future__ import annotations

import json
import os
import re
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


# One fenced block and nothing else: the opening marker, the `Source:` line the
# runtime adds, a `---` rule, the content, and a closing marker carrying THE
# SAME id. Anchored end to end, and the id is a backreference rather than a
# second `\w+`, so a name that merely contains marker-shaped text is left alone
# instead of being cut at whatever looked like a fence.
_WRAPPED = re.compile(
    r'\A<<<EXTERNAL_UNTRUSTED_CONTENT id="(?P<id>[^"\n]*)">>>\n'
    r'(?:Source:[^\n]*\n)?'
    r'---\n'
    r'(?P<body>.*)'
    r'\n<<<END_EXTERNAL_UNTRUSTED_CONTENT id="(?P=id)">>>\Z',
    re.DOTALL)


def unwrap_external(text):
    """The content of an `<<<EXTERNAL_UNTRUSTED_CONTENT>>>` block, or `text`.

    Deterministic in both directions: text that is not exactly one whole block
    comes back untouched. That is the case that matters, because the fencing is
    not uniform -- the same listing carries a wrapped `summary` beside a bare
    `summaryOverride` -- and a stripper that guessed at partial matches would
    turn an inconsistent input into an inconsistently mangled one.

    `.*` is greedy under DOTALL, so a body holding its own end-marker keeps it:
    the outermost fence is the real one, and the forgery stays visible as text
    rather than truncating the name at somebody else's say-so.
    """
    if not isinstance(text, str):
        return text
    match = _WRAPPED.match(text)
    return match.group("body") if match else text


def normalize(entries):
    """{account, candidates, calendars} from gog's calendar list."""
    if not isinstance(entries, list):
        raise GatherError("calendar listing is not a list")
    calendars, flagged, owned_by = [], [], set()
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
        if entry.get("primary") is True:
            flagged.append(cid)
        # Only owner-role rows vote: a calendar someone shared in carries THEIR
        # address in dataOwner, and counting it would make a stranger's account
        # a candidate for this owner's config.
        if role == "owner":
            owner_of = entry.get("dataOwner")
            if isinstance(owner_of, str) and owner_of.strip():
                owned_by.add(owner_of)
    if not calendars:
        raise GatherError("no calendars in the listing")
    return {"account": derive_account(flagged, owned_by),
            "candidates": sorted(owned_by),
            "calendars": calendars}


def derive_account(flagged, owned_by):
    """The authenticated account, or None when nothing decides it.

    Three branches, in order of how much they assume:

    1. exactly one calendar flagged `primary` -- the clearest signal, and the
       only one seen live;
    2. no flag, but the owner-role calendars all name one `dataOwner` -- that
       address owns everything this account can write to, so it is the account;
    3. anything else -- two primaries, or owner-role calendars disagreeing, or
       no signal at all. None, and the caller asks.

    Returning None rather than picking is the point. An account guessed wrong
    is not a visible failure: it is written to `calendar.account`, every
    producer authenticates as that identity, and the reads come back thin or
    empty for reasons no one traces back to here.
    """
    if len(set(flagged)) == 1:
        return flagged[0]
    if not flagged and len(owned_by) == 1:
        return next(iter(owned_by))
    return None


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
