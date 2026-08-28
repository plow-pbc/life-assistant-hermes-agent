#!/usr/bin/env python3
"""send_nudge_chat.py — message the owner the nudge text over Plow Chat.

The nudge's second surface. No arguments: the text comes from this leg's
OWN fixed handoff — written by nudge_candidates.py (every qualifying
reminder), never by the model — the endpoint and chat from
PLOW_CHAT_BASE_URL / PLOW_CHAT_CHAT_UID, and the bearer from
PLOW_CHAT_TOKEN — env first, then /opt/data/.env, the file activation
writes and the gateway loads (the same lesson register_crons.py's
resolve_deliver learned in #24: an exec session's env never carries the
per-instance PLOW_CHAT_* values). The bearer never reaches argv, and a
prompt-injected turn has no argument to steer.

Wire shape is the plow-chat-platform plugin's own send path:
POST {base}/v1/chats/{uid}/messages with {"body": <text>}. Non-2xx exits 1
loudly — the kiosk post already succeeded by the time this runs, so a quiet
failure here would look delivered while the owner's phone stays silent.

Each leg consumes its own handoff on success: post_nudge.py consumes the
kiosk file the ordinary way, and a successful send here unlinks this one (a
failure leaves it for a retry, the same semantics the kiosk poster has on
its own error exits).

Why not the cron's --deliver arm (the digest's mechanism): --deliver relays
EVERY final response, and this producer runs half-hourly with quiet no-op
runs — the script leg keeps quiet runs silent by construction.
"""
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)
import post_to_kiosk  # noqa: E402

HANDOFF = "/opt/data/ld/calendar-nudge-chat"
DOTENV = "/opt/data/.env"

# The shared redirect/bearer-leak guard, not a copy: a followed 3xx forwards
# the Authorization header to the new origin (post_to_kiosk's docstring owns
# the threat model), and a security helper duplicated across two files is one
# a future fix silently misses.
_no_redirect_opener = post_to_kiosk._no_redirect_opener


def require(name, dotenv):
    value = (os.environ.get(name) or dotenv.get(name) or "").strip()
    if not value:
        raise SystemExit(
            f"{name} is unset or blank in the env and {DOTENV} -- activation "
            "writes it there; without it the owner cannot be messaged"
        )
    return value


def main():
    # One accepted spelling (NAME=value), same as register_crons.dotenv_values:
    # the file is machine-written by activation, and a second spelling is a
    # second thing that can drift.
    try:
        lines = pathlib.Path(DOTENV).read_text().splitlines()
    except FileNotFoundError:
        lines = []
    dotenv = {
        name: value
        for name, _, value in (line.partition("=") for line in lines)
        if name.isidentifier()
    }

    text = pathlib.Path(HANDOFF).read_text().strip()
    if not text:
        raise SystemExit(f"{HANDOFF} is empty -- nothing to send")

    base = require("PLOW_CHAT_BASE_URL", dotenv).rstrip("/")
    uid = require("PLOW_CHAT_CHAT_UID", dotenv)
    token = require("PLOW_CHAT_TOKEN", dotenv)

    request = urllib.request.Request(
        f"{base}/v1/chats/{uid}/messages",
        data=json.dumps({"body": text}).encode("utf-8"),
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _no_redirect_opener().open(request, timeout=30) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Plow Chat send failed: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"Plow Chat send failed: {e.reason}") from e
    if not 200 <= status < 300:
        raise SystemExit(f"Plow Chat send failed: HTTP {status}")
    # Consume this leg's one-shot handoff so it cannot be resent stale.
    os.unlink(HANDOFF)
    print(f"sent nudge to chat ({len(text)} chars)")


if __name__ == "__main__":
    main()
