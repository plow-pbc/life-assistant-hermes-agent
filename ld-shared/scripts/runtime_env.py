"""runtime_env.py — the gateway's own env file, read the one shared way.

Two consumers, one parser: ld-dashboard's register_crons.py (expanding the
digest's delivery target) and ld-calendar-nudge's post_nudge.py (finding
its Plow Chat credentials). Both learned the same lesson in #24: a
`docker exec` session's env never carries the per-instance PLOW_CHAT_*
values — they live in /opt/data/.env, the file activation writes and the
gateway itself loads (measured: the first live registration refused on an
unset uid that sat one file-read away).
"""
from __future__ import annotations

import pathlib

DOTENV = "/opt/data/.env"


def dotenv_values(path=DOTENV):
    """Parse the dotenv with one spelling: NAME=value.

    No quoting, no `export`, no substitution -- the file is machine-written
    by activation in exactly this shape, and a second accepted spelling is a
    second thing that can drift. Absent file reads as empty: each caller's
    own refusal names what's missing either way.
    """
    try:
        lines = pathlib.Path(path).read_text().splitlines()
    except FileNotFoundError:
        return {}
    return {
        name: value
        for name, _, value in (line.partition("=") for line in lines)
        if name.isidentifier()  # a '#'-comment line fails this on its own
    }
