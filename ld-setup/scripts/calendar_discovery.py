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
    CONFIG_FILE, FeedError, _decode_command_response, relay, relay_config,
)

CACHE = Path("/var/lib/hermes/ld/calendar-discovery.json")
MAX_AGE_SECONDS = 3900
READY_INTERVAL = 3600
ARGV = ["plow-gog", "calendar", "calendars", "--json", "--results-only"]


class NeedsAccount(Exception):
    """An account refusal needs intervention, not another timer attempt."""


def _load(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _command(credentials, argv):
    payload = relay(*credentials, "plow_run_command", {"argv": argv})
    error = payload.get("error", "")
    if isinstance(error, str) and error.startswith((
        "this command runs on one account: pass --account <email>",
        "that --account is not a connected account.",
    )):
        raise NeedsAccount
    return payload


def _discover(credentials):
    # The accounts verb is structured data, not a subprocess stdout envelope.
    payload = _command(credentials, ["plow-gog", "accounts"])
    accounts = payload.get("accounts")
    if (payload.get("status") != "completed" or not isinstance(accounts, list)
            or not accounts or payload.get("degraded") != []):
        raise FeedError("accounts unavailable")
    groups, seen = [], set()
    for entry in accounts:
        account = entry.get("account") if isinstance(entry, dict) else None
        if not isinstance(account, str) or not account.strip() or account in seen:
            raise FeedError("invalid account listing")
        seen.add(account)
        output = _decode_command_response(_command(
            credentials, [*ARGV, "--account", account]))
        groups.append({**normalize(extract_array(output), account=account),
                       "is_default": entry.get("is_default") is True})
    return {"status": "ready", "accounts": groups}


def refresh(path=CACHE, *, now=None):
    """Refresh only when due; persist scheduling across process restarts."""
    now = time.time() if now is None else now
    calendar = _load(CONFIG_FILE).get("calendar", {})
    if isinstance(calendar, dict) and "sources" in calendar:
        return
    previous = _load(path)
    if previous.get("status") == "needs_account":
        return
    retry_at = previous.get("retry_at", 0)
    if isinstance(retry_at, (int, float)) and now < retry_at:
        return
    credentials, _ = relay_config()
    try:
        if not credentials:
            raise FeedError("relay unavailable")
        snapshot = _discover(credentials)
        snapshot["retry_at"] = now + READY_INTERVAL
    except NeedsAccount:
        snapshot = {"status": "needs_account",
                    "reason": "Choose a connected Google account before retrying discovery."}
    except (FeedError, GatherError, OSError, ValueError):
        attempts = previous.get("attempts", 0)
        attempts = min(attempts, 4) + 1 if isinstance(attempts, int) and attempts >= 0 else 1
        snapshot = {"status": "pending", "attempts": attempts,
                    "reason": "Calendar discovery is temporarily unavailable.",
                    "retry_at": now + min(300 * 2 ** (attempts - 1), 3600)}
    snapshot["checked_at"] = now
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
    """Stopped states remain visible; expired choices are not proof of disconnection."""
    try:
        snapshot = _load(path)
        if snapshot.get("status") == "needs_account":
            return snapshot
        age = (time.time() if now is None else now) - snapshot["checked_at"]
        if snapshot.get("status") == "ready" and not isinstance(snapshot.get("accounts"), list):
            return {"status": "pending"}
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
