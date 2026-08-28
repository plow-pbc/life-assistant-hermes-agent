#!/usr/bin/env python3
"""post_nudge.py — post ld-calendar-nudge's kiosk reminder.

Thin wrapper over `ld-shared/scripts/post_to_kiosk.py`: sets the
bundle-specific MESSAGE_FILE + CARD + BODY_TYPE, then dispatches.

MESSAGE_FILE sits under /opt/data (HERMES_WRITE_SAFE_ROOT) per #12. Card 1
is the slot shared with ld-morning-triage — the store keeps the latest post
per card, and both are alerts by design.

CONSUME is False: this is the FIRST of two legs (kiosk, then Plow Chat) fed
by the same handoff, so a successful kiosk post must leave the file for
send_nudge_chat.py — the last leg, which owns consume-on-success.
"""
import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)
import post_to_kiosk  # noqa: E402

post_to_kiosk.MESSAGE_FILE = "/opt/data/ld/calendar-nudge-text"
post_to_kiosk.CARD = "1"
post_to_kiosk.BODY_TYPE = "alert"
post_to_kiosk.TITLE = ""  # hide the eyebrow — the reminder gets the full card height
post_to_kiosk.CONSUME = False  # the chat leg still reads this handoff


if __name__ == "__main__":
    post_to_kiosk.main()
