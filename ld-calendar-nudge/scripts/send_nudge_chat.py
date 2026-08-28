#!/usr/bin/env python3
"""send_nudge_chat.py — message the owner the nudge text over Plow Chat.

The nudge's second surface. No arguments: the text comes from the fixed
handoff file, the endpoint and chat from PLOW_CHAT_BASE_URL /
PLOW_CHAT_CHAT_UID, and the bearer from PLOW_CHAT_TOKEN — env first, then
/opt/data/.env, the file activation writes and the gateway loads (the same
lesson register_crons.py's resolve_deliver learned in #24: an exec session's
env never carries the per-instance PLOW_CHAT_* values). The bearer never
reaches argv, and a prompt-injected turn has no argument to steer.

Wire shape is the plow-chat-platform plugin's own send path:
POST {base}/v1/chats/{uid}/messages with {"body": <text>}. Non-2xx exits 1
loudly — the kiosk post already succeeded by the time this runs, so a quiet
failure here would look delivered while the owner's phone stays silent.

Consume-on-success lives HERE, on the LAST leg: post_nudge.py (kiosk, runs
first) sets post_to_kiosk.CONSUME = False so the same handoff survives to
feed this send; a successful send then unlinks it (a failure leaves it for a
retry, the same semantics the kiosk poster has on its own error exits).

Why not the cron's --deliver arm (the digest's mechanism): --deliver relays
EVERY final response, and this producer runs half-hourly with quiet no-op
runs — the script leg keeps quiet runs silent by construction.
"""
import json
import os
import pathlib
import urllib.error
import urllib.request

HANDOFF = "/opt/data/ld/calendar-nudge-text"
DOTENV = "/opt/data/.env"


def _no_redirect_opener():
    """urllib opener that refuses 3xx redirects — same threat post_to_kiosk
    guards: default urllib follows redirects AND forwards the Authorization
    header to the new origin, steering the bearer to wherever a rewritten
    endpoint or compromised host points."""

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    return urllib.request.build_opener(_NoRedirect)


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
    # Last leg: consume the one-shot handoff so it cannot be reposted stale.
    os.unlink(HANDOFF)
    print(f"sent nudge to chat ({len(text)} chars)")


if __name__ == "__main__":
    main()
