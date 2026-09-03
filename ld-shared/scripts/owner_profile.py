#!/usr/bin/env python3
"""owner_profile.py -- what to call the owner, read from and written to their Plow account.

The name lives in ONE place, `users.display_name` on the Plow API: it is what the
chat roster names the owner by in every shared thread, what invites call them,
and what this agent confirms on first contact. Nothing here writes a copy to
disk; a turn that needs the name runs `get`.

  get         print the account's display name, or "(unset)" when there is none
  set NAME    record NAME on the account and print what was stored

The credential is the instance's own PLOW_AGENT_TOKEN, the same one the mailbox
read uses; a blank one refuses by name.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from bearer_http import request_json, require  # noqa: E402

PROFILE = "/v1/auth/profile"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("get")
    sub.add_parser("set").add_argument("name")
    args = parser.parse_args(argv)
    base, token = require("PLOW_API_BASE"), require("PLOW_AGENT_TOKEN")

    if args.command == "set":
        name = args.name.strip()
        if not name:
            sys.exit("error: the name is blank; nothing was recorded")
        profile = request_json("PATCH", base, PROFILE, token, f"PATCH {PROFILE}", {"display_name": name})
    else:
        profile = request_json("GET", base, PROFILE, token, f"GET {PROFILE}")
    print(profile.get("display_name") or "(unset)")


if __name__ == "__main__":
    main()
