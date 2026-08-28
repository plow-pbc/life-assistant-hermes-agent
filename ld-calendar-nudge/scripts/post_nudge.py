#!/usr/bin/env python3
"""post_nudge.py — post ld-calendar-nudge's reminder to both surfaces, in order.

Wrapper over `ld-shared/scripts/post_to_kiosk.py` (sets the bundle-specific
MESSAGE_FILE + CARD + BODY_TYPE, then dispatches) plus this producer's second
leg: the same reminder text to the owner over the Plow Chat gateway, only
after the kiosk post succeeded. One command enforces that ordering and keeps
the text on one fixed handoff path — the cron's turn never rebuilds it.

The chat leg is a committed script rather than the cron's native --deliver
arm on purpose: --deliver relays EVERY final response, and this producer's
half-hourly runs are mostly quiet no-ops (see register_crons.py's divide).

The chat env is resolved BEFORE the kiosk leg runs: post_to_kiosk consumes
MESSAGE_FILE on success, so a half-configured install refusing only at the
chat step would have already posted the card and burned the handoff — the
owner silently misses the chat reminder, every tick. Refusing up front makes
a misconfiguration a no-op on both surfaces. A chat-leg NETWORK failure after
a successful kiosk post still exits non-zero with the file consumed; the
reminder is already on the shared kiosk, and the next half-hourly tick
recomposes from live calendar state — a retry file would only repost a stale
reminder.

MESSAGE_FILE sits under /opt/data (HERMES_WRITE_SAFE_ROOT) per #12. The chat
env values live in /opt/data/.env, which the GATEWAY loads — a docker-exec
shell does not carry them (the #24 gotcha); test from a real agent turn or
source that file first.
"""
import json
import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)
import post_to_kiosk  # noqa: E402

post_to_kiosk.MESSAGE_FILE = "/opt/data/ld/calendar-nudge-text"
post_to_kiosk.CARD = "1"
post_to_kiosk.BODY_TYPE = "alert"
post_to_kiosk.TITLE = ""  # hide the eyebrow — calendar reminders carry no title


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(
            f"error: ${name} is unset or empty — it lands in /opt/data/.env at "
            "activation time, which the gateway loads; a docker-exec shell "
            "must source it first"
        )
    return value


def resolve_chat_env():
    return tuple(
        require_env(n)
        for n in ("PLOW_CHAT_BASE_URL", "PLOW_CHAT_CHAT_UID", "PLOW_CHAT_TOKEN")
    )


def send_chat(text, dry_run, base_url, chat_uid, token):
    url = f"{base_url.rstrip('/')}/v1/chats/{chat_uid}/messages"

    if dry_run:
        print(
            json.dumps(
                {
                    "method": "POST",
                    "url": url,
                    "authorization": "Bearer <redacted>",
                    "body": {"body": f"<redacted, {len(text)} chars>"},
                },
                indent=2,
            )
        )
        return

    post_to_kiosk.post_bearer_json(url, token, {"body": text}, "Plow Chat")


def main():
    dry_run = "--dry-run" in sys.argv[1:]
    # Read before the kiosk leg consumes the file on success; both legs send
    # the identical text.
    text = post_to_kiosk.read_required_file(
        post_to_kiosk.MESSAGE_FILE, "alert text file"
    )
    chat = resolve_chat_env()  # refuse BEFORE the kiosk leg posts and consumes
    post_to_kiosk.main()  # exits non-zero on any kiosk failure — chat never runs
    send_chat(text, dry_run, *chat)


if __name__ == "__main__":
    main()
