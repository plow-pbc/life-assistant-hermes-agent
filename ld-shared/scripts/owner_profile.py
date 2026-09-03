#!/usr/bin/env python3
"""owner_profile.py -- what to call the owner, read from and written to their Plow account.

The name lives in ONE place, `users.display_name` on the Plow API: it is what the
chat roster names the owner by in every shared thread, what invites call them,
and what this agent confirms on first contact. Nothing here writes a copy to
disk; a turn that needs the name runs `get`.

  get                 print the account's display name, or "(unset)" when there is none
  set --input PATH    record the name in PATH on the account and print what was stored

PATH holds the API's own PATCH body and nothing else, `{"display_name": "..."}`,
staged by the turn with its FILE tool. Never argv: the name is a person's own
words, and a command composed around someone's words is a command built out of
their input. It is removed once it has been written through, so the account
stays the only copy.

The credential is the instance's own PLOW_AGENT_TOKEN, the same one the mailbox
read uses; a blank one refuses by name.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from bearer_http import request_json, require  # noqa: E402

PROFILE = "/v1/auth/profile"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("get")
    sub.add_parser("set").add_argument(
        "--input", metavar="PATH", required=True,
        help='read {"display_name": ...} from PATH, where the turn staged it with its FILE tool')
    args = parser.parse_args(argv)
    base, token = require("PLOW_API_BASE"), require("PLOW_AGENT_TOKEN")

    if args.command == "set":
        try:
            with open(args.input, encoding="utf-8") as staged:
                name = (json.load(staged).get("display_name") or "").strip()
        except (OSError, ValueError, AttributeError) as exc:
            sys.exit(f"error: could not read {args.input}: {exc}")
        if not name:
            sys.exit("error: display_name is blank; nothing was recorded")
        profile = request_json("PATCH", base, PROFILE, token, f"PATCH {PROFILE}", {"display_name": name})
    else:
        profile = request_json("GET", base, PROFILE, token, f"GET {PROFILE}")
    print(profile.get("display_name") or "(unset)")
    # After the name is printed, never before: the sheet tells the turn to say
    # that line back, and an OSError here must not take it with it. Only after
    # the write landed, too -- a refusal above keeps the file so the turn can
    # fix what was named and run again -- and never left behind on success,
    # where it would be a second copy of the owner's own words.
    if args.command == "set":
        os.remove(args.input)


if __name__ == "__main__":
    main()
