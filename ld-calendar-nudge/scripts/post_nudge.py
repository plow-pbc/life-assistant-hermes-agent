#!/usr/bin/env python3
"""post_nudge.py — post ld-calendar-nudge's kiosk reminder.

Thin wrapper over `ld-shared/scripts/post_to_kiosk.py`: sets the
bundle-specific MESSAGE_FILE + CARD + BODY_TYPE, then dispatches.

MESSAGE_FILE sits under /opt/data per #12. Card 1 is the slot shared with
ld-morning-triage — the store keeps the latest post per card, and both are
alerts by design. The handoff is written by nudge_candidates.py (the
earliest qualifying reminder), never by the model; the chat leg
(send_nudge_chat.py) has its OWN handoff, so the default consume-on-success
applies here like every other wrapper.
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


if __name__ == "__main__":
    post_to_kiosk.main()
