#!/usr/bin/env python3
"""read_inbox.py — the assistant's own mailbox, read on demand.

The owner asks "did you see my email?" in chat and this answers it. There is
no poller and no stored copy: the question is the trigger, and Gmail stays the
only place the mail lives.

Which mailbox needs no configuring. `GET /v1/lines` returns the email line
this credential can reach and no other -- the API matches the caller's
assistant persona against the mailbox's, so Elm is handed elm@plow.co and
nothing else. A second one in that list would mean the server's rule changed
under us, so this refuses rather than guessing which is ours.

What comes back is only mail the owner sent or was copied on; the server
applies that, not this script.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"))
from bearer_http import open_no_redirect  # noqa: E402

TIMEOUT = 30


def require(name):
    """Refuse by name — a blank credential must say which one is blank."""
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"error: {name} is not set; this instance cannot reach its mailbox")
    return value


def get_json(base, path, token, label):
    request = urllib.request.Request(
        url=f"{base.rstrip('/')}{path}",
        method="GET",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with open_no_redirect(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        sys.exit(f"error: {label} returned HTTP {exc.code} {exc.reason}")
    except urllib.error.URLError as exc:
        sys.exit(f"error: {label} failed: {exc.reason}")


def resolve_mailbox(base, token):
    lines = get_json(base, "/v1/lines", token, "GET /v1/lines")["data"]
    mailboxes = [line for line in lines if line.get("provider_type") == "email"]
    if len(mailboxes) != 1:
        sys.exit(
            f"error: expected exactly one reachable email line, got {len(mailboxes)}; "
            "the server's persona rule changed and this script must not guess"
        )
    return mailboxes[0]


OPEN = "<<<UNTRUSTED_EMAIL>>>"
CLOSE = "<<<END_UNTRUSTED_EMAIL>>>"


def defang(value):
    """Take the fence out of text that is inside the fence.

    A sender who writes the closing marker into their own body would otherwise
    end the fence early and have whatever follows read as ours -- the one way a
    label like this fails. So the markers are stripped from every field the
    sender controls, which is all of them.
    """
    return str(value).replace(OPEN, "<<<").replace(CLOSE, "<<<")


def render(thread):
    """One thread, wholly inside the fence: headers as well as bodies.

    A subject is as sender-written as a body, so drawing the line between them
    would just move the gap. The fence is a label and not a wall -- the words
    inside are someone else's, and an instruction among them is still only text
    the owner asked us to read.
    """
    messages = thread.get("messages", [])
    # Subject lives on the message, not the thread; the first one names it.
    subject = (messages[0].get("subject") if messages else "") or "(no subject)"
    out = [f"## {defang(subject)}", OPEN]
    for message in messages:
        out.append(f"\nFrom: {defang(message.get('from_address', '?'))}  ({defang(message.get('date', '?'))})")
        for field in ("to", "cc"):
            if message.get(field):
                out.append(f"{field.title()}: {defang(', '.join(message[field]))}")
        out.append("")
        out.append(defang(message.get("body_text") or "").strip() or "(empty)")
    out.append(CLOSE)
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="how far back to look (default 7)")
    args = parser.parse_args(argv)

    base = require("PLOW_API_BASE")
    token = require("PLOW_AGENT_TOKEN")
    mailbox = resolve_mailbox(base, token)
    since = int(time.time()) - args.days * 86400

    query = urllib.parse.urlencode({"since": since})
    threads = get_json(
        base, f"/v1/email-lines/{mailbox['uid']}/threads?{query}", token, "GET email-line threads"
    )["data"]

    print(f"{mailbox['provider_key']} — {len(threads)} thread(s) in the last {args.days} day(s)\n")
    if not threads:
        # Say the mailbox was reached. "Nothing" and "the call failed" must not
        # read the same to the model answering the owner.
        print("No mail the owner sent or was copied on in that window.")
        return
    for thread in threads:
        print(render(thread))
        print()


if __name__ == "__main__":
    main()
