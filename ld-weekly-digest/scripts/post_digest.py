#!/usr/bin/env python3
"""post_digest.py — post ld-weekly-digest's kiosk digest.

Thin wrapper over `ld-shared/scripts/post_to_kiosk.py`: sets the
bundle-specific MESSAGE_FILE + CARD + BODY_TYPE, then dispatches.

MESSAGE_FILE sits under /var/lib/hermes (HERMES_WRITE_SAFE_ROOT) per #12. The body
summarizes a household's week from private calendars; the shared helper's
dry-run redaction and consume-on-success semantics are the deliberate
handling for that (see post_to_kiosk.py's module docstring).
"""
import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)
import post_to_kiosk  # noqa: E402

post_to_kiosk.MESSAGE_FILE = "/var/lib/hermes/ld/weekly-digest-text"
post_to_kiosk.CARD = "4"
post_to_kiosk.BODY_TYPE = "digest"
post_to_kiosk.TITLE = "This week"  # human eyebrow over the type's machine word "digest"


if __name__ == "__main__":
    post_to_kiosk.main()
