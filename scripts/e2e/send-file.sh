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

# ld-shared/scripts on the path so this uses the same bearer transport the ld-*
# scripts do -- as PYTHONPATH rather than the sys.path.insert they use, because
# a heredoc has no __file__ to hang a relative path off.
PYTHONPATH="$REPO_DIR/ld-shared/scripts${PYTHONPATH:+:$PYTHONPATH}" \
python3 - "$PLOW_API_HOST_BASE" "$PLOW_AGENT_TOKEN" "$PLOW_HOME_CHANNEL" "$1" "${2:-}" <<'PY'
import json, mimetypes, os, sys, urllib.request

# open_no_redirect, not urlopen: urllib follows a redirect and replays the
# request at whatever the response points to, bearer header included, so a
# redirect anywhere on this path hands the agent's credential to an origin Plow
# never named. The ld-* scripts already go through this for that reason, and
# there is no cause for the harness to be the one place that does not.
from bearer_http import open_no_redirect  # noqa: E402

# Bounded, because none of these should ever hang: an unreachable twin or API
# left send-file.sh stuck with no output until somebody noticed. The upload gets
# longer than the JSON calls -- it is the one carrying the bytes.
CALL_TIMEOUT = 30
UPLOAD_TIMEOUT = 120

api, token, chat, path, caption = sys.argv[1:6]
data = open(path, "rb").read()
name = os.path.basename(path)
ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
auth = {"Authorization": f"Bearer {token}"}


def post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={**headers, "Content-Type": "application/json"})
    with open_no_redirect(req, timeout=CALL_TIMEOUT) as resp:
        return json.load(resp)


declared = post(f"{api}/v1/chats/{chat}/attachments",
                {"filename": name, "content_type": ctype, "size_bytes": len(data)}, auth)
# The PUT goes to the provider's URL with EXACTLY the headers Plow returned and
# nothing else -- that URL is a write capability, not a Plow endpoint, so the
# bearer must not ride along. It goes through the same no-redirect opener even
# so: the bytes are the capability here, and a redirect would hand them to
# whatever answered.
put = urllib.request.Request(declared["upload_url"], data=data,
                             headers=declared["upload_headers"], method="PUT")
with open_no_redirect(put, timeout=UPLOAD_TIMEOUT) as resp:
    assert resp.status < 400, resp.status
sent = post(f"{api}/v1/chats/{chat}/messages",
            {"body": caption.strip(), "attachment_uids": [declared["uid"]]}, auth)
print(f"sent {name} ({ctype}, {len(data)} bytes) as {sent.get('uid')}")
PY
