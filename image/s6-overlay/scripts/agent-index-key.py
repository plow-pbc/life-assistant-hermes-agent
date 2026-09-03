#!/usr/bin/env python3
"""Trade this container's Plow token for an index-scoped key, once.

The Plow token authorises far more than reporting usage: chats, the Latch relay
and inference all answer to it. Handing it to the index every hour makes an
index-side compromise worth the whole agent, for a job whose entire need is
"add these numbers to a page".

So it crosses to the index exactly once, to POST /v1/keys, and what comes back
is an `aik_` key the index itself refuses for anything that takes an identity --
it can report usage and manage its own stories, and cannot claim an agent id or
mint another key. The reporter is then started with only that key in its
environment, and the Plow bearer never reaches it at all.

Written next to the agent's home so it survives a container replacement: a key
re-minted on every boot would leave a trail of live credentials nobody revokes.
"""
import json
import os
import sys
import urllib.error
import urllib.request

INDEX = os.environ.get("AGENT_INDEX_API", "https://agent-index-server.vercel.app")
KEY_PATH = os.environ.get("AGENT_INDEX_KEY_PATH", "/var/lib/hermes/.agent-index/token")
AGENT_KEY_PREFIX = "aik_"


def stored():
    """A key we already hold, or None. Anything that is not one of ours is not
    usable and not ours to keep -- the client deletes such a file on sight, and
    this refuses to hand it on."""
    try:
        with open(KEY_PATH) as handle:
            value = handle.read().strip()
    except OSError:
        return None
    return value if value.startswith(AGENT_KEY_PREFIX) else None


def mint(plow_token):
    """Exchange the Plow token for an index key. Shown once by the index, so a
    failure to store it is a failure of the whole exchange."""
    request = urllib.request.Request(
        f"{INDEX}/v1/keys",
        data=json.dumps({"label": f"reporter for {os.environ.get('AGENT_ID', 'an agent')}"}).encode(),
        headers={"authorization": f"Bearer {plow_token}", "content-type": "application/json",
                 "accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        answer = json.loads(response.read() or b"{}")
    key = answer.get("key")
    if not isinstance(key, str) or not key.startswith(AGENT_KEY_PREFIX):
        raise ValueError("the index did not return a usable key")
    return key


def store(key):
    """0600, and written before it is used: a key we hold but cannot save is a
    live credential nobody can revoke, because nothing recorded that it exists."""
    os.makedirs(os.path.dirname(KEY_PATH), mode=0o700, exist_ok=True)
    descriptor = os.open(KEY_PATH + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(key)
    os.replace(KEY_PATH + ".tmp", KEY_PATH)


def main():
    """Print the key the reporter should use, or exit non-zero saying why."""
    key = stored()
    if key:
        print(key)
        return 0

    plow_token = os.environ.get("PLOW_AGENT_TOKEN", "")
    if not plow_token:
        print("agent-index: no PLOW_AGENT_TOKEN to exchange", file=sys.stderr)
        return 1
    try:
        key = mint(plow_token)
    except (urllib.error.URLError, ValueError, json.JSONDecodeError) as failure:
        # The index being unreachable is not a reason to fall back to the Plow
        # token: that is the exposure this exists to remove, and the next hour
        # will try again.
        print(f"agent-index: could not exchange the Plow token for an index key: "
              f"{type(failure).__name__}", file=sys.stderr)
        return 1
    store(key)
    print(key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
