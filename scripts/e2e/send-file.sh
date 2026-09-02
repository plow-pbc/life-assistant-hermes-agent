#!/usr/bin/env bash
# Put a file into the chat WITHOUT a model turn, by walking Plow's media
# contract directly: declare, upload, send -- the same three steps the plugin
# makes (hermes-plow-chat plow-chat-platform/__init__.py::_send_attachment).
# Use it to test the transport on its own when the agent's judgement is not
# what is under test.
#
# NOT `hermes send`: that refuses plow_chat out of process --
#   "No live adapter for platform 'plow_chat' ... the platform plugin must
#    register a standalone_sender_fn"
# -- so the only non-LLM path to this chat is the API itself.
#
# The path is a HOST path here, not a container one.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
require PLOW_API_HOST_BASE PLOW_AGENT_TOKEN PLOW_HOME_CHANNEL
[ $# -ge 1 ] || { echo "usage: send-file.sh <host-path> [caption]" >&2; exit 2; }

python3 - "$PLOW_API_HOST_BASE" "$PLOW_AGENT_TOKEN" "$PLOW_HOME_CHANNEL" "$1" "${2:-}" <<'PY'
import json, mimetypes, os, sys, urllib.request
api, token, chat, path, caption = sys.argv[1:6]
data = open(path, "rb").read()
name = os.path.basename(path)
ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
auth = {"Authorization": f"Bearer {token}"}


def post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


declared = post(f"{api}/v1/chats/{chat}/attachments",
                {"filename": name, "content_type": ctype, "size_bytes": len(data)}, auth)
# The PUT goes to the provider's URL with EXACTLY the headers Plow returned and
# nothing else -- that URL is a write capability, not a Plow endpoint, so the
# bearer must not ride along.
put = urllib.request.Request(declared["upload_url"], data=data,
                             headers=declared["upload_headers"], method="PUT")
with urllib.request.urlopen(put) as resp:
    assert resp.status < 400, resp.status
sent = post(f"{api}/v1/chats/{chat}/messages",
            {"body": caption.strip(), "attachment_uids": [declared["uid"]]}, auth)
print(f"sent {name} ({ctype}, {len(data)} bytes) as {sent.get('uid')}")
PY
