#!/usr/bin/env bash
# Text the agent as the owner's handset. Inbound never goes to Plow directly:
# POST /channels/linq/event is HMAC-verified and only the twin holds the
# secret, so everything enters through the twin, which signs for us.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
require TWIN_HOST_BASE LINE_PHONE MEMBER_PHONE TWIN_THREAD
[ $# -ge 1 ] || { echo "usage: send.sh <text>" >&2; exit 2; }

# The baseline for await-reply.sh, taken HERE -- before the inbound exists --
# rather than by that script once this one has returned. The agent sometimes
# answers in under a second, and a baseline read after the fact already counts
# that reply, so the wait runs to its timeout and calls a working turn a
# failure. Nothing can produce an outbound between this read and the POST
# below: the message that provokes one has not been sent yet.
outbound_count > "$BASELINE_FILE"

python3 - "$TWIN_HOST_BASE" "$LINE_PHONE" "$MEMBER_PHONE" "$*" <<'PY'
import json, sys, urllib.request
twin, line, member, text = sys.argv[1:5]
body = json.dumps({"to_phone": line, "remote_phone": member, "text": text}).encode()
req = urllib.request.Request(f"{twin}/ui/inbound", data=body,
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req) as resp:
    print(f"sent ({resp.status}): {text}")
PY
