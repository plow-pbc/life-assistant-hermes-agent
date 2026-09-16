#!/usr/bin/env python3
"""post_sports.py — post ld-sports' kiosk tile.

Thin wrapper over `ld-shared/scripts/post_to_kiosk.py`: sets
the bundle-specific MESSAGE_FILE + CARD + BODY_TYPE, then dispatches.

Posts as card 5 / type "sports" — a self-contained HTML tile the viewer
renders verbatim. TITLE is hidden so the tile gets the full card height.

The wall shows images from itself only (its CSP is `img-src 'self' data:`), so
the team logos travel inside the tile: each <img> src the tile names — the ESPN
team logo URL from the scoreboard feed — is fetched here at tile size and
embedded as a data: URI before the tile is posted. A logo that cannot be
embedded stops the run with an error and nothing is posted, so the wall keeps
its last tile.
"""
import base64
import html
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)
import post_to_kiosk  # noqa: E402
from bearer_http import open_no_redirect  # noqa: E402

LOGO_HOST = "a.espncdn.com"
LOGO_PATH = re.compile(r"/i/teamlogos/[a-z0-9/_-]+\.png")
# ESPN's image resizer, at the .sp-logo box size: a 38px logo is ~1-2 KB where
# the feed's 500px original is ~12 KB, and the tile carries every one of them.
LOGO_RESIZER = f"https://{LOGO_HOST}/combiner/i?"
LOGO_PX = 38
LOGO_MAX_BYTES = 64 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
SRC_ATTR = re.compile(r'(?<![\w-])src="([^"]*)"', re.IGNORECASE)


def logo_data_uri(src):
    """The ESPN team logo at `src`, fetched at tile size, as a data: URI."""
    url = urllib.parse.urlsplit(src)
    if url.scheme != "https" or url.hostname != LOGO_HOST or not LOGO_PATH.fullmatch(url.path):
        sys.exit(f"error: tile image {src!r} is not an ESPN team logo (https://{LOGO_HOST}/i/teamlogos/….png)")
    fetch = LOGO_RESIZER + urllib.parse.urlencode({"img": url.path, "h": LOGO_PX, "w": LOGO_PX})
    try:
        with open_no_redirect(urllib.request.Request(fetch), timeout=30) as response:
            png = response.read(LOGO_MAX_BYTES + 1)
    except OSError as exc:  # URLError, HTTPError and timeouts are all OSErrors
        sys.exit(f"error: team logo {src} not fetched: {exc}")
    if len(png) > LOGO_MAX_BYTES or not png.startswith(PNG_MAGIC):
        sys.exit(f"error: team logo {src} is not a PNG of at most {LOGO_MAX_BYTES} bytes")
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def embed_logos(tile):
    """The tile with every <img> src replaced by its embedded logo."""
    def embed(match):
        tag = match.group(0)
        src = SRC_ATTR.search(tag)
        if not src:
            sys.exit(f"error: tile image has no src=\"…\": {tag}")
        return tag[:src.start(1)] + logo_data_uri(html.unescape(src.group(1))) + tag[src.end(1):]
    return IMG_TAG.sub(embed, tile)


post_to_kiosk.MESSAGE_FILE = "/var/lib/hermes/ld/sports-text"
post_to_kiosk.CARD = "5"
post_to_kiosk.BODY_TYPE = "sports"
post_to_kiosk.TITLE = ""  # hide the eyebrow — the self-contained tile owns the card
post_to_kiosk.TRANSFORM = embed_logos


if __name__ == "__main__":
    post_to_kiosk.main()
