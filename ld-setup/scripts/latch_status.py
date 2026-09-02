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

def config_path(env=None):
    env = os.environ if env is None else env
    home = env.get("HERMES_HOME") or "/var/lib/hermes"
    return os.path.join(home, "config.yaml")


def relay_configured(text):
    """True when config.yaml registers the relay to the owner's Mac.

    The stanza is written by the deployment, never by hand, and it has exactly
    one shape -- a top-level `mcp_servers:` mapping, the server at two spaces,
    its settings deeper still:

        mcp_servers:
          latch:
            url: https://…

    So that is what this looks for, literally. An earlier version derived the
    servers' indent from whatever it found first and matched `latch:` at that
    level, which is a small YAML parser wearing a regex -- and it read a `latch:`
    nested inside another server's settings as the relay whenever a comment sat
    deeper than the keys. A parser that is nearly right about a file it does not
    have to parse is worse than a check that knows the one line it is looking
    for: this answers "unconfigured" for any shape it does not recognise, which
    costs a calendar step and never invents a Mac that is not there.

    Standard library only -- the sheet runs this as plain `python3`, and PyYAML
    lives in Hermes' own venv rather than the system interpreter in at least one
    build we ship. A `latch:` with nothing under it registers no tool: the url
    and the bearer are what make a server.
    """
    return re.search(
        r"^mcp_servers:[ \t]*$"          # the mapping, on its own line
        r"(?:\n[ \t]*(?:#.*)?)*"          # blanks and comments, any indent
        r"(?:\n  [^\s#][^\n]*"            # other servers at two spaces, and
        r"(?:\n(?:[ \t]*|    [^\n]*))*)*"  # their settings, deeper
        r"\n  [\"']?latch[\"']?:[ \t]*$"  # the relay, at two spaces
        r"(?:\n[ \t]*(?:#.*)?)*"          # blanks and comments
        r"\n    [^\s#]",                  # and at least one setting under it
        text, re.MULTILINE) is not None


def main(env=None):
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
