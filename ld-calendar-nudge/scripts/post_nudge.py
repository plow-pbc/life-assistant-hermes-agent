#!/usr/bin/env python3
"""post_nudge.py — the nudge's one posting command: kiosk card 1, then chat.

The sheet runs only this. Two handoffs, written by nudge_candidates.py (never
by the model): every qualifying reminder, earliest first, for chat; and the
earliest one on a kitchen-wall calendar for the kiosk, absent when no meeting
is on the wall. Order is the data-integrity contract:

1. Resolve + validate the Plow Chat config FIRST — from the process
   environment, which first boot fills from the credential the host dropped in
   — refusing by name before ANYTHING posts, so a blank chat config can never
   leave a qualifying run half-delivered (kiosk up, owner never messaged).
2. Read the chat handoff once through post_to_kiosk's fixed-file read/refusal
   seam (missing or empty refuses loudly, nothing consumed).
3. Kiosk, only when the card handoff exists: its line — ≤115 enforced by the
   filter — goes to card 1, `type: "alert"` (the slot shared with
   ld-morning-triage; latest post per card wins), over post_to_kiosk's
   stdin transport: MESSAGE_FILE stays None, so the shared helper neither
   reads nor consumes the handoff — this coordinator owns both.
4. Chat: the whole body goes to the owner over
   {base}/v1/chats/{uid}/messages through the shared post_bearer_json —
   no-redirect guard on every bearer request, bearer never in argv.
5. Consume both handoffs once, only after both legs succeeded: a failure at
   either leg leaves them for a retry (a kiosk re-post on a chat retry is a
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
from runtime_env import AGENT_DOTENV, agent_values  # noqa: E402

post_to_kiosk.CARD = "1"
post_to_kiosk.BODY_TYPE = "alert"
post_to_kiosk.TITLE = ""  # hide the eyebrow — the reminder gets the full card height

# The handoffs, written by nudge_candidates.py and consumed here.
HANDOFF = "/var/lib/hermes/ld/calendar-nudge-text"
CARD = "/var/lib/hermes/ld/calendar-nudge-card"


def require(name):
    value = (os.environ.get(name) or "").strip()
    if not value:
        sys.exit(
            f"{name} is unset or blank in this process's environment -- first "
            "boot publishes it there from the credential the host dropped in; "
            "refusing BEFORE the kiosk post so a half-delivered run cannot "
            "happen"
        )
    return value


def resolve_chat():
    """The chat endpoint + bearer, validated before anything posts.

    From the container environment alone. These three are the tenant's
    credential, and first boot is what publishes them; a file the agent can
    write is not a place to look for the API base its own bearer is sent to.
    """
    base = require("PLOW_API_BASE").rstrip("/")
    uid = require("PLOW_HOME_CHANNEL")
    token = require("PLOW_AGENT_TOKEN")
    return f"{base}/v1/chats/{uid}/messages", token


def main():
    chat_url, token = resolve_chat()
    text = post_to_kiosk.read_required_file(HANDOFF, "reminder text")
    on_wall = os.path.exists(CARD)

    if on_wall:
        # The kiosk leg takes its one line over the stdin transport — an
        # importer-only seam (the CLI feeds no stdin), so the shared helper
        # never touches the handoff.
        saved_stdin = sys.stdin
        sys.stdin = io.StringIO(post_to_kiosk.read_required_file(CARD, "wall card"))
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
    if not on_wall:
        print(f"no meeting on a wall calendar, so no kiosk card; chat nudge posted ({len(text)} chars)")
        return
    os.unlink(CARD)
    latch = agent_values(AGENT_DOTENV).get(post_to_kiosk.DELIVERY_KEY, "").strip() == "latch"
    kiosk = "kiosk card queued for Latch (both calls above still owed)" if latch else "posted kiosk card"
    print(f"{kiosk}; chat nudge posted ({len(text)} chars)")


if __name__ == "__main__":
    main()
