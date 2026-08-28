#!/usr/bin/env python3
"""post_message.py — post ld-morning-updates' kiosk affirmation.

Thin wrapper over `ld-shared/scripts/post_to_kiosk.py`: sets the
bundle-specific MESSAGE_FILE + CARD + BODY_TYPE, then dispatches.

MESSAGE_FILE sits under /opt/data (HERMES_WRITE_SAFE_ROOT) per #12. The body
is a ≤115-char affirmation composed from calendar context; the shared
helper's dry-run redaction and consume-on-success semantics are the
deliberate handling for that (see post_to_kiosk.py's module docstring).
"""
import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)
import post_to_kiosk  # noqa: E402

post_to_kiosk.MESSAGE_FILE = "/opt/data/ld/morning-updates-text"
post_to_kiosk.CARD = "2"
post_to_kiosk.BODY_TYPE = "affirmation"
post_to_kiosk.TITLE = ""  # hide the eyebrow — the affirmation gets the full card height


if __name__ == "__main__":
    post_to_kiosk.main()
