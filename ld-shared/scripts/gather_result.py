"""gather_result.py — consume a producer's gather file, envelope and all.

The shapes the deterministic filters (ld-morning-triage's
triage_candidates.py, ld-calendar-nudge's nudge_candidates.py) read, kept
here rather than as copies. The semantics are triage's proven ones:

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
- A plow-gog read with no --account is a different payload altogether (see
  read_fanout), so it has its own reader over the same consume-first.
"""
from __future__ import annotations

import json
import os


class GatherError(Exception):
    """A failed or malformed gather; str() is the stderr-ready message."""


def _consume(path):
    with open(path) as f:
        raw = f.read().strip()
    os.unlink(path)
    return raw


def read_gather(path):
    """Consume (read + unlink) the gather file; unwrap a persisted envelope.

    Returns the command's raw output text, stripped. Raises GatherError on a
    nonzero exit_code or an envelope that does not parse.
    """
    raw = _consume(path)
    if raw.startswith("{"):
        try:
            inner = json.loads(json.loads(raw)["result"])
            if inner["exit_code"] != 0:
                raise GatherError(f"gather failed: exit_code={inner['exit_code']}")
            raw = inner["output"].strip()
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
            raise GatherError(f"malformed gather envelope: {e}") from e
    return raw


def read_fanout(path):
    """Consume a plow-gog read that named no --account: (items, degraded).

    Latch runs gog once per connected account and answers with ONE merged
    payload, {status, items, degraded} -- no exit_code and no stdout, so
    read_gather cannot read it (measured on a live fan-out, 2026-09-14). Each
    item carries the `account` Latch read it through, tagged by the Mac after
    the fetch. A partial success completes with the failed accounts named in
    `degraded`; what one costs is the caller's call. The object is found by
    its first brace, wrapped in the runtime's `result` string or bare, so a
    turn that writes an inline answer with a line ahead of it still reads.
    """
    raw = _consume(path)
    try:
        outer, _ = json.JSONDecoder().raw_decode(raw[raw.index("{"):])
        payload = outer.get("result", outer)
        if isinstance(payload, str):
            payload = json.loads(payload)
        status = payload.get("status")
        items, degraded = payload.get("items"), payload.get("degraded")
    except (ValueError, TypeError, AttributeError) as e:
        raise GatherError(f"malformed fan-out gather: {e!r}") from e
    if status != "completed":
        raise GatherError(f"gather did not complete: status={status!r}")
    if not isinstance(items, list) or not isinstance(degraded, list):
        raise GatherError("malformed fan-out gather: items and degraded must be lists")
    return items, degraded
