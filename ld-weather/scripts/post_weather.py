#!/usr/bin/env python3
"""post_weather.py — post ld-weather's kiosk tile.

Thin wrapper over `team-skills/ld-shared/scripts/post_to_kiosk.py`: sets
the bundle-specific MESSAGE_FILE + CARD + BODY_TYPE, then dispatches.

Posts as card 3 / type "weather" — a self-contained HTML tile the viewer
renders verbatim. TITLE is hidden so the tile gets the full card height.
"""
import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)
import post_to_kiosk  # noqa: E402

post_to_kiosk.MESSAGE_FILE = "/tmp/ld-weather-text"
post_to_kiosk.CARD = "3"
post_to_kiosk.BODY_TYPE = "weather"
post_to_kiosk.TITLE = ""  # hide the eyebrow — the self-contained tile owns the card


if __name__ == "__main__":
    post_to_kiosk.main()
