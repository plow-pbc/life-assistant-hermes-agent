#!/usr/bin/env python3
"""mint_wall_token.py -- the wall's bearer, minted here once and shipped in two files.

The Pi keeps today's server: /api/message behind DASHBOARD_TOKEN, on the
household LAN. This agent runs in the cloud and cannot reach it; the owner's
Mac, running Plow Latch, can. So the token minted here goes to exactly two
places, both files under /opt/data/ld that the agent ships whole with
Latch's file tool -- never through chat, never on argv (argv is shown on the
owner's approval card and kept in the audit record):

  /opt/data/ld/pi.env         ICAL_URL + DASHBOARD_TOKEN   -> the Pi's ~/ld-data/.env
  /opt/data/ld/dashboard.hdr  Authorization: Bearer <t>    -> ~/Plow/ld/dashboard.hdr on the Mac

and three lines are appended to /opt/data/.env for the producers:
DASHBOARD_ENDPOINT_URL (the Pi's message API), DASHBOARD_TOKEN, and
DASHBOARD_DELIVERY=latch, which is what turns post_to_kiosk.py's POST into a
Latch hand-off. The append lands AFTER the gateway loaded the dotenv, which
is why post_to_kiosk.py reads the dotenv itself as its third source.

Idempotent on the token: a dotenv that already names DASHBOARD_ENDPOINT_URL
never re-mints -- the Pi holds the old token, and a new one here would lock
the producers out of the wall. The ADDRESS is not sticky the same way: a
re-run with a different pi_address re-points DASHBOARD_ENDPOINT_URL in place
(token unchanged), so cards follow the owner's current Pi instead of a
decommissioned one. The two files ARE rewritten from the existing token every
run, so a re-run after a lost /opt/data/ld/ still has something to ship.

Stdout never carries the token. It carries pi_line_1= and pi_line_2=, the two
commands the agent runs on the Pi through Latch -- bare, one per line, nothing
shell-wrapped, so each value drops straight into an ssh argv element.

ICAL_URL is written blank unless --ical-url is given: nothing in the config
or the interview otherwise carries an iCal feed URL (the calendar ids there
are gog's, not a feed), and the viewer treats a blank one as "Can't reach
calendar" until the owner fills it. The URL is a private feed, so like the
token it is written to pi.env only -- never printed.
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)
from runtime_env import DOTENV, dotenv_values  # noqa: E402

LD_DIR = "/opt/data/ld"
# A LAN IP or an mDNS/DNS name. It lands inside a URL in the dotenv and inside
# the curl the agent runs through Latch, so nothing else is allowed through.
PI_ADDRESS_RE = re.compile(r"[A-Za-z0-9.-]+")
# The Pi's ssh login. It rides ssh/scp argv in Phase 3; this check is the
# code-level twin of the interview rule so a missed refusal there still
# cannot put shell-relevant characters on the Mac.
PI_USER_RE = re.compile(r"[A-Za-z0-9._-]+")
# apt-get, not apt: apt prints "WARNING: apt does not have a stable CLI
# interface", and the skill reads any WARNING as "this phase did not finish".
# `sudo env ...`, not a bare prefix: sudo's env_reset drops a caller-set
# DEBIAN_FRONTEND before apt-get ever sees it.
PI_LINE_1 = ("sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y"
             " nodejs npm git chromium fonts-noto-color-emoji")
# The template's main, tracked directly: a household without a fork of
# plow-pbc/life-dashboard runs the updater against the template itself.
# bootstrap.sh requires the repo URL positionally (${1:?usage}).
PI_LINE_2 = ("curl -fsSL https://raw.githubusercontent.com/plow-pbc/life-dashboard/main/updater/bootstrap.sh"
             " | sh -s -- https://github.com/plow-pbc/life-dashboard.git")


def append_dotenv(path, pairs):
    # The leading newline is not decoration: a dotenv the gateway or a person
    # last wrote may not end in one, and a bare append would splice the first
    # key onto the last line's value -- taking the instance off its chat, not
    # just off its wall. runtime_env skips the blank line it may leave.
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n" + "".join(f"{k}={v}\n" for k, v in pairs))


def rewrite_dotenv_line(path, key, value):
    """Converge one machine-written NAME=value line. Only ever aimed at the
    endpoint line -- the token line is never touched (a re-mint would lock the
    producers out of the wall). Written beside and os.replace'd in, never
    truncate-in-place: this file is the whole agent's config, and a crash
    mid-write must leave the original intact."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    out = [f"{key}={value}" if line.partition("=")[0] == key else line for line in lines]
    tmp = f"{path}.repoint"
    write_private(tmp, "\n".join(out) + "\n")
    os.replace(tmp, path)


def write_private(path, text):
    """Create-or-rewrite, mode 600. fchmod BEFORE the write: O_CREAT's mode only
    applies to a new file, and a rewrite of a looser-permissioned one would
    expose the token at the old mode for the length of the write."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def main(argv=None, dotenv_path=DOTENV, ld_dir=LD_DIR):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pi_address", help="the Pi on the owner's LAN: an IP, or raspberrypi.local")
    parser.add_argument(
        "--ical-url", default="",
        help="optional iCal feed URL for the Pi's calendar tile; blank is legal (502s the tile)",
    )
    parser.add_argument(
        "--pi-user", default="",
        help="the Pi's ssh login, validated here for Phase 3's argv; blank skips the check",
    )
    args = parser.parse_args(argv)
    if not PI_ADDRESS_RE.fullmatch(args.pi_address):
        raise SystemExit(
            f"refusing: pi address {args.pi_address!r} is not [A-Za-z0-9.-] -- "
            "it lands in a URL and in an ssh argv element"
        )
    if args.pi_user and not PI_USER_RE.fullmatch(args.pi_user):
        raise SystemExit(
            f"refusing: pi user {args.pi_user!r} is not [A-Za-z0-9._-] -- "
            "it lands in an ssh argv element in Phase 3"
        )

    values = dotenv_values(dotenv_path)
    old = values.get("DASHBOARD_ENDPOINT_URL", "").strip()
    endpoint = f"http://{args.pi_address}:5174/api/message"
    if old:
        token = values.get("DASHBOARD_TOKEN", "").strip()
        if not token:
            raise SystemExit(
                f"refusing: {dotenv_path} names DASHBOARD_ENDPOINT_URL but DASHBOARD_TOKEN "
                "is blank -- the Pi's token is not here to ship; restore the line or start over"
            )
        if old == endpoint:
            print(f"already minted: DASHBOARD_ENDPOINT_URL={endpoint} (unchanged -- the Pi holds this token)")
        else:
            rewrite_dotenv_line(dotenv_path, "DASHBOARD_ENDPOINT_URL", endpoint)
            print(f"re-pointed: DASHBOARD_ENDPOINT_URL={endpoint} (token unchanged -- "
                  "cards now target this Pi; ship pi.env to it in Phase 3)")
    else:
        token = secrets.token_urlsafe(24)
        append_dotenv(dotenv_path, [
            ("DASHBOARD_ENDPOINT_URL", endpoint),
            ("DASHBOARD_TOKEN", token),
            ("DASHBOARD_DELIVERY", "latch"),
        ])
        print(f"minted the wall token; appended DASHBOARD_ENDPOINT_URL={endpoint}, "
              f"DASHBOARD_TOKEN and DASHBOARD_DELIVERY=latch to {dotenv_path}.")

    os.makedirs(ld_dir, mode=0o700, exist_ok=True)
    write_private(os.path.join(ld_dir, "pi.env"), f"ICAL_URL={args.ical_url}\nDASHBOARD_TOKEN={token}\n")
    write_private(os.path.join(ld_dir, "dashboard.hdr"), f"Authorization: Bearer {token}\n")
    print(f"wrote {ld_dir}/pi.env and {ld_dir}/dashboard.hdr (mode 600) -- "
          "ship them with plow_write_file; never paste them.")
    print(f"pi_line_1={PI_LINE_1}")
    print(f"pi_line_2={PI_LINE_2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
