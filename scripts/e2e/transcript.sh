#!/usr/bin/env bash
# The conversation as the owner's handset sees it. /ui/... needs no auth.
#
# The twin keys threads by its OWN id (chat_N), not the Plow chat uid
# (cht_...) -- passing the wrong one here is a 404, not an empty thread.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
require TWIN_HOST_BASE TWIN_THREAD

python3 - "$TWIN_HOST_BASE" "$TWIN_THREAD" <<'PY'
import json, sys, urllib.request
twin, thread = sys.argv[1:3]
with urllib.request.urlopen(f"{twin}/ui/chats/{thread}") as resp:
    chat = json.load(resp)
for msg in chat.get("messages", []):
    who = "owner" if msg["direction"] == "inbound" else "AGENT"
    for part in msg.get("parts", []):
        if part.get("type") == "text":
            print(f"{msg['sent_at']}  {who:5}  {part['value']}")
        else:
            print(f"{msg['sent_at']}  {who:5}  [{part.get('type')} "
                  f"{part.get('mime_type')} {part.get('filename')}] {part.get('url')}")
PY
