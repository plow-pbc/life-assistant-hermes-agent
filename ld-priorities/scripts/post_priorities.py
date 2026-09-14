#!/usr/bin/env python3
"""post_priorities.py -- post ld-priorities' kiosk tile (card 6, type priorities).

Thin wrapper over `ld-shared/scripts/post_to_kiosk.py`, like every producer's:
sets MESSAGE_FILE + CARD + BODY_TYPE at import so tests/test_config_contract.py
and tests/test_wrappers.py can read them, and TITLE from the manifest's list
name at run time. `priorities.py post` composes the tile into MESSAGE_FILE and
posts in-process (it needs the same constants live for its own --dry-run
tests, which rebind priorities.MESSAGE_FILE rather than this file's literal);
this wrapper is what a standalone retry after a failed send invokes instead.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "ld-shared", "scripts"))
import post_to_kiosk  # noqa: E402

post_to_kiosk.MESSAGE_FILE = "/var/lib/hermes/ld/priorities-text"
post_to_kiosk.CARD = "6"
post_to_kiosk.BODY_TYPE = "priorities"


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    import priorities  # noqa: E402

    post_to_kiosk.TITLE = priorities.load()["name"]
    post_to_kiosk.main()
