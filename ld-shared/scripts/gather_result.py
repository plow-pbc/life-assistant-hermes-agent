"""gather_result.py — consume a gather file: latch's answer, redirected.

The deterministic filters (ld-morning-triage's triage_candidates.py,
ld-calendar-nudge's nudge_candidates.py) read a file the model wrote by
running one latch command with its stdout redirected there. The semantics
are kept here rather than as copies:

- Consume-FIRST: the gather is read and deleted before anything else (a
  broken config, a bad answer) gets a chance to abort the run — the raw
  corpus must not outlive the run whatever the outcome.
- latch prints exactly one JSON object on stdout when a call worked, and
  nothing there when it did not (its error goes to stderr, which the
  redirect leaves on the terminal). So an empty gather is a failed call,
  never a quiet one.
- Loud failure: an empty or malformed gather raises GatherError with a
  stderr-ready message — the caller prints it and exits 2, because a failed
  gather read as a quiet day is the trap both filters exist to avoid.
"""
from __future__ import annotations

import json
import os


class GatherError(Exception):
    """A failed or malformed gather; str() is the stderr-ready message."""


def read_answer(path):
    """Consume (read + unlink) the gather file; latch's answer, as a dict."""
    with open(path) as f:
        raw = f.read().strip()
    os.unlink(path)
    if not raw:
        raise GatherError("the gather is empty: the latch call failed "
                          "(its error is on the terminal, not in the file)")
    try:
        answer = json.loads(raw)
    except ValueError as e:
        raise GatherError(f"malformed gather: {e}") from e
    if not isinstance(answer, dict):
        raise GatherError("malformed gather: latch answers with one object")
    return answer


def read_fanout(path):
    """Consume a Google read that named no --account: (items, failed).

    latch runs gog once per connected account and answers with every
    account's rows, each tagged with its `account`, plus one outcome per
    account: {"items": [...], "accounts": [{"account", "status", "reason"?}]}.
    `failed` is every outcome that is neither `ok` nor `empty`; what one
    costs is the caller's call.
    """
    answer = read_answer(path)
    items, accounts = answer.get("items"), answer.get("accounts")
    if not isinstance(items, list) or not isinstance(accounts, list):
        raise GatherError("malformed fan-out gather: items and accounts must be lists")
    try:
        failed = [a for a in accounts if a["status"] not in ("ok", "empty")]
    except (KeyError, TypeError) as e:
        raise GatherError(f"malformed fan-out gather: {e!r}") from e
    return items, failed
