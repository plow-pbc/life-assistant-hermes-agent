#!/usr/bin/env python3
"""post_priorities.py -- post ld-priorities' kiosk tile (card 6, type priorities).

Thin wrapper over `ld-shared/scripts/post_to_kiosk.py`, like every producer's:
sets MESSAGE_FILE + CARD + BODY_TYPE at import so tests/test_config_contract.py
and tests/test_wrappers.py can read them, and TITLE from the manifest's list
name at run time. `priorities.py post` imports the three constants from here
and posts in-process; this wrapper is what a standalone retry after a failed
send invokes instead.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "ld-shared", "scripts"))
from exclusive_lock import exclusive_lock  # noqa: E402
import post_to_kiosk  # noqa: E402

# The one declaration of all three; priorities.py imports them from here.
MESSAGE_FILE = "/var/lib/hermes/ld/priorities-text"
CARD = "6"
BODY_TYPE = "priorities"
post_to_kiosk.MESSAGE_FILE, post_to_kiosk.CARD, post_to_kiosk.BODY_TYPE = MESSAGE_FILE, CARD, BODY_TYPE


if __name__ == "__main__":
    # Python auto-inserts the invoked script's own directory, but that is the
    # INVOCATION path, not HERE's realpath -- explicit, so `import priorities`
    # still resolves if this is ever reached through a symlinked skills tree.
    sys.path.insert(0, HERE)
    import priorities  # noqa: E402

    # The lock `priorities.py post` holds while it recomposes MESSAGE_FILE.
    with exclusive_lock(priorities.MANIFEST, "refusing to post"):
        post_to_kiosk.TITLE = priorities.load()["name"]
        post_to_kiosk.main()
