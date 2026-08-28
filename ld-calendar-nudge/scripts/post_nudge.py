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

The kiosk leg consumes MESSAGE_FILE on success (post_to_kiosk's semantics),
so the text is read here first. A chat-leg failure after a successful kiosk
post exits non-zero with the file already consumed; the reminder is already
on the shared kiosk, and the next half-hourly tick recomposes from live
calendar state — a retry file would only repost a stale reminder.

MESSAGE_FILE sits under /opt/data (HERMES_WRITE_SAFE_ROOT) per #12. The chat
env values live in /opt/data/.env, which the GATEWAY loads — a docker-exec
shell does not carry them (the #24 gotcha); test from a real agent turn or
source that file first.
"""
import json
import os
import sys
import urllib.error
import urllib.request

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


def send_chat(text, dry_run):
    base_url = require_env("PLOW_CHAT_BASE_URL")
    chat_uid = require_env("PLOW_CHAT_CHAT_UID")
    token = require_env("PLOW_CHAT_TOKEN")
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

    req = urllib.request.Request(
        url=url,
        method="POST",
        data=json.dumps({"body": text}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    # Same posture as the kiosk leg: a redirect would forward the bearer to a
    # new origin, so refuse it and let the HTTPError path fail loudly.
    opener = post_to_kiosk._no_redirect_opener()
    try:
        opener.open(req, timeout=30).close()
    except urllib.error.HTTPError as exc:
        sys.exit(f"error: Plow Chat returned HTTP {exc.code} {exc.reason}")
    except urllib.error.URLError as exc:
        sys.exit(f"error: POST to Plow Chat failed: {exc.reason}")


def main():
    dry_run = "--dry-run" in sys.argv[1:]
    # Read before the kiosk leg consumes the file on success; both legs send
    # the identical text.
    text = post_to_kiosk.read_required_file(
        post_to_kiosk.MESSAGE_FILE, "alert text file"
    )
    post_to_kiosk.main()  # exits non-zero on any kiosk failure — chat never runs
    send_chat(text, dry_run)


if __name__ == "__main__":
    main()
