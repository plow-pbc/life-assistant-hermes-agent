#!/usr/bin/env python3
"""triage_candidates.py — filter the iMessage gather for ld-morning-triage.

Reads the `latch messages search --since 36h` answer from its gather-file
argument, deleting the file as it goes (`messages`: one object per message,
newest first, each with its `chat`, its sender in `from` — `me` true on the
owner's own — and its `text`), applies the unaddressed rule per chat — every
inbound message after the chat's last outbound is a candidate; an outbound as
the latest message means the chat was answered — drops excluded handles, and
emits JSON candidates for the LLM to rank. Deterministic on purpose: the LLM
only ranks; it never applies the rule by eye.

The whole unanswered burst is emitted, not just the newest message: an urgent
ask followed by a "you there?" nudge must not hide the ask behind the nudge.

A capped answer (`next` set: more than one page in the window) keeps the
newest messages, and the rule holds on them exactly: everything it drops is
older than everything it keeps, so no kept inbound loses the reply that
followed it.

Exit 2 on malformed input rather than skipping rows: a half-parsed morning is
indistinguishable from a quiet one on the kiosk, so it must fail loudly.
Framing is what fails loudly — an empty gather (latch printed no answer),
missing keys, a `me` that is not a boolean, a timestamp with no offset. A
message with no text is data, not framing: an attachment-only row, or one
latch could not read (`text: null`), produces no candidate.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)
from gather_result import GatherError, read_answer  # noqa: E402

EXCERPT_CAP = 500


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("gather", help="gather file (latch's answer, redirected)")
    args = parser.parse_args()

    # Consume-first lives in ld-shared/scripts/gather_result.py (shared with
    # the calendar-nudge filter).
    try:
        answer = read_answer(args.gather)
    except GatherError as e:
        print(e, file=sys.stderr)
        return 2
    # A window with more messages than one page holds older ones latch did not
    # send, and an urgent ask among them would read as a quiet morning.
    if answer.get("next"):
        print("incomplete gather: the window has more messages than one page", file=sys.stderr)
        return 2
    messages = answer.get("messages")
    if not isinstance(messages, list):
        print("malformed gather: no message list", file=sys.stderr)
        return 2

    with open(args.config) as f:
        excluded = set(
            json.load(f)["morning_triage"]["exclude"]["imessage_handles"]
        )

    chats: dict[int, list[tuple[int, bool, str, str | None]]] = {}
    # Oldest first, as latch ordered them: the per-chat sort below is stable,
    # so two messages in the same second keep latch's order.
    for n, message in enumerate(reversed(messages), 1):
        try:
            sender = message["from"]
            if not isinstance(sender["me"], bool):
                raise ValueError(f"from.me={sender['me']!r}")
            at = datetime.fromisoformat(message["at"])
            if at.utcoffset() is None:
                raise ValueError("timestamp has no UTC offset")
            chats.setdefault(message["chat"]["id"], []).append(
                (int(at.timestamp()), not sender["me"], sender["handle"],
                 message["text"])
            )
        except (KeyError, TypeError, ValueError) as e:
            # Never echo the message itself: it may hold private text.
            print(f"malformed message {n}: {e}", file=sys.stderr)
            return 2

    candidates = []
    for chat_id, msgs in chats.items():
        msgs.sort(key=lambda m: m[0])
        # Everything after the chat's last outbound is the unanswered burst;
        # no outbound anywhere means the whole chat is the burst.
        unanswered_from = max(
            (i + 1 for i, m in enumerate(msgs) if not m[1]), default=0
        )
        for ts, _, handle, body in msgs[unanswered_from:]:
            if handle in excluded or not body:
                continue
            candidates.append({
                "chat_id": chat_id,
                "handle": handle,
                "sent_at": ts,
                "excerpt": body[:EXCERPT_CAP],
            })

    candidates.sort(key=lambda c: c["sent_at"], reverse=True)
    json.dump(candidates, sys.stdout)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
