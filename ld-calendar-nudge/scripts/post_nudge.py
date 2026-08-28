#!/usr/bin/env python3
"""post_nudge.py — the nudge's one posting command: kiosk card 1, then chat.

The sheet runs only this. Order is the data-integrity contract:

1. Resolve + validate the Plow Chat config FIRST — env, then /opt/data/.env
   (the #24 lesson) — refusing by name before ANYTHING posts, so a blank
   chat config can never leave a qualifying run half-delivered (kiosk up,
   owner never messaged, handoff already consumed).
2. Kiosk: post_to_kiosk.main(consume=False) posts card 1, `type: "alert"`
   (the slot shared with ld-morning-triage; the store keeps the latest post
   per card) and fails loudly on any non-200.
3. Chat: the same reminder set goes to the owner over
   {base}/v1/chats/{uid}/messages through the shared post_bearer_json —
   no-redirect guard on every bearer request, bearer never in argv.

Both handoffs — written by nudge_candidates.py, never by the model — are
consumed HERE, only after both legs succeed: a failure at either leg leaves
both files for a retry, and re-posting the kiosk on a chat retry is a
harmless latest-wins replace. One consume owner, one command.

MESSAGE_FILE sits under /opt/data per #12.
"""
import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)
import post_to_kiosk  # noqa: E402
from runtime_env import DOTENV, dotenv_values  # noqa: E402

post_to_kiosk.MESSAGE_FILE = "/opt/data/ld/calendar-nudge-text"
post_to_kiosk.CARD = "1"
post_to_kiosk.BODY_TYPE = "alert"
post_to_kiosk.TITLE = ""  # hide the eyebrow — the reminder gets the full card height

# The chat leg's own handoff (every qualifying reminder; the kiosk file
# carries only the earliest). Written by nudge_candidates.py.
CHAT_FILE = "/opt/data/ld/calendar-nudge-chat"


def require(name, dotenv):
    value = (os.environ.get(name) or dotenv.get(name) or "").strip()
    if not value:
        sys.exit(
            f"{name} is unset or blank in the env and {DOTENV} -- activation "
            "writes it there; refusing BEFORE the kiosk post so a half-"
            "delivered run cannot happen"
        )
    return value


def resolve_chat():
    """The chat endpoint + bearer, validated before anything posts."""
    dotenv = dotenv_values(DOTENV)
    base = require("PLOW_CHAT_BASE_URL", dotenv).rstrip("/")
    uid = require("PLOW_CHAT_CHAT_UID", dotenv)
    token = require("PLOW_CHAT_TOKEN", dotenv)
    return f"{base}/v1/chats/{uid}/messages", token


def main():
    chat_url, token = resolve_chat()

    text = None
    if "--dry-run" not in sys.argv:
        # Read (and require) the chat handoff up front too — same
        # fail-before-posting rule as the credentials.
        with open(CHAT_FILE) as f:
            text = f.read().strip()
        if not text:
            sys.exit(f"{CHAT_FILE} is empty -- nothing to send")

    post_to_kiosk.main(consume=False)
    if "--dry-run" in sys.argv:
        # main() printed the redacted kiosk envelope; nothing was consumed.
        print(f"dry-run: would then POST the chat handoff to {chat_url}")
        return

    post_to_kiosk.post_bearer_json(chat_url, token, {"body": text}, "Plow Chat")

    # Both legs succeeded: consume both one-shot handoffs. CHAT_FILE first —
    # a crash between the two unlinks then biases the residue toward the
    # accepted failure modes: a retry fails loudly on the missing chat
    # handoff (never a duplicate chat message), and the next tick overwrites
    # whatever is left.
    os.unlink(CHAT_FILE)
    os.unlink(post_to_kiosk.MESSAGE_FILE)
    print(f"posted kiosk card and chat nudge ({len(text)} chars)")


if __name__ == "__main__":
    main()
