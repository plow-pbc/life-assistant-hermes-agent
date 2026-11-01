#!/usr/bin/env python3
"""The owner's machines, for scripts that run without a chat, through latch.

The image ships latch and boot keeps it current; the model calls it itself.
The calendar discovery and the calendar feed run unattended, so they call it
through this, and act for the owner's home chat: every call that latch judges
names it with `--plow-chat "$PLOW_HOME_CHANNEL"`.

Hosts are chosen per run from one `latch status`, never from configuration:

  - Google (calendar, mail): the first online host with `google` on. `cloud`
    has it, so these work with the owner's Mac asleep.
  - The Mac: the first online host with `messages` on. `~/Plow/ld/`, where
    the wall's bearer file and the bodies shipped to it live, is on the Mac,
    which is also the host on the household's network.

latch prints one JSON object on stdout when a call worked, and one
`{"error": {...}}` line on stderr when it did not. LatchError carries that
error's `code`, so callers branch on latch's own codes.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

# latch exec's own ceiling, and every call's: a host gone quiet mid-call is a
# LatchError, never an unattended run that hangs.
TIMEOUT_S = 600


class LatchError(RuntimeError):
    """latch did not answer with what was asked for. `code` is latch's own
    error code when it gave one (`host_offline`, `google_needs_sign_in`, ...)."""

    def __init__(self, message: str, code: str | None = None,
                 details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def latch(*args: str) -> dict:
    """One latch call's JSON, or LatchError carrying latch's error code."""
    try:
        done = subprocess.run(["latch", *args], capture_output=True, text=True,
                              timeout=TIMEOUT_S + 30)
    except subprocess.TimeoutExpired:
        raise LatchError(f"latch {args[0]}: no answer in {TIMEOUT_S + 30}s") from None
    except OSError:
        raise LatchError("latch is not installed", "not_installed") from None
    if done.returncode == 0:
        try:
            return json.loads(done.stdout)
        except ValueError:
            raise LatchError(f"latch {args[0]}: answer is not JSON") from None
    try:
        error = json.loads(done.stderr.strip().splitlines()[-1])["error"]
        code = error["code"]
    except (IndexError, KeyError, TypeError, ValueError):
        raise LatchError(f"latch {args[0]}: exited {done.returncode}") from None
    # The code, never the message: a message can carry an account address or
    # the argv back, and these runs print what they raise.
    # latch puts a code's details beside it, e.g. `accounts` on a sign-in error.
    raise LatchError(f"latch {args[0]}: {code}", code, error)


def chat() -> list[str]:
    """`--plow-chat` for the owner's home chat, which every unattended call acts for."""
    home = (os.environ.get("PLOW_HOME_CHANNEL") or "").strip()
    if not home:
        raise LatchError("PLOW_HOME_CHANNEL is not set", "no_home_chat")
    return ["--plow-chat", home]


def online_hosts(status: dict) -> list[dict]:
    hosts = status.get("hosts")
    return [h for h in hosts if isinstance(h, dict) and h.get("online") is True
            and isinstance(h.get("name"), str)] if isinstance(hosts, list) else []


def host_with(plugin: str, status: dict | None = None) -> str:
    """The first online host with PLUGIN on, or LatchError `no_host`."""
    status = latch("status") if status is None else status
    for host in online_hosts(status):
        plugins = host.get("plugins") or []
        if any(isinstance(p, dict) and p.get("name") == plugin
               and p.get("state") == "enabled" for p in plugins):
            return host["name"]
    raise LatchError(f"no online host has {plugin} on", "no_host")


def mac(status: dict | None = None) -> str:
    """The owner's Mac: the host `~/Plow/ld/` lives on."""
    return host_with("messages", status)


def google(*gog_args: str, host: str) -> dict:
    return latch("google", "--host", host, *chat(), "--", *gog_args)


def put(host: str, path: str, content: str) -> None:
    """Write CONTENT to PATH on HOST (`~` expanded there)."""
    with tempfile.TemporaryDirectory() as work:
        local = Path(work) / Path(path).name
        local.write_text(content)
        latch("file", "put", "--host", host, *chat(), str(local), path)


def run(host: str, argv: list[str], *, network: bool = False) -> str:
    """ARGV's stdout on HOST; LatchError unless it exited 0."""
    result = latch("exec", "--host", host, *chat(),
                   *(["--network"] if network else []),
                   "--timeout", str(TIMEOUT_S), "--", *argv)
    if result.get("exit_code") != 0:
        raise LatchError(f"{argv[0]} on {host} exited {result.get('exit_code')}",
                         "command_failed")
    return result.get("stdout", "")
