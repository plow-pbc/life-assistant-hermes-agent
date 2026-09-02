#!/usr/bin/env bash
# Mint this loop's agent credential and its chat, and write scripts/e2e/.env.
#
# This is the real activation flow -- the same one `agent-mgr activate` drives
# and the same one an owner completes from their phone -- run against the local
# stack, where the twin plays the phone network so nobody has to text anything.
# It hands back BOTH halves the agent needs: the scoped bearer and the chat it
# is granted, so no credential is minted by hand and no row is written directly.
#
# The $100 welcome credit rides along: a user created on this path is created
# with one (webhook.py, WELCOME_CREDIT_USD), and without a positive balance
# every model call comes back 402 Insufficient balance. Nothing to seed.
#
# usage: activate.sh [handset]
#   ACTIVATE_TRIES   attempts before giving up (default 1 -- read why below)
#   LINE_TIMEOUT     seconds to wait for one attempt's redeem (default 20)
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

API="${PLOW_API_HOST_BASE:-https://api.plow.orb.local}"
TWIN="${TWIN_HOST_BASE:-https://dtu-linq.plow.orb.local}"

# A handset given here pins every attempt to it -- for reusing one already
# known to work. Left off (the normal case) every attempt gets a fresh one.
FIXED_MEMBER="${1:-}"

python3 - "$API" "$TWIN" "$E2E_DIR/.env" "$FIXED_MEMBER" <<'PY'
"""Retry activation until a (line, handset) pair provisions cleanly.

The line is NOT ours to choose. `POST /v1/auth/activate` picks it itself --
`secrets.choice(pool)` in auth_routes/router.py -- so there is no field to ask
for one and no way to walk the pool deliberately. What an attempt gets is
whichever line the server rolled.

That matters because in a long-lived dev DB some (line, roster) pairs are
poisoned: if the rolled line already carries a chat owned by ANOTHER account
with the same member roster, provisioning raises CrossOwnerCollisionError, the
redeem stays "pending" forever, and the twin retries the failing webhook in a
loop. The old script made exactly one attempt and told the operator to guess a
different handset by hand.

A miss is "no verified redeem in LINE_TIMEOUT", which is the shape this failure
actually takes: not an error response, just a redeem that never leaves
"pending".

RETRYING IS EXPENSIVE, AND NOT TO US. This defaults to ONE attempt.

A miss does not simply expire. The inbound that failed to provision leaves a
webhook delivery in the TWIN's fanout queue that answers 500 forever, and that
queue is ordered: the stuck delivery head-of-line blocks EVERY chat on the twin,
not just this activation, until somebody restarts the container. So a retry loop
does not merely waste attempts -- each miss degrades the shared stack a little
further for everyone using it, and after the first miss the queue is already
blocked, which means no later attempt's inbound can reach Plow at all. The
retries cannot succeed; they can only add more stuck deliveries.

An earlier version of this file claimed an abandoned attempt "expires on its own
-- no row is touched". That was wrong, and it was wrong in the direction that
does damage.

If activation misses:

  1. Look, before retrying:   docker logs plow-api-1 | grep -i crossowner
  2. Clearing the wedged queue costs everyone on the stack a restart of
     plow-dtu-linq-1, so it is the head chef's call, not this script's:
         docker restart plow-dtu-linq-1
  3. Only then try again. ACTIVATE_TRIES=n raises the attempt count, and is
     worth using only against a freshly restarted twin -- on a wedged one the
     extra attempts are pure cost.
"""
import json
import random
import os
import sys
import time
import urllib.error
import urllib.request

api, twin, dest, fixed_member = sys.argv[1:5]

# ONE. Every miss wedges the twin's fanout queue for every chat on the stack
# (see the module docstring), so looping is not this script's decision to make.
TRIES = int(os.environ.get("ACTIVATE_TRIES", "1"))
LINE_TIMEOUT = float(os.environ.get("LINE_TIMEOUT", "20"))
POLL_EVERY = 2.0


def post(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def handset():
    """A member phone in the range the loop uses, outside the managed pool."""
    return fixed_member or "+1555765%04d" % random.randrange(10000)


def attempt(n):
    """One activation. Returns the winning details, or None for a miss."""
    try:
        activation = post(f"{api}/v1/auth/activate",
                          {"name": "e2e-onboarding-v2", "provision_chat": True})
    except urllib.error.HTTPError as exc:
        print(f"  {n}: activate failed -- HTTP {exc.code} {exc.read()[:200]!r}")
        return None

    line = activation.get("send_to")
    member = handset()
    # The line is logged on every attempt, hit or miss: which lines keep losing
    # is the only signal anyone has about which are poisoned, and it is not
    # visible anywhere else without reading the API's logs.
    print(f"  {n}: line {line} / handset {member} ... ", end="", flush=True)

    # The code goes in through the twin, never at Plow: POST
    # /channels/linq/event is HMAC-verified and only the twin holds the secret.
    try:
        post(f"{twin}/ui/inbound", {"to_phone": line, "remote_phone": member,
                                    "text": activation["display_code"]})
    except urllib.error.HTTPError as exc:
        print(f"twin refused the inbound (HTTP {exc.code})")
        return None

    deadline = time.time() + LINE_TIMEOUT
    last = ""
    while time.time() < deadline:
        time.sleep(POLL_EVERY)
        try:
            redeem = post(f"{api}/v1/auth/activate/redeem",
                          {"activation_secret": activation["activation_secret"]})
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            continue
        last = redeem.get("status", "")
        if last == "verified":
            print("verified")
            return {"redeem": redeem, "line": line, "member": member}
    print(f"no verify in {LINE_TIMEOUT:.0f}s (last: {last or 'none'})")
    return None


won = None
print(f"activating -- {TRIES} attempt{'' if TRIES == 1 else 's'}, "
      + (f"handset pinned to {fixed_member}" if fixed_member else
         "a fresh handset" + ("" if TRIES == 1 else " each")))
for n in range(1, TRIES + 1):
    won = attempt(n)
    if won:
        break

if not won:
    # Flushed first: the attempt lines go to stdout and this goes to stderr, and
    # unflushed stdout would print the last miss AFTER the explanation of it.
    sys.stdout.flush()
    plural = "one attempt" if TRIES == 1 else f"{TRIES} attempts"
    print(f"\nactivation did not complete in {plural}.", file=sys.stderr)
    print("", file=sys.stderr)
    print("That miss has almost certainly left a 500ing delivery in the twin's", file=sys.stderr)
    print("fanout queue, which blocks delivery for EVERY chat on this stack --", file=sys.stderr)
    print("so do not just run this again. In order:", file=sys.stderr)
    print("", file=sys.stderr)
    print("  1. docker logs plow-api-1 | grep -i crossowner", file=sys.stderr)
    print("  2. ask the head chef to restart the twin (it is shared):", file=sys.stderr)
    print("       docker restart plow-dtu-linq-1", file=sys.stderr)
    print("  3. then activate again -- against a wedged twin no attempt can", file=sys.stderr)
    print("     succeed, because the inbound never reaches Plow.", file=sys.stderr)
    if fixed_member:
        print("", file=sys.stderr)
        print(f"You pinned the handset ({fixed_member}). The collision is on the", file=sys.stderr)
        print("(line, roster) pair, so pinning cannot escape one -- drop the", file=sys.stderr)
        print("argument once the twin is healthy.", file=sys.stderr)
    raise SystemExit(1)

d = won["redeem"]
token, chat = d["token"], d["chat"]
mine = {
    "PLOW_API_HOST_BASE": api,
    "TWIN_HOST_BASE": twin,
    "PLOW_API_BASE": api,
    "PLOW_AGENT_TOKEN": token,
    "HERMES_CUSTOM_PLOW_API_KEY": token,
    "PLOW_HOME_CHANNEL": chat["uid"],
    "TWIN_THREAD": chat["provider_key"],
    # The line the winning attempt landed on. Recorded because nothing else
    # remembers it: the next run rolls again, and anyone reading this file to
    # find out where the chat lives would otherwise have to go to the API.
    "LINE_PHONE": won["line"],
    "MEMBER_PHONE": won["member"],
    "TZ": "America/Los_Angeles",
}
# MERGE, never clobber. This file is also where a person hands the loop things
# no activation can regenerate -- a Latch device URL and its token, say -- and
# an earlier version of this script rewrote the whole file, so re-running it
# silently destroyed them with nothing to restore from. Keys this script owns
# are replaced; every other line survives, comments and order included.
kept, seen = [], set()
if os.path.exists(dest):
    for raw in open(dest):
        stripped = raw.strip()
        key = stripped.split("=", 1)[0] if "=" in stripped and not stripped.startswith("#") else None
        if key in mine:
            seen.add(key)
            kept.append(f"{key}={mine[key]}\n")
        else:
            kept.append(raw if raw.endswith("\n") else raw + "\n")
kept += [f"{k}={v}\n" for k, v in mine.items() if k not in seen]
with open(dest, "w") as f:
    f.writelines(kept)
print(f"wrote {dest}: line {won['line']} / chat {chat['uid']} / twin thread {chat['provider_key']}")
extra = sorted(k for k in (l.split("=", 1)[0].strip() for l in kept if "=" in l and not l.startswith("#")) if k not in mine)
if extra:
    print("kept, untouched: " + ", ".join(extra))
PY
chmod 600 "$E2E_DIR/.env"
