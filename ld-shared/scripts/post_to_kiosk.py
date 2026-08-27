#!/usr/bin/env python3
"""post_to_kiosk.py — shared POST helper for every ld- producer, on any platform.

This is the ONE canonical copy. It lives in `plow-pbc/life-dashboard-skills`
and is pulled into each life-dashboard seed's bundle set at install time (as
`ld-shared`), so a fix here reaches the Plow agent seed AND the Hermes agent
seed without being hand-applied twice.

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
    - MESSAGE_FILE set (containers that CAN write a /tmp handoff, e.g. Hermes):
      read that fixed path, then consume it after a successful send so a later
      run can't repost stale text.
    - MESSAGE_FILE None (read-only agent sandboxes, e.g. Plow — its file tool
      cannot create a /tmp handoff): read stdin, fed by the caller's quoted
      heredoc, so an injected body is inert data, never parsed as shell.

  endpoint URL + bearer token — file-first, env fallback:
    - /config/secrets/dashboard-{endpoint-url,token} files when present (Plow
      lands these mode-600 on a read-only secrets mount), else
    - DASHBOARD_ENDPOINT_URL / DASHBOARD_TOKEN env vars (Hermes has no per-agent
      secrets mount; it exports these into the container env from data/.env —
      the same mechanism the plow-connectors skill reads its bearer from).
  Both are fixed, non-argv, non-caller-steerable.

The test suite imports this module and rebinds these constants (the secret-file
paths, MESSAGE_FILE) and feeds stdin — a seam reachable only by an importer,
not by the CLI a scheduled agent invokes.

Caller contract — the viewer requires all of card/type/text; `card` picks the
kiosk slot (latest post per card wins). The eyebrow defaults to `type`; set the
optional module var TITLE to "" to hide it or to a string to override it:

    import post_to_kiosk
    post_to_kiosk.MESSAGE_FILE = "/tmp/ld-<bundle>-text"   # Hermes only; Plow leaves None
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
import sys
import urllib.error
import urllib.request
from pathlib import Path

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

# Shared across all producers — file-first, then env fallback (see module docstring).
ENDPOINT_FILE = "/config/secrets/dashboard-endpoint-url"
TOKEN_FILE = "/config/secrets/dashboard-token"
ENDPOINT_ENV = "DASHBOARD_ENDPOINT_URL"
TOKEN_ENV = "DASHBOARD_TOKEN"
# The Pi backend rides the household LAN/tailnet, not the public internet —
# http:// is an accepted trade-off for that trust zone.
REQUIRED_URL_PREFIXES = ("http://", "https://")


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
    """Read a required secret, file-first then env (see module docstring).

    Both sources are fixed and non-argv. The file path is tried first (Plow's
    read-only /config/secrets mount); when absent, the env var is used (Hermes
    populates it from data/.env). Fails loud if neither is populated, so a
    misconfigured install never half-posts to an unknown endpoint.
    """
    if Path(file_path).exists():
        return read_required_file(file_path, label)
    value = os.environ.get(env_name, "").strip()
    if not value:
        sys.exit(f"error: {label} missing — no file at {file_path} and ${env_name} is unset/empty")
    return value


def read_message():
    """Message text from the fixed source the wrapper selected (never argv)."""
    if MESSAGE_FILE:
        return read_required_file(MESSAGE_FILE, f"{BODY_TYPE} text file")
    text = sys.stdin.read().strip()
    if not text:
        sys.exit(f"error: no {BODY_TYPE} text on stdin")
    return text


def _no_redirect_opener():
    """urllib opener that refuses 3xx redirects.

    Default urllib follows redirects AND forwards the Authorization header to
    the new origin — a rewritten endpoint or compromised host could steer the
    bearer to an attacker URL. Refuse the redirect; the HTTPError handler then
    fails loudly.
    """

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    return urllib.request.build_opener(_NoRedirect)


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
    url = read_secret(ENDPOINT_FILE, ENDPOINT_ENV, "endpoint URL")
    if not any(url.startswith(p) for p in REQUIRED_URL_PREFIXES):
        sys.exit(f"error: endpoint URL must start with http:// or https://, got: {url}")
    token = read_secret(TOKEN_FILE, TOKEN_ENV, "token")

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

    req = urllib.request.Request(
        url=url,
        method="POST",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    opener = _no_redirect_opener()
    try:
        # urllib's default HTTPErrorProcessor raises HTTPError on any non-2xx,
        # so reaching this block means success — discard the response body
        # rather than echoing it to stdout. The endpoint may echo submitted
        # text on success, and that text can be derived from private content
        # (e.g. ld-morning-triage's paraphrased mail bodies).
        opener.open(req, timeout=30).close()
    except urllib.error.HTTPError as exc:
        # Don't decode exc.read() — same echoed-text concern as the success path.
        sys.exit(f"error: message API returned HTTP {exc.code} {exc.reason}")
    except urllib.error.URLError as exc:
        sys.exit(f"error: POST to {url} failed: {exc.reason}")

    # Consume the one-shot handoff file so a later run can't repost this text.
    # Only on the success path — left intact on the error exits above so a retry
    # resends it. No-op when the text came from stdin (MESSAGE_FILE None).
    if MESSAGE_FILE:
        os.unlink(MESSAGE_FILE)


if __name__ == "__main__":
    main()
