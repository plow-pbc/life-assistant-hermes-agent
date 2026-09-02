#!/usr/bin/env python3
"""post_alert.py — post ld-morning-triage's kiosk alert.

Thin wrapper over `ld-shared/scripts/post_to_kiosk.py`: sets the
bundle-specific MESSAGE_FILE + CARD + BODY_TYPE, then dispatches.

MESSAGE_FILE sits under /var/lib/hermes (HERMES_WRITE_SAFE_ROOT) per #12. The body
is a ≤115-char paraphrase of a private message; the shared helper's dry-run
redaction and consume-on-success semantics are the deliberate handling for
that (see post_to_kiosk.py's module docstring).
"""
import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)
import post_to_kiosk  # noqa: E402

post_to_kiosk.MESSAGE_FILE = "/var/lib/hermes/ld/morning-triage-text"
post_to_kiosk.CARD = "1"
post_to_kiosk.BODY_TYPE = "alert"
post_to_kiosk.TITLE = ""  # hide the eyebrow — the alert text gets the full card height


if __name__ == "__main__":
    post_to_kiosk.main()
