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

    So the relay's line is compared with `  latch:` and nothing cleverer. Three
    earlier versions tried to be: an indent walk that read a `latch:` nested in
    another server's settings as the relay whenever a comment sat deeper than
    the keys; a regex that nested a repetition inside a repetition, which CodeQL
    flagged as exponential backtracking (py/redos), so a config.yaml full of
    blank indented lines would have hung the probe -- and a probe that hangs is
    a turn that improvises; and a key-parsing scan that accepted quoted spellings
    the deployment does not write.

    Each of those was a YAML parser in miniature, judged against a file this
    does not have to parse. One string comparison cannot backtrack, cannot
    misread a nesting level, and is legible without running it.

    The comparison is against the RAW line, trailing spaces and all. `  latch: `
    is valid YAML meaning the same thing, and this reports it as unconfigured --
    deliberately, because the deployment writes this file with yaml.safe_dump
    and never emits a trailing space, so a line that has one was edited by hand
    and is not the stanza this recognises. Erring that way costs a calendar step
    and an install link the owner has already followed; erring the other way
    calls a tool that may not be registered, which is where every ❓ in this
    branch's history came from.

    Standard library only -- the sheet runs this as plain `python3`, and PyYAML
    lives in Hermes' own venv rather than the system interpreter in at least one
    build we ship. A `latch:` with nothing under it registers no tool: the url
    and the bearer are what make a server.
    """
    lines = text.splitlines()
    inside = False
    for index, line in enumerate(lines):
        if not inside:
            inside = line.rstrip() == "mcp_servers:"
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue                      # blanks and comments, at any indent
        if not line.startswith(" "):
            return False                  # the next top-level key: block over
        if line != "  latch:":
            continue                      # some other server, or its settings
        # The relay, named at the servers' level. It counts only with settings
        # under it -- `latch:` alone registers nothing.
        for follower in lines[index + 1:]:
            if not follower.strip() or follower.lstrip().startswith("#"):
                continue
            # Anything indented deeper than the server's own level is under it.
            return len(follower) - len(follower.lstrip()) > 2
        return False
    return False


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
