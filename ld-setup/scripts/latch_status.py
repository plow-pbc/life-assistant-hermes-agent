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

The key is `latch`, and only `latch`. It is the base seed's name for the relay
(plow-hermes-agent #2) and this repo's, and it is also the prefix on the tool
the model calls -- `mcp__latch__plow_run_command` -- so accepting a second
spelling here would answer "configured" for a build whose tool is registered
under a name the sheet does not use, and send the turn hunting for it.

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
import re
import sys

RELAY_KEY = "latch"


def config_path(env=None):
    env = os.environ if env is None else env
    home = env.get("HERMES_HOME") or "/var/lib/hermes"
    return os.path.join(home, "config.yaml")


def relay_configured(text):
    """True when config.yaml registers the relay to the owner's Mac.

    The stanza is written by the deployment, not by hand, and it always has the
    same shape -- a top-level `mcp_servers:` mapping with `latch:` as one of its
    keys and that server's settings under it:

        mcp_servers:
          latch:
            url: https://…

    So the question is exactly "does a two-space `latch:` key sit under
    `mcp_servers:`, with something below it", and it is answered by looking for
    that, not by parsing YAML. Standard library only: the sheet runs this as
    plain `python3`, and PyYAML lives in Hermes' own venv rather than the system
    interpreter in at least one build we ship -- an `import yaml` under
    /usr/bin/python3 once took down every container start. A probe that raises
    is worse than no probe, because the turn improvises.

    A key with nothing under it registers no tool: the url and the bearer are
    what make a server, so `latch:` alone is not a relay.
    """
    block = re.search(r"^mcp_servers:[ \t]*$\n((?:[ \t]+.*|[ \t]*)\n*)*",
                      text, re.MULTILINE)
    if not block:
        return False
    # The servers, and each one's settings beneath it. `latch:` with nothing
    # under it registers no tool -- the url and the bearer are what make a
    # server -- so the trailing group is required, not optional.
    match = re.search(
        r"^(?P<indent>[ \t]+)[\"']?latch[\"']?:[ \t]*$"
        r"(?:\n[ \t]*(?:#.*)?)*"
        r"\n(?P=indent)[ \t]+\S",
        block.group(0), re.MULTILINE)
    return match is not None


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
