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

Standard library only, and that is not an aesthetic. The sheet tells a turn to
run this with plain `python3`, and PyYAML lives in Hermes' own venv rather than
the system interpreter in at least one build we ship -- the e2e entrypoint
carries a comment about exactly that, from the day an `import yaml` under
/usr/bin/python3 took down every start. A probe that raises is worse than no
probe: the turn improvises, and improvising is what this file exists to stop.
So the one question it asks -- is there a relay key under mcp_servers -- is
answered by reading the indentation, not by parsing YAML.
"""
from __future__ import annotations

import os
import sys

RELAY_KEYS = ("plow", "latch")


def config_path(env=None):
    env = os.environ if env is None else env
    home = env.get("HERMES_HOME") or "/var/lib/hermes"
    return os.path.join(home, "config.yaml")


def relay_configured(text):
    """True when config.yaml registers a relay to the owner's Mac.

    A deliberately small YAML reader for one question. `mcp_servers:` is a
    top-level key, so it sits at column zero, and its servers are the keys
    indented directly under it. A relay key with nothing under it (`plow:` on
    its own, or `mcp_servers: {}`) registers no tool and is not a relay.

    Anything this cannot make sense of reads as no relay, which is the safe
    answer: the pitch and the link still go out, and no tool is reached for.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("mcp_servers:"):
            continue
        if line.split(":", 1)[1].strip() not in ("", "{}", "null", "~"):
            return False          # an inline mapping we will not parse, or a scalar
        child_indent = None
        for follower in lines[index + 1:]:
            if not follower.strip() or follower.lstrip().startswith("#"):
                continue
            indent = len(follower) - len(follower.lstrip())
            if indent == 0:
                break             # the next top-level key: the block is over
            if child_indent is None:
                child_indent = indent
            if indent != child_indent:
                continue          # deeper: a server's own settings, not its name
            name = follower.strip().split(":", 1)[0].strip().strip("\"'")
            if name in RELAY_KEYS and _has_body(lines, follower, child_indent):
                return True
        return False
    return False


def _has_body(lines, header, indent):
    """True when the server named by `header` has settings under it.

    `plow:` with nothing beneath it registers nothing -- the url and the bearer
    are what make a server -- so it is not a relay, and reading it as one would
    send the turn off to call a tool that is not there.
    """
    for follower in lines[lines.index(header) + 1:]:
        if not follower.strip() or follower.lstrip().startswith("#"):
            continue
        return len(follower) - len(follower.lstrip()) > indent
    return False


def main(argv=None, env=None):
    path = config_path(env)
    try:
        with open(path) as handle:
            text = handle.read()
    except FileNotFoundError:
        # No config to read is no relay configured. This is a status probe, not
        # a health check: it answers the question or says the safe thing.
        print("unconfigured")
        return 0
    except OSError as exc:
        raise SystemExit(f"refusing to report latch status: {exc}") from None

    print("configured" if relay_configured(text) else "unconfigured")
    return 0


if __name__ == "__main__":
    sys.exit(main())
