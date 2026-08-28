#!/usr/bin/env python3
"""triage_candidates.py — decode + filter the iMessage gather for ld-morning-triage.

Reads the fixed sqlite3 query's `-json` stdout on stdin (an array of
`{chat_id, is_from_me, handle, sent_at, hexbody}` objects), decodes each body
(typedstream via the NSString marker, else plain UTF-8), applies the
unaddressed rule per chat — every inbound message after the chat's last
outbound is a candidate; an outbound as the latest message means the chat was
answered — drops excluded handles, and emits JSON candidates for the LLM to
rank. Deterministic on purpose: the LLM only ranks; it never parses sqlite
output or message framing.

The whole unanswered burst is emitted, not just the newest message: an urgent
ask followed by a "you there?" nudge must not hide the ask behind the nudge.

Exit 2 on malformed input rather than skipping rows: a half-parsed morning is
indistinguishable from a quiet one on the kiosk, so it must fail loudly.
Framing is what fails loudly — the JSON envelope, missing keys, is_from_me
outside {0,1}, bad hex. An individual body that yields no text is data, not
framing: attachment-only and sticker rows routinely carry an attributedBody
with nothing decodable (and an attachment-only row's hexbody is the empty
string — sqlite's hex() of NULL is '', never NULL), so one of them must not
abort the whole morning — it just produces no candidate.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

EXCERPT_CAP = 500


def decode_body(blob: bytes) -> str | None:
    """Extract the message string from an attributedBody typedstream, or
    fall back to plain UTF-8 (rows whose body came from `text`)."""
    i = blob.find(b"NSString")
    if i < 0:
        try:
            return blob.decode("utf-8")
        except UnicodeDecodeError:
            return None
    i += len(b"NSString") + 5  # class-end + version markers
    if i >= len(blob):
        return None
    marker = blob[i]
    if marker == 0x81:
        length, i = int.from_bytes(blob[i + 1:i + 3], "little"), i + 3
    elif marker == 0x82:
        length, i = int.from_bytes(blob[i + 1:i + 5], "little"), i + 5
    else:
        length, i = marker, i + 1
    try:
        return blob[i:i + length].decode("utf-8")
    except UnicodeDecodeError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("gather", nargs="?",
                        help="gather file (raw sqlite3 -json output, or the "
                             "persisted plow_run_command result envelope); "
                             "stdin when omitted")
    args = parser.parse_args()

    # sqlite3 -json emits nothing at all (not "[]") for an empty result set.
    # The gather is consumed FIRST: the raw 36-hour corpus must not outlive
    # this run whatever the outcome, so it's read and deleted before anything
    # else (a broken config, a bad envelope) gets a chance to abort the run.
    if args.gather:
        with open(args.gather) as f:
            raw = f.read().strip()
        os.unlink(args.gather)
    else:
        raw = sys.stdin.read().strip()

    with open(args.config) as f:
        excluded = set(
            json.load(f)["morning_triage"]["exclude"]["imessage_handles"]
        )
    # An oversized plow_run_command result reaches the model as a persisted
    # envelope — {"result": "<json of {exit_code, handle, output}>"} — not as
    # raw query stdout. Unwrap it here so the model passes the persisted path
    # untouched; a query result itself is always an array (or empty), never
    # an object, so the sniff cannot misfire on real rows.
    if raw.startswith("{"):
        try:
            inner = json.loads(json.loads(raw)["result"])
            if inner["exit_code"] != 0:
                print(f"gather failed: exit_code={inner['exit_code']}",
                      file=sys.stderr)
                return 2
            raw = inner["output"].strip()
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
            print(f"malformed gather envelope: {e}", file=sys.stderr)
            return 2
    try:
        rows = json.loads(raw) if raw else []
    except json.JSONDecodeError as e:
        print(f"malformed sqlite json: {e}", file=sys.stderr)
        return 2

    chats: dict[int, list[tuple[int, bool, str, str | None]]] = {}
    for n, row in enumerate(rows, 1):
        try:
            direction = row["is_from_me"]
            if direction not in (0, 1):
                raise ValueError(f"is_from_me={direction!r}")
            hexbody = row["hexbody"]
            body = decode_body(bytes.fromhex(hexbody)) if hexbody else None
            chats.setdefault(row["chat_id"], []).append(
                (row["sent_at"], direction == 0, row["handle"], body)
            )
        except (KeyError, TypeError, ValueError) as e:
            # Never echo the row itself: it may hold private message bytes.
            print(f"malformed sqlite row {n}: {e}", file=sys.stderr)
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
