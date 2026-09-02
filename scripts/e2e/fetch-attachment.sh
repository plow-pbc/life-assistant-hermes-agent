#!/usr/bin/env bash
# Download the latest attachment the agent sent and say what it actually is.
#
# The twin's inbox renders an image as a LINK, not an inline <img> (only audio
# gets a player), so "the GIF arrived" is an assertion, not something you can
# see. This is that assertion: the bytes, and `file`'s verdict on them.
#
# The URL in the transcript is the twin's in-network origin
# (LINQ_TWIN_PUBLIC_BASE_URL, http://dtu-linq:8091), which does not resolve on
# the Mac -- so only the path is reused, against the host-facing twin.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
require TWIN_HOST_BASE TWIN_THREAD

out="${1:-/tmp/e2e-attachment}"

path="$(python3 - "$TWIN_HOST_BASE" "$TWIN_THREAD" <<'PY'
import json, sys, urllib.parse, urllib.request
twin, thread = sys.argv[1:3]
with urllib.request.urlopen(f"{twin}/ui/chats/{thread}") as resp:
    chat = json.load(resp)
for msg in reversed(chat.get("messages", [])):
    for part in msg.get("parts", []):
        if part.get("type") == "media" and part.get("url"):
            print(urllib.parse.urlparse(part["url"]).path)
            raise SystemExit(0)
raise SystemExit("no media part in this thread")
PY
)"

curl -fsS "$TWIN_HOST_BASE$path" -o "$out"
echo "$TWIN_HOST_BASE$path"
ls -l "$out"
file "$out"
