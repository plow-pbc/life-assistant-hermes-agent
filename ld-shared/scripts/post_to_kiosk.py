#!/usr/bin/env python3
"""post_to_kiosk.py — shared POST helper for every ld- producer, on any platform.

This repo owns `ld-shared` outright — nothing syncs it in or out any more — so
this file is edited here and nowhere else.

Each producer ships a tiny wrapper (`post_message.py`, `post_alert.py`,
`post_digest.py`, `post_nudge.py`, `post_weather.py`, `post_sports.py`) that
sets a couple of module-level constants and calls `main()`. The wrapper is the
only file the cron/agent invokes; this module is never on the agent's
invocation path directly. That keeps the no-CLI-content security model intact:
the body-shaping constants live in the wrapper (fixed strings), not on argv.

Two transports, selected per platform WITHOUT a mode flag — the helper just
reads from whichever fixed source is populated, none of them caller-redirectable
via argv:

  message text
    - MESSAGE_FILE set (containers whose file tool CAN write a handoff, e.g.
      Hermes): read that fixed path. Hermes confines its file tool to
      HERMES_WRITE_SAFE_ROOT (/opt/data), so the path a wrapper picks has to
      sit under it -- see the wrappers' /opt/data/ld/<bundle>-text. That path
      is on the agent's home bind rather than the container-ephemeral /tmp it
      replaced, so a leftover now outlives a restart, and its body sits durably
      in the operator's host home. Trivially fine for weather and sports, which
      post public feed data. ld-morning-triage's body is a <=115-char paraphrase
      of a private iMessage, and its durability was decided deliberately when
      that producer went live: accepted, because a successful send has already
      put the same text on the shared kiosk, and the retry that the error exits
      below exist to allow needs the file to survive. Only a SUCCESSFUL send
      consumes it, so any run that writes a body without one -- a failed send, a
      dry run, an aborted run -- leaves it on disk, and a later run that errors
      before composing posts that body as fresh. Nothing here timestamps it.
      Left this way deliberately: the alternative, consuming on read, costs the
      retry the error exits below exist to allow.
    - MESSAGE_FILE None (read-only agent sandboxes, e.g. Plow — its file tool
      cannot create a handoff at all): read stdin, fed by the caller's quoted
      heredoc, so an injected body is inert data, never parsed as shell.

  endpoint URL + bearer token — three fixed sources, in this order:
    - /config/secrets/dashboard-{endpoint-url,token} files when present (Plow
      lands these mode-600 on a read-only secrets mount), else
    - DASHBOARD_ENDPOINT_URL / DASHBOARD_TOKEN in the process env (Hermes has no
      per-agent secrets mount; the gateway loads /opt/data/.env once at start
      and the container env is that load), else
    - the same two names read straight out of that dotenv (runtime_env.DOTENV).
      This third source is what makes ld-setup work on a live instance:
      mint_wall_token.py APPENDS its lines after `up`, so they are absent from
      the env the gateway loaded and would stay invisible to every cron-spawned
      producer until a restart. Unlike the other two, this dotenv is
      agent-writable at runtime, so a dotenv-sourced endpoint URL is held to
      one exact shape (_validate_dotenv_endpoint) — the Pi's own message API,
      http://<host>:5174/api/message — before the bearer is ever attached to
      a request against it.
  All three are fixed, non-argv, non-caller-steerable; all three empty is a
  loud refusal.

  delivery — DASHBOARD_DELIVERY in the dotenv:
    - `latch`: no POST. The wire body is written to OUTBOX_DIR/card-<n>.json
      (mode 600) and LATCH_BLOCK is printed: the two Latch calls that ship it
      from the owner's Mac, which is on the Pi's LAN when this container is
      not. The token is not read in this mode.
    - anything else: the direct POST above.

The test suite imports this module and rebinds these constants (the secret-file
paths, DOTENV, MESSAGE_FILE) and feeds stdin — a seam reachable only by an importer,
not by the CLI a scheduled agent invokes.

Caller contract — the viewer requires all of card/type/text; `card` picks the
kiosk slot (latest post per card wins). The eyebrow defaults to `type`; set the
optional module var TITLE to "" to hide it or to a string to override it:

    import post_to_kiosk
    post_to_kiosk.MESSAGE_FILE = "/opt/data/ld/<bundle>-text"  # Hermes only; Plow leaves None
    post_to_kiosk.CARD = "1" | "2" | "3" | "4" | "5"
    post_to_kiosk.BODY_TYPE = "alert" | "affirmation" | "weather" | "digest" | "sports"
    post_to_kiosk.main()   # message text on stdin when MESSAGE_FILE is None

`--dry-run` always redacts the body text to `<redacted, N chars>` — producers
paraphrase private mail/iMessage/Slack bodies, so the dry-run output stays
non-sensitive across all producers (operators read MESSAGE_FILE / re-run to see
exact text).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from bearer_http import open_no_redirect
from runtime_env import DOTENV, dotenv_values, household_host

# Bundle-specific — the wrapper sets these before calling main().
CARD: str | None = None
BODY_TYPE: str | None = None
# Optional producer-controlled eyebrow. None → show the card's type as its title
# (default); "" → HIDE the title (reclaim vertical space); a string → override.
TITLE: str | None = None
# When set by the wrapper, the message text is read from this fixed path (and
# consumed after a successful send) instead of stdin. Left None on read-only
# agent sandboxes, which feed the text on stdin.
MESSAGE_FILE: str | None = None

# Shared across all producers — file, then env, then the dotenv (module docstring).
ENDPOINT_FILE = "/config/secrets/dashboard-endpoint-url"
TOKEN_FILE = "/config/secrets/dashboard-token"
ENDPOINT_ENV = "DASHBOARD_ENDPOINT_URL"
TOKEN_ENV = "DASHBOARD_TOKEN"
# The Pi backend rides the household LAN/tailnet, not the public internet —
# http:// is an accepted trade-off for that trust zone.
REQUIRED_URL_PREFIXES = ("http://", "https://")
# The only endpoint shape the agent-writable dotenv may name: the Pi's own
# message API on the household LAN, with the host held to the same safe
# charset mint_wall_token.py enforces -- the URL is interpolated into the
# sh -c the Mac runs in latch mode, so a metacharacter host is an injection,
# not just a wrong address.
DOTENV_ENDPOINT_RE = re.compile(r"http://[A-Za-z0-9.-]+:5174/api/message")

# Latch delivery. When the dotenv says DASHBOARD_DELIVERY=latch the Pi is
# reachable only from the owner's Mac, so the wire body goes to this outbox
# and the agent ships it with two Latch calls (ld-shared/references/
# latch-delivery.md). The token is never read in that mode: the Mac holds it
# in ~/Plow/ld/dashboard.hdr, written once by ld-setup.
DELIVERY_KEY = "DASHBOARD_DELIVERY"
OUTBOX_DIR = "/opt/data/ld/outbox"
# The tool names are qualified because this block is read BY THE MODEL: an MCP
# tool carries its server's key as a prefix, and a bare name is one the build
# does not register -- which sends the turn hunting through tool_search instead
# of calling it. Measured elsewhere at twenty-one API calls and no reply.
LATCH_BLOCK = (
    "NOT DELIVERED — ship it through Latch, then paste both outputs:\n"
    "1. mcp__latch__plow_write_file  path=~/Plow/ld/card-{card}.json  content=<the JSON below>\n"
    "2. mcp__latch__plow_run_command argv=[\"sh\",\"-c\",\"curl -fsS -H @$HOME/Plow/ld/dashboard.hdr "
    "-H 'Content-Type: application/json' --data-binary @$HOME/Plow/ld/card-{card}.json "
    "{url}\"] network=true\n"
    "{json}"
)


def read_required_file(path, label):
    """Read the stripped contents of a fixed file `path` or exit non-zero.

    Used for the single-line secret files (endpoint URL, bearer token) and the
    MESSAGE_FILE handoff; `.strip()` only removes surrounding whitespace, so an
    embedded newline in a multi-line body round-trips.
    """
    try:
        value = Path(path).read_text().strip()
    except OSError as exc:
        sys.exit(f"error: {label} not readable: {path} ({exc.strerror})")
    if not value:
        sys.exit(f"error: {label} is empty: {path}")
    return value


def read_secret(file_path, env_name, label):
    """Read a required secret: file, then env, then the dotenv (module docstring).

    All three sources are fixed and non-argv. The file is tried first (Plow's
    read-only /config/secrets mount); then the process env; then /opt/data/.env
    itself, which is the only source that sees a line ld-setup appended after
    the gateway started. Fails loud when all three are empty, so a
    misconfigured install never half-posts to an unknown endpoint.

    Returns (value, source) — source is "file", "env" or "dotenv". The caller
    uses the source to decide whether extra validation applies: unlike the
    read-only secrets mount or the gateway's startup env, /opt/data/.env is
    agent-writable at runtime (see _validate_dotenv_endpoint).
    """
    if Path(file_path).exists():
        return read_required_file(file_path, label), "file"
    env_value = os.environ.get(env_name, "").strip()
    if env_value:
        return env_value, "env"
    dotenv_value = dotenv_values(DOTENV).get(env_name, "").strip()
    if not dotenv_value:
        sys.exit(
            f"error: {label} missing — no file at {file_path}, ${env_name} is unset/empty, "
            f"and {DOTENV} does not set it"
        )
    return dotenv_value, "dotenv"


def _validate_dotenv_endpoint(url):
    """A dotenv-sourced endpoint URL must be exactly the Pi's message API.
    /opt/data/.env is agent-writable at runtime, so an injected turn
    appending a second DASHBOARD_ENDPOINT_URL= line (last duplicate wins in
    dotenv_values) must not be able to steer the bearer to an attacker host.
    The secrets file and the startup env are trusted as today — this only
    gates the third, writable source.
    """
    if not DOTENV_ENDPOINT_RE.fullmatch(url):
        sys.exit(
            "error: endpoint URL is not http://<host>:5174/api/message with a "
            f"[A-Za-z0-9.-] host — refusing to use {url}"
        )
    # The regex pins the shape; this pins the *reach*. A syntactically clean
    # public hostname (collector.example) would still walk the bearer off the
    # household network via the Mac's curl.
    if not household_host(url[len("http://"):-len(":5174/api/message")]):
        sys.exit(
            f"error: endpoint host in {url} is not on the household network "
            "(a private IP or a .local name) — refusing"
        )


def read_message():
    """Message text from the fixed source the wrapper selected (never argv)."""
    if MESSAGE_FILE:
        return read_required_file(MESSAGE_FILE, f"{BODY_TYPE} text file")
    text = sys.stdin.read().strip()
    if not text:
        sys.exit(f"error: no {BODY_TYPE} text on stdin")
    return text


def post_bearer_json(url, token, body, label):
    """One bearer-token JSON POST, shared by every producer leg.

    Refuses redirects (see _no_redirect_opener), exits loudly on any failure,
    and discards the response body — the endpoint may echo submitted text on
    success, and that text can derive from private content.
    """
    req = urllib.request.Request(
        url=url,
        method="POST",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        open_no_redirect(req, timeout=30).close()
    except urllib.error.HTTPError as exc:
        # Don't decode exc.read() — the same echoed-text concern.
        sys.exit(f"error: {label} returned HTTP {exc.code} {exc.reason}")
    except urllib.error.URLError as exc:
        sys.exit(f"error: POST to {label} failed: {exc.reason}")


def hand_off_to_latch(url, body):
    """Latch delivery: the exact wire body to the outbox, mode 600, plus the
    two Latch calls the agent makes with it. No request leaves here -- the
    Mac, on the Pi's LAN, makes it -- and the agent's run is not done until
    that curl returned 2xx (the reference sheet holds it to that)."""
    os.makedirs(OUTBOX_DIR, mode=0o700, exist_ok=True)
    path = os.path.join(OUTBOX_DIR, f"card-{CARD}.json")
    wire = json.dumps(body)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(wire)
    print(LATCH_BLOCK.format(card=CARD, url=url, json=wire))


def main():
    if not CARD:
        sys.exit("error: post_to_kiosk.CARD not set by caller")
    if not BODY_TYPE:
        sys.exit("error: post_to_kiosk.BODY_TYPE not set by caller")

    parser = argparse.ArgumentParser(
        description=f"Post a {BODY_TYPE!r} message to the life-dashboard kiosk."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the request instead of sending it"
    )
    args = parser.parse_args()

    text = read_message()
    dotenv = dotenv_values(DOTENV)
    latch = dotenv.get(DELIVERY_KEY, "").strip() == "latch"
    url, url_source = read_secret(ENDPOINT_FILE, ENDPOINT_ENV, "endpoint URL")
    if latch and url_source == "env" and dotenv.get(ENDPOINT_ENV, "").strip():
        # ld-setup owns the latch endpoint in the dotenv and re-points it there
        # on a Pi address change; the startup env is that file's boot-time load,
        # so after a re-point the env is the stale copy — the dotenv line wins.
        # Only over env: a secrets-mount file keeps its documented precedence.
        url, url_source = dotenv[ENDPOINT_ENV].strip(), "dotenv"
    if not any(url.startswith(p) for p in REQUIRED_URL_PREFIXES):
        sys.exit(f"error: endpoint URL must start with http:// or https://, got: {url}")
    if url_source == "dotenv":
        _validate_dotenv_endpoint(url)
    # Direct mode reads the token here, BEFORE --dry-run, so a missing token
    # still refuses on a dry run as it always has. Latch mode never needs it.
    token = None if latch else read_secret(TOKEN_FILE, TOKEN_ENV, "token")[0]

    body = {"card": CARD, "type": BODY_TYPE, "text": text}
    if TITLE is not None:
        body["title"] = TITLE

    if args.dry_run:
        # Always redact the body text — producers paraphrase private mail /
        # iMessage / Slack content, and a single redaction policy across all
        # producers avoids a per-producer privacy branch. Don't consume
        # MESSAGE_FILE on a dry run — it's a test, the real run still needs it.
        print(
            json.dumps(
                {
                    "method": "POST",
                    "url": url,
                    "authorization": "Bearer <redacted>",
                    "content_type": "application/json",
                    "body": {**body, "text": f"<redacted, {len(text)} chars>"},
                },
                indent=2,
            )
        )
        return

    if latch:
        hand_off_to_latch(url, body)
    else:
        post_bearer_json(url, token, body, "message API")

    # Consume the one-shot handoff. Success path only — left intact on the error
    # exits above so a retry resends it; the module docstring owns the window
    # where that retry reposts a stale body as fresh, and why it is left open.
    # No-op when the text came from stdin (MESSAGE_FILE None) — which is also
    # how a coordinator that owns its handoff itself posts (post_nudge.py).
    if MESSAGE_FILE:
        os.unlink(MESSAGE_FILE)


if __name__ == "__main__":
    main()
