"""runtime_env.py — the gateway's own env file, read the one shared way.

Two consumers, one parser: ld-dashboard's register_crons.py (expanding the
digest's delivery target) and ld-calendar-nudge's post_nudge.py (finding
its Plow Chat credentials). Both learned the same lesson in #24: a
`docker exec` session's env never carries the per-instance PLOW_CHAT_*
values — they live in /var/lib/hermes/.env, the file activation writes and the
gateway itself loads (measured: the first live registration refused on an
unset uid that sat one file-read away).
"""
from __future__ import annotations

import ipaddress
import pathlib

# Hermes' own dotenv. Read for the gateway's local API key; the tenant values
# are NOT here.
#
# PLOW_API_BASE, PLOW_AGENT_TOKEN and PLOW_HOME_CHANNEL live in the container
# environment, which first boot fills from the credential the host dropped in
# and which the agent cannot write -- that, not this file's mode, is what stops
# a turn re-pointing the API base and sending its own bearer somewhere else.
DOTENV = "/var/lib/hermes/.env"

# The agent's own file, in the instance directory it already owns. Everything
# the agent itself records after setup lives here -- the wall's endpoint and
# token, the relay pair its owner minted -- because those are the agent's to
# write and the tenant's credential is not.
#
# The split is a boundary, not a second copy: no name appears in both, nothing
# running as root reads this file, and what it holds stays UNTRUSTED. An
# endpoint read from here is still held to the household-network gate before
# anything hands it a bearer.
AGENT_DOTENV = "/var/lib/hermes/ld/.env"


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


def agent_values(path=AGENT_DOTENV):
    """The agent's own file, parsed the same one way. Absent reads as empty:
    an agent whose owner has not finished setup has written nothing yet."""
    return dotenv_values(path)


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
