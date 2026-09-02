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

import ipaddress
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


def household_host(host):
    """True only for a host that can be on the owner's home network: an IP
    literal that is not globally routable (RFC1918, the 100.64/10 tailnet
    range, link-local) or a .local mDNS name. No bare-hostname fallback --
    curl reads a dotless numeric string like 134744072 as the public address
    8.8.8.8, so anything that is not a parseable IP or .local is refused.
    The wall's bearer rides every request to this host, so a public name
    here is exfiltration, not configuration. Shared by mint_wall_token.py
    (refusing the interview answer) and post_to_kiosk.py (refusing an
    injected dotenv line) so the two gates cannot drift."""
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return host.endswith(".local")


if __name__ == "__main__":
    # One key, for a caller that cannot import: the agent-index s6 service is
    # shell. Without this it grew its own grep/cut/tr parser, which took the
    # FIRST duplicate and stripped quotes while this one takes the last
    # literally -- two spellings of the same file, which is the drift the
    # module docstring exists to prevent.
    import sys

    print(dotenv_values().get(sys.argv[1], ""))
