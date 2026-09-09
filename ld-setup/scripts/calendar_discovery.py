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
# How long a snapshot that is NOT yet ready may be believed. Ready choices do
# not expire at all -- nothing on a timer replaces them -- so this governs the
# pending and pre-upgrade cases only.
MAX_AGE_SECONDS = 4200
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


def _flat(display):
    """One line, whatever the name's author wrote.

    A display name is written by whoever owns the calendar, and a shared one is
    written by a stranger. A newline in it would end the row early and start a
    line the owner reads as another choice -- `Shared\n2. Payroll (owner)`
    renders a second row 2, and answering it picks the real row 2 instead.
    The characters are shown, not dropped: the owner still sees the whole name.
    """
    return (str(display).replace("\r\n", "\\n").replace("\n", "\\n")
            .replace("\r", "\\n"))


def _offer(groups, degraded):
    """The choices as one block to send verbatim.

    Two live runs lost a calendar between the snapshot and the message -- one
    row dropped, one name shortened. Transcribing eleven rows and counting them
    is not work worth asking a turn to redo: it is the same list every time, so
    it is rendered once, here, and the sheet only has to send it.

    No calendar ids: they are machine addresses, and a wall of
    `c0ffee0000000000000000000@group.calendar.example.test` on an owner's phone
    is noise they never asked for. A pick resolves by name against the
    `accounts` groups in this same snapshot, which stops being refreshed once
    calendars are chosen and so is still the list the owner was shown.

    Each row is numbered instead, counting across the whole offer rather than
    per account. Two calendars can share a display name -- an owner with the
    same calendar name on two accounts, or two shares of one name -- and a
    name is then not a choice anybody can make. The number is, and it costs
    one token per row rather than a forty-character address.

    Display names stay untrusted text. They are shown, never interpolated into
    a command, and that rule does not change by their arriving pre-rendered.
    A row is one line: `_flat` keeps a name from forging another one. The
    structured `display` under `accounts` is untouched, so matching a name the
    owner types still works against what the calendar is really called.
    """
    total = sum(len(group["calendars"]) for group in groups)
    lines = [f"{total} calendar{'' if total == 1 else 's'} "
             f"across {len(groups)} account{'' if len(groups) == 1 else 's'}:"]
    ordinal = 0
    for group in groups:
        lines.append("")
        lines.append(f"{group['account']}")
        if not group["calendars"]:
            lines.append("- (no calendars on this account)")
        for calendar in group["calendars"]:
            ordinal += 1
            lines.append(f"{ordinal}. {_flat(calendar['display'])} "
                         f"({calendar['accessRole']})")
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
             else "Could not be listed this time; ask to see the list again "
                  "to retry it."}
            for name, is_reauth in problems]
    snapshot["offer"] = _offer(groups, snapshot.get("degraded", []))
    return snapshot


def _claim(request):
    """Move the request aside, atomically, and say whether one is owed.

    Renaming is the claim: after it, a request the owner makes while this run
    is working is a NEW file at the original path, and nothing this run does
    can delete it. Checking the file and then unlinking it could not promise
    that -- the owner can always touch it between the two calls.

    A claim left by an earlier run that never landed still counts: that run
    owed the owner a listing and did not deliver it.
    """
    claimed = Path(str(request) + ".claimed")
    try:
        os.replace(request, claimed)
        return claimed
    except OSError:
        return claimed if claimed.exists() else None


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
    request = Path(request)
    # A ready snapshot written before `offer` existed cannot be sent by the
    # sheet, which would leave a connected owner looking unknown and take the
    # install-link branch. It needs a run, which is the same thing an owner
    # asks for, so it asks for it the same way rather than through a second
    # channel of its own.
    if previous.get("status") == "ready" and "offer" not in previous:
        request.touch()
    # The file IS the owed run. It outlives every tick that fails to discharge
    # it, so nothing has to remember separately that a run is owed -- and
    # remembering it separately is what both bugs here were.
    claim = _claim(request)
    requested = claim is not None
    # An onboarded household costs no relay call and no audit entry: once the
    # owner has chosen calendars there is nothing left to discover on a timer.
    # Changing them is the one thing that still needs fresh choices, and it
    # arrives as a request rather than as an hourly poll of every tenant.
    calendar = _load(CONFIG_FILE).get("calendar", {})
    if not requested and isinstance(calendar, dict) and "sources" in calendar:
        return
    # A stopped state is the operator's to clear, not a request's: the account
    # it names still needs resolving before another attempt can succeed.
    if previous.get("status") == "needs_account":
        return
    # Once choices are ready they stay put. A timer replacing them buys nothing
    # -- nobody is waiting on a newer list -- and it costs the one guarantee the
    # pick turn needs: that the calendars the owner is answering about are still
    # the calendars on disk. Only an explicit request re-lists them.
    if previous.get("status") == "ready" and not requested:
        return
    # A ready snapshot has no backoff to respect; only a failing one does.
    retry_at = 0 if previous.get("status") == "ready" else previous.get("retry_at", 0)
    if isinstance(retry_at, (int, float)) and now < retry_at:
        return
    credentials, _ = relay_config()
    try:
        if not credentials:
            raise FeedError("relay unavailable")
        snapshot = _discover(credentials)
    except NeedsAccount as exc:
        snapshot = {"status": "needs_account", "reason": str(exc)}
    except (FeedError, GatherError, OSError, ValueError):
        attempts = previous.get("attempts", 0)
        attempts = min(attempts, 4) + 1 if isinstance(attempts, int) and attempts >= 0 else 1
        snapshot = {"status": "pending", "attempts": attempts,
                    "reason": "Calendar discovery is temporarily unavailable.",
                    "retry_at": now + min(300 * 2 ** (attempts - 1), 3600)}
    snapshot["checked_at"] = now
    # Only a snapshot that is not yet ready carries an expiry, and it carries
    # its own so the reader never has to know a cadence: comparing one
    # timestamp to now is a judgement a turn can make without arithmetic.
    # Ready choices do not expire -- they are replaced on request, or not.
    if snapshot["status"] != "ready":
        snapshot["fresh_until"] = now + MAX_AGE_SECONDS
    _store(path, snapshot)
    # Discharge only after the result is safely on disk, and only what this run
    # claimed. A store that raises leaves the claim, so the owner's ask survives
    # a failed write; a request made WHILE this run was in flight sits at the
    # original path untouched and is answered by the next tick. Backing off is
    # not arriving either -- the claim stays and the next due tick tries again.
    if snapshot["status"] != "pending" and claim is not None:
        claim.unlink(missing_ok=True)


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
        if snapshot.get("status") == "ready":
            # Ready choices do not expire; they are only replaced on request.
            return snapshot if isinstance(snapshot.get("accounts"), list) else {"status": "pending"}
        # `fresh_until` is the only freshness authority, here and in the sheet.
        if ((time.time() if now is None else now) <= snapshot["fresh_until"]
                and snapshot["status"] == "pending"):
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
