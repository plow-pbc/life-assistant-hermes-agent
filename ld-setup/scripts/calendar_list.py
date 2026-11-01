"""calendar_list.py -- one account's calendars, as choices a turn can offer.

calendar_discovery.py hands this each account's rows from latch's
`calendar calendars` fan-out, and gets back:

    {"account": "<the account latch read them through>",
     "candidates": ["<authenticated address>", ...],
     "calendars": [{"id": ..., "display": ..., "accessRole": ...}, ...]}

The names arrive wrapped. Text fetched from Google is fenced in
`<<<EXTERNAL_UNTRUSTED_CONTENT ...>>>` markers, so `summary` is a five-line
block with the calendar's actual name on the inside. unwrap_external()
(ld-shared) takes it off, because the alternative is the model lifting the
name out by eye -- and a listing where some rows are fenced and some are not
is exactly the shape an eye normalises inconsistently. The authenticated
account is the one latch tagged the rows with, never calendar ownership or a
primary calendar ID.

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

import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"))

from external_content import unwrap_external  # noqa: E402
from gather_result import GatherError  # noqa: E402


def normalize(entries, *, account=None):
    """{account, candidates, calendars} from one account's calendar rows."""
    if not isinstance(entries, list):
        raise GatherError("calendar listing is not a list")
    calendars, owned_by = [], set()
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
        # Only owner-role rows vote: a calendar someone shared in carries THEIR
        # address in dataOwner, and counting it would make a stranger's account
        # an identity in this owner's config. The authenticated address is one
        # identity; an owner with several addresses owns calendars under each,
        # and the nudge needs all of them to recognise them in a meeting.
        if role == "owner":
            owner_of = entry.get("dataOwner")
            if isinstance(owner_of, str) and owner_of.strip():
                owned_by.add(owner_of)
        calendars.append({"id": cid, "display": str(display), "accessRole": role})
    if account:
        owned_by.add(account)
    return {"account": account,
            "candidates": sorted(owned_by),
            "calendars": calendars}
