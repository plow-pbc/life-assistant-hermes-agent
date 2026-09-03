#!/usr/bin/env python3
"""latch_status.py -- is a relay to the owner's Mac configured at all?

Prints one word on stdout: `configured` or `unconfigured`.

This exists so the sheet never has to name a tool that might not be there.
Whether this deployment can reach a Mac is a property of the image, not of the
conversation: either the relay is registered and its tools exist in the turn,
or it is not and no tool by that name does.

A model told to call a tool that is absent does not conclude "not connected".
It goes looking -- tool_search, again under another name, then the tool that
renders a blocking `?` -- and it narrates the hunt to the owner. Measured, all
three in one turn: "There's no Latch-specific tool search hit for
`plow_run_command`/`plow_write_file` -- let me check if those exist under a
different name", then a `clarify` call, then the sheet's own rule quoted back
to her. A terminal command, by contrast, always exists.

So the sheet asks THIS first, and names the relay's tools only in the branch
where they are registered.

The answer is `PLOW_MCP_URL`. First boot asks Plow who this agent is and
publishes the relay it is told about into the container environment; a tenant
with no relay gets no such variable, and the same value is what sets
`mcp_servers.plow.enabled` in the agent's config. One variable, one question,
and the turn inherits it from the gateway that started it.

This used to read the config file instead, with a hand-rolled scan standing in
for a YAML parser -- because PyYAML lives in Hermes' own venv rather than the
system interpreter the sheet invokes, and an `import yaml` under
/usr/bin/python3 once took down every container start. That scan answered the
same question two steps downstream of this variable: three earlier versions of
it were wrong in three different ways (a nesting misread, a regex CodeQL
flagged for exponential backtracking, a quoted-spelling scan), and each fix
was a parser in miniature judged against a file it did not have to parse. The
file is still the gateway's source of truth; it is just not the shortest way to
ask.
"""
from __future__ import annotations

import os
import sys


def main(env=None):
    """`configured` when first boot published a relay for this tenant.

    Blank counts as absent: a variable exported empty is nobody's answer, and
    unconfigured is the safe direction here -- it costs an install link the
    owner has already followed, where the other direction costs a tool call
    that cannot land.
    """
    env = os.environ if env is None else env
    print("configured" if (env.get("PLOW_MCP_URL") or "").strip() else "unconfigured")
    return 0


if __name__ == "__main__":
    sys.exit(main())
