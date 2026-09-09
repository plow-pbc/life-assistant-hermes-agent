#!/usr/bin/env python3
"""Refresh calendar choices in the background; serve a local snapshot to chat.

The reader never contacts the relay or waits for a refresh. A snapshot is
replaced atomically so a queued owner reply cannot observe a partial listing.
Display names remain untrusted data, even after normalization.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from calendar_list import GatherError, extract_array, normalize  # noqa: E402
from calendar_feed import (  # noqa: E402
    FeedError, _decode_command_response, relay, relay_config,
)

CACHE = Path("/var/lib/hermes/ld/calendar-discovery.json")
MAX_AGE_SECONDS = 900
ARGV = ["gog", "calendar", "calendars", "--json", "--results-only"]


def refresh(path=CACHE, *, now=None):
    """Publish only normalized choices, never raw command output or errors."""
    snapshot = {"status": "pending"}
    credentials, _ = relay_config()
    if credentials:
        try:
            output = _decode_command_response(relay(
                *credentials, "plow_run_command", {"argv": ARGV}))
            snapshot = {"status": "ready", **normalize(extract_array(output))}
        except (FeedError, GatherError, OSError, ValueError):
            pass
    snapshot["checked_at"] = time.time() if now is None else now
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent,
                                         prefix=".calendar-discovery-",
                                         delete=False) as staged:
            name = staged.name
            json.dump(snapshot, staged)
        os.replace(name, path)
    finally:
        if name is not None:
            Path(name).unlink(missing_ok=True)


def read_snapshot(path=CACHE, *, now=None):
    """Anything short of usable choices is pending, not proof of disconnection."""
    try:
        snapshot = json.loads(Path(path).read_text())
        age = (time.time() if now is None else now) - snapshot["checked_at"]
        if 0 <= age <= MAX_AGE_SECONDS and snapshot["status"] in ("ready", "pending"):
            return snapshot
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {"status": "pending"}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--refresh"]:
        try:
            refresh()
        except OSError:
            print("calendar discovery: cache unavailable", file=sys.stderr)
        return 0
    if argv:
        raise SystemExit("usage: calendar_discovery.py [--refresh]")
    json.dump(read_snapshot(), sys.stdout)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
