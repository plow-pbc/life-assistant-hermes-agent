#!/usr/bin/env python3
"""post_nudge.py — the nudge's one posting command: kiosk card 1, then chat.

The sheet runs only this. One handoff, written by nudge_candidates.py (never
by the model): every qualifying reminder, earliest first. Order is the
data-integrity contract:

1. Resolve + validate the Plow Chat config FIRST — env, then /opt/data/.env
   (the #24 lesson) — refusing by name before ANYTHING posts, so a blank
   chat config can never leave a qualifying run half-delivered (kiosk up,
   owner never messaged).
2. Read the handoff once through post_to_kiosk's fixed-file read/refusal
   seam (missing or empty refuses loudly, nothing consumed).
3. Kiosk: the FIRST line — the earliest reminder, ≤115 enforced by the
   filter — goes to card 1, `type: "alert"` (the slot shared with
   ld-morning-triage; latest post per card wins), over post_to_kiosk's
   stdin transport: MESSAGE_FILE stays None, so the shared helper neither
   reads nor consumes the handoff — this coordinator owns both.
4. Chat: the whole body goes to the owner over
   {base}/v1/chats/{uid}/messages through the shared post_bearer_json —
   no-redirect guard on every bearer request, bearer never in argv.
5. Consume the handoff once, only after both legs succeeded: a failure at
   either leg leaves it for a retry (a kiosk re-post on a chat retry is a
   harmless latest-wins replace).
"""
import io
import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)
import post_to_kiosk  # noqa: E402
from runtime_env import DOTENV, dotenv_values  # noqa: E402

post_to_kiosk.CARD = "1"
post_to_kiosk.BODY_TYPE = "alert"
post_to_kiosk.TITLE = ""  # hide the eyebrow — the reminder gets the full card height

# The one handoff, written by nudge_candidates.py and consumed here.
HANDOFF = "/opt/data/ld/calendar-nudge-text"


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
    base = require("PLOW_API_BASE", dotenv).rstrip("/")
    uid = require("PLOW_HOME_CHANNEL", dotenv)
    token = require("PLOW_AGENT_TOKEN", dotenv)
    return f"{base}/v1/chats/{uid}/messages", token


def main():
    chat_url, token = resolve_chat()
    text = post_to_kiosk.read_required_file(HANDOFF, "reminder text")

    # The kiosk leg takes its one line over the stdin transport — an
    # importer-only seam (the CLI feeds no stdin), so the shared helper
    # never touches the handoff.
    saved_stdin = sys.stdin
    sys.stdin = io.StringIO(text.splitlines()[0])
    try:
        post_to_kiosk.main()
    finally:
        sys.stdin = saved_stdin

    if "--dry-run" in sys.argv:
        # main() printed the redacted kiosk envelope; nothing was consumed.
        print(f"dry-run: would then POST the reminder body to {chat_url}")
        return

    post_to_kiosk.post_bearer_json(chat_url, token, {"body": text}, "Plow Chat")

    # Both legs are done here: chat posted, and the kiosk body either posted
    # (direct) or sits durably in the outbox (latch) — the Latch calls above
    # replay from that file, so consuming the handoff loses nothing.
    os.unlink(HANDOFF)
    latch = dotenv_values(DOTENV).get(post_to_kiosk.DELIVERY_KEY, "").strip() == "latch"
    kiosk = "kiosk card queued for Latch (both calls above still owed)" if latch else "posted kiosk card"
    print(f"{kiosk}; chat nudge posted ({len(text)} chars)")


if __name__ == "__main__":
    main()
