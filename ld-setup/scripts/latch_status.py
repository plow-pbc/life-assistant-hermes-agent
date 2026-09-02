#!/usr/bin/env python3
"""latch_status.py -- is a relay to the owner's Mac configured at all?

Prints one word on stdout: `configured` or `unconfigured`.

This exists so the sheet never has to name a tool that might not be there.
Whether this deployment can reach a Mac is a property of the image, not of the
conversation: `mcp_servers` in the agent's own config.yaml either holds the
relay or it does not, and when it does not, the relay's tools are not
registered and no tool by that name exists in the turn.

A model told to call a tool that is absent does not conclude "not connected".
It goes looking -- tool_search, again under another name, then the tool that
renders a blocking `❓` -- and it narrates the hunt to the owner. Measured, all
three in one turn: "There's no Latch-specific tool search hit for
`plow_run_command`/`plow_write_file` -- let me check if those exist under a
different name", then a `clarify` call, then the sheet's own rule quoted back
to her. A terminal command, by contrast, always exists.

So the sheet asks THIS first, and names the relay's tools only in the branch
where they are registered.

`plow` and `latch` are both accepted: the key is the server's name, the cloud
image and this repo have disagreed about it before, and a status script that
answers "unconfigured" because the key was renamed would take the whole
calendar step out of the run with nothing in the log to say why.
"""
from __future__ import annotations

import os
import sys

RELAY_KEYS = ("plow", "latch")


def config_path(env=None):
    env = os.environ if env is None else env
    home = env.get("HERMES_HOME") or "/var/lib/hermes"
    return os.path.join(home, "config.yaml")


def relay_configured(config):
    """True when config.yaml registers a relay to the owner's Mac.

    Anything that is not a mapping of server names -- absent, null, an empty
    mapping, or a scalar someone typed by hand -- is "no relay". The question
    this answers is whether a tool will be there, and only a populated mapping
    puts one there.
    """
    servers = config.get("mcp_servers") if isinstance(config, dict) else None
    if not isinstance(servers, dict):
        return False
    return any(servers.get(key) for key in RELAY_KEYS)


def main(argv=None, env=None):
    import yaml  # deferred: the message matters more than the import site

    path = config_path(env)
    try:
        with open(path) as handle:
            config = yaml.safe_load(handle)
    except FileNotFoundError:
        # No config to read is no relay configured. This is a status probe, not
        # a health check: it answers the question or says the safe thing.
        print("unconfigured")
        return 0
    except (OSError, yaml.YAMLError) as exc:
        raise SystemExit(f"refusing to report latch status: {exc}") from None

    print("configured" if relay_configured(config) else "unconfigured")
    return 0


if __name__ == "__main__":
    sys.exit(main())
