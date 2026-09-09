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
# The sheet's only way to ask for choices out of band. An empty file is the
# whole protocol: the service consumes it on its next tick, so nothing here
# parses owner-writable content.
REQUEST = Path("/var/lib/hermes/ld/calendar-discovery.request")
# A ready snapshot must outlive its own refresh cycle: READY_INTERVAL plus the
# service's 300s tick, twice, so a refresh landing a tick late never makes a
# healthy tenant read `pending`. 3900 left exactly zero margin.
MAX_AGE_SECONDS = 4200
READY_INTERVAL = 3600
ARGV = ["plow-gog", "calendar", "calendars", "--json", "--results-only"]
REFUSAL_REASON = "Choose a connected Google account before retrying discovery."


def _reconnect(names):
    """Reconnect wording, never a relay error string: those can carry addresses
    the owner did not ask us to repeat back."""
    return ("Reconnect " + ", ".join(names)
            + " in Latch; discovery will not retry that account until then.")


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
        # deviceAgent.ts's two sibling refusals: the multi-account form of the
        # line above, and a named account Plow can no longer mint for. Both are
        # the owner's to resolve, so neither is worth another timer attempt.
        "an --account entry is not a connected account.",
        "that account cannot be used right now:",
    )):
        raise NeedsAccount(REFUSAL_REASON)
    return payload


def _offer(groups, degraded):
    """The choices as one block to send verbatim.

    Two live runs lost a calendar between the snapshot and the message -- one
    row dropped, one name shortened. Transcribing eleven rows and counting them
    is not work worth asking a turn to redo: it is the same list every time, so
    it is rendered once, here, and the sheet only has to send it.

    Display names stay untrusted text. They are shown, never interpolated into
    a command, and that rule does not change by their arriving pre-rendered.
    """
    total = sum(len(group["calendars"]) for group in groups)
    lines = [f"{total} calendar{'' if total == 1 else 's'} "
             f"across {len(groups)} account{'' if len(groups) == 1 else 's'}:"]
    for group in groups:
        lines.append("")
        lines.append(f"{group['account']}")
        if not group["calendars"]:
            lines.append("- (no calendars on this account)")
        for calendar in group["calendars"]:
            lines.append(f"- {calendar['display']} ({calendar['accessRole']}) "
                         f"[{calendar['id']}]")
    for entry in degraded:
        lines.append("")
        lines.append(f"{entry['account']} -- {entry['reason']}")
    return "\n".join(lines)


def _discover(credentials):
    # The accounts verb is structured data, not a subprocess stdout envelope.
    payload = _command(credentials, ["plow-gog", "accounts"])
    accounts = payload.get("accounts")
    degraded = payload.get("degraded")
    if (payload.get("status") != "completed" or not isinstance(accounts, list)
            or not isinstance(degraded, list) or not (accounts or degraded)):
        raise FeedError("accounts unavailable")
    problems = [(entry.get("account"), entry.get("reason") == "needs_reauth")
                for entry in degraded if isinstance(entry, dict)]
    groups, seen = [], set()
    for entry in accounts:
        account = entry.get("account") if isinstance(entry, dict) else None
        if not isinstance(account, str) or not account.strip() or account in seen:
            # The accounts response itself is malformed, which is not one
            # account's problem to be demoted into: nothing here is trustworthy.
            raise FeedError("invalid account listing")
        seen.add(account)
        # One account's listing failing is that account's problem. Healthy
        # groups already gathered are not thrown away for it -- the owner gets
        # the calendars that answered, and the reason the rest did not.
        try:
            output = _decode_command_response(_command(
                credentials, [*ARGV, "--account", account]))
            calendars = normalize(extract_array(output), account=account)
        except NeedsAccount:
            problems.append((account, True))
            continue
        except (FeedError, GatherError, ValueError):
            problems.append((account, False))
            continue
        # No `is_default`: nothing consumed it, and a group flagged as the
        # default invited the sheet to offer that account's calendars alone.
        groups.append(calendars)
    if not groups:
        # Nothing to offer: a revoked token is the owner's to fix and stops
        # discovery, while anything else can come back on its own.
        reauth = sorted({name for name, is_reauth in problems
                         if is_reauth and isinstance(name, str)})
        if reauth and len(reauth) == len(problems):
            raise NeedsAccount(_reconnect(reauth))
        raise FeedError("no usable accounts")
    snapshot = {"status": "ready", "accounts": groups}
    if problems:
        snapshot["degraded"] = [
            {"account": name,
             "reason": _reconnect([name]) if is_reauth and isinstance(name, str)
             else "Temporarily unavailable; discovery keeps trying."}
            for name, is_reauth in problems]
    snapshot["offer"] = _offer(groups, snapshot.get("degraded", []))
    return snapshot


def _take_request(request):
    """True once per request file, which is consumed before anything runs.

    Consuming first means a refresh that dies partway cannot leave the file
    behind for the next tick to retry forever.
    """
    try:
        Path(request).unlink()
        return True
    except OSError:
        return False


def _store(path, snapshot):
    """Replace the snapshot atomically; a queued reader never sees a partial."""
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


def refresh(path=CACHE, *, now=None, request=REQUEST):
    """Refresh only when due; persist scheduling across process restarts."""
    now = time.time() if now is None else now
    previous = _load(path)
    # A request outlives the tick that took it. One transient failure on a
    # requested run would otherwise strand the owner: the retry is due, but the
    # selection gate below sends the tick home before it can happen, and the
    # request file is already spent. So the run stays owed until it succeeds or
    # stops, and only then is the selection gate allowed to close again.
    requested = _take_request(request) or previous.get("requested") is True
    # An onboarded household costs no relay call and no audit entry: once the
    # owner has chosen calendars there is nothing left to discover on a timer.
    # Changing them is the one thing that still needs fresh choices, and it
    # arrives as a request rather than as an hourly poll of every tenant.
    # A snapshot written before `fresh_until` existed cannot be judged by the
    # reader, which would call a connected owner unknown and offer them the
    # install link. One refresh replaces it, so treat it as due.
    legacy = bool(previous) and "fresh_until" not in previous
    calendar = _load(CONFIG_FILE).get("calendar", {})
    if not (requested or legacy) and isinstance(calendar, dict) and "sources" in calendar:
        return
    # A stopped state is the operator's to clear, not a request's: the account
    # it names still needs resolving before another attempt can succeed.
    if previous.get("status") == "needs_account":
        return
    retry_at = 0 if legacy else previous.get("retry_at", 0)
    if isinstance(retry_at, (int, float)) and now < retry_at:
        # The request file was spent by this tick. If the backoff sends us home
        # before anything ran, the ask has to be written down first, or stored
        # sources close the gate on every later tick and the owner waits for a
        # run nobody remembers was asked for.
        if requested and previous.get("requested") is not True:
            _store(path, {**previous, "requested": True})
        return
    credentials, _ = relay_config()
    try:
        if not credentials:
            raise FeedError("relay unavailable")
        snapshot = _discover(credentials)
        snapshot["retry_at"] = now + READY_INTERVAL
    except NeedsAccount as exc:
        snapshot = {"status": "needs_account", "reason": str(exc)}
    except (FeedError, GatherError, OSError, ValueError):
        attempts = previous.get("attempts", 0)
        attempts = min(attempts, 4) + 1 if isinstance(attempts, int) and attempts >= 0 else 1
        snapshot = {"status": "pending", "attempts": attempts,
                    "reason": "Calendar discovery is temporarily unavailable.",
                    "retry_at": now + min(300 * 2 ** (attempts - 1), 3600)}
    # An owed request is carried on the snapshot until the run it asked for
    # actually lands; backing off is not landing.
    if requested and snapshot["status"] == "pending":
        snapshot["requested"] = True
    snapshot["checked_at"] = now
    # The snapshot carries its own expiry so the reader never has to know the
    # refresh cadence: chat reads this file directly, and comparing one
    # timestamp to now is a judgement a turn can make without arithmetic.
    snapshot["fresh_until"] = now + MAX_AGE_SECONDS
    _store(path, snapshot)


def read_snapshot(path=CACHE, *, now=None):
    """The operator's read, for a shell. Chat reads the file itself.

    Stopped states remain visible; expired choices are not proof of
    disconnection. This is the same rule §5 of the sheet states in words, and
    `test_the_sheet_and_the_service_agree_on_staleness` holds the two together.
    """
    try:
        snapshot = _load(path)
        if snapshot.get("status") == "needs_account":
            return snapshot
        if snapshot.get("status") == "ready" and not isinstance(snapshot.get("accounts"), list):
            return {"status": "pending"}
        # `fresh_until` is the only freshness authority, here and in the sheet.
        if ((time.time() if now is None else now) <= snapshot["fresh_until"]
                and snapshot["status"] in ("ready", "pending")):
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
    # No argument is an operator at a shell asking what the service thinks.
    # The agent never runs this: it reads the snapshot file with its file tool,
    # which needs no command approval and cannot block a turn.
    json.dump(read_snapshot(), sys.stdout)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
