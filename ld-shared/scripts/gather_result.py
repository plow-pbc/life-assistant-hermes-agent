"""gather_result.py — consume a producer's gather file, envelope and all.

The one shape both deterministic filters (ld-morning-triage's
triage_candidates.py, ld-calendar-nudge's nudge_candidates.py) share, moved
here rather than kept as two copies. The semantics are triage's proven ones:

- Consume-FIRST: the gather is read and deleted before anything else (a
  broken config, a bad envelope) gets a chance to abort the run — the raw
  corpus must not outlive the run whatever the outcome.
- An oversized plow_run_command result reaches the model as a persisted
  envelope — {"result": "<json of {exit_code, handle, output}>"} — not as
  raw command stdout. The sniff unwraps it; a command's own output opens
  with an array (or is empty), never an object, so the sniff cannot misfire
  on real rows.
- Loud failure: a nonzero envelope exit_code or a malformed envelope raises
  GatherError with a stderr-ready message — the caller prints it and exits
  2, because a failed gather read as a quiet day is the trap both filters
  exist to avoid.
"""
from __future__ import annotations

import json
import os


class GatherError(Exception):
    """A failed or malformed gather; str() is the stderr-ready message."""


def read_gather(path):
    """Consume (read + unlink) the gather file; unwrap a persisted envelope.

    Returns the command's raw output text, stripped. Raises GatherError on a
    nonzero exit_code or an envelope that does not parse.
    """
    with open(path) as f:
        raw = f.read().strip()
    os.unlink(path)

    if raw.startswith("{"):
        try:
            inner = json.loads(json.loads(raw)["result"])
            if inner["exit_code"] != 0:
                raise GatherError(f"gather failed: exit_code={inner['exit_code']}")
            raw = inner["output"].strip()
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
            raise GatherError(f"malformed gather envelope: {e}") from e
    return raw
