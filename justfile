# life-assistant -- what only this agent has.
#
# Deployment lives in plow-pbc/agent-mgr, which owns the compose service, the
# bring-up, the pins and the contract tests for every agent on this host.
# <agent> below is the registry name of a registered instance. `life` is the
# only one that may be registered today -- see README "Adding a second instance":
#
#   agent-mgr deploy <agent>      # config and the Plow Chat plugin (skills.tsv is empty)
#   agent-mgr activate <agent>    # prints a code; its OWNER texts it, from their phone
#   agent-mgr up <agent>          # down / restart / logs
#   agent-mgr sign-in <agent>     # device-code OAuth; hand the URL to its owner
#   agent-mgr agent <agent> "what's the weather?"
#
#   # The dashboard crons -- NOT replayed by deploy, so bring-up is not done
#   # without them. Deliberately NOT restated here: the exact turn, the paste
#   # request, the exit-code caveat and the exec form for scripted callers are
#   # README "Bring-up". A third copy is how the first two drifted.
#
# No check-connectors: this instance installs no plow-connectors. See README
# "No connectors, and what that costs".
#
# Eleven recipes here re-implemented those. What is left is one recipe that does
# something agent-mgr does not yet do -- see below -- and `test`.
#
# Requires just >= 1.27, for the [doc("...")] attributes below. An older just
# does not degrade to a missing description -- it fails to parse the whole file,
# so EVERY recipe stops working, `check-latch` included. This repo is shared by
# every instance, so that lands on an owner's Mac whose just nobody checked.

# `pytest -q tests/` runs everything. The three suites that came from
# plow-pbc/life-dashboard-skills used to report through a counter and never
# raise, so they lived beside the code they test and a shim ran each as a
# subprocess to read its exit code; they are ordinary pytest files in tests/
# now, and the shim is gone.

[doc("Run the whole suite: this agent's contracts and the ld- producer suites.")]
test:
    uv run --no-project --python 3.13 --with pytest==8.4.2 --with pyyaml==6.0.2 pytest -q tests/
