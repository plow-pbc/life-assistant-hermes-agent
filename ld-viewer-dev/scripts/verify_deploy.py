#!/usr/bin/env python3
"""verify_deploy.py — is the pushed SHA actually live on the kiosk?

Usage: verify_deploy.py <sha> [--timeout 600]

Polls GET <base>/api/version every POLL_INTERVAL seconds, where <base> is
DASHBOARD_ENDPOINT_URL (the producers' POST endpoint, read from the agent's
dotenv) with its `/api/message` suffix stripped — one env var, two surfaces.

  exit 0  the kiosk reports exactly {"sha": <sha>} — the deploy is live.
  exit 1  timeout: the SHA never went live (the Pi's updater built, failed a
          health check, and rolled back — or never picked the push up). The
          last response is printed verbatim so the report can carry it.
  exit 2  DASHBOARD_ENDPOINT_URL missing/malformed, or a blank <sha>.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

ENDPOINT_ENV = "DASHBOARD_ENDPOINT_URL"
MESSAGE_SUFFIX = "/api/message"
# Rebound by the test suite; the updater fires every 2 min, so 5s is plenty.
POLL_INTERVAL = 5.0


def die_malformed(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


def base_url():
    """The kiosk base URL, derived from the one endpoint var this agent has."""
    url = os.environ.get(ENDPOINT_ENV, "").strip()
    if not url:
        die_malformed(f"${ENDPOINT_ENV} is unset/empty — it lives in the agent's dotenv")
    if not url.startswith(("http://", "https://")):
        die_malformed(f"${ENDPOINT_ENV} must start with http:// or https://, got: {url}")
    if not url.endswith(MESSAGE_SUFFIX):
        die_malformed(f"${ENDPOINT_ENV} must end with {MESSAGE_SUFFIX}, got: {url}")
    return url[: -len(MESSAGE_SUFFIX)]


def probe(url):
    """One GET of /api/version → its body text, or the failure as text."""
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code} {exc.reason}"
    except urllib.error.URLError as exc:
        return f"unreachable: {exc.reason}"
    except TimeoutError:
        # A kiosk that accepts the connection but stalls mid-read; treat it
        # like unreachable so the poll loop keeps going until --timeout.
        return "unreachable: timed out mid-response"


def main():
    parser = argparse.ArgumentParser(
        description="Poll the kiosk's /api/version until the given SHA is live."
    )
    parser.add_argument("sha", help="the pushed commit SHA to wait for")
    parser.add_argument("--timeout", type=float, default=600,
                        help="seconds to keep polling (default 600)")
    args = parser.parse_args()
    if not args.sha.strip():
        die_malformed("a blank SHA can never match a live deploy")

    url = base_url() + "/api/version"
    deadline = time.monotonic() + args.timeout
    while True:
        last = probe(url)
        try:
            reported = json.loads(last).get("sha")
        except (json.JSONDecodeError, AttributeError):
            reported = None
        if reported == args.sha:
            print(f"live: {url} reports sha {args.sha}")
            return
        if time.monotonic() >= deadline:
            print(f"timeout: {url} never reported sha {args.sha} — last response: {last}")
            sys.exit(1)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
