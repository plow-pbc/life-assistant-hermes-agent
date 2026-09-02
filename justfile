# life-assistant -- what only this agent has.
#
# The image is built and run the way the README says; see "Run locally". What
# is left here is `test`.
#
# Requires just >= 1.27, for the [doc("...")] attribute below. An older just
# does not degrade to a missing description -- it fails to parse the whole file.

# `pytest -q tests/` runs everything. The three suites that came from
# plow-pbc/life-dashboard-skills used to report through a counter and never
# raise, so they lived beside the code they test and a shim ran each as a
# subprocess to read its exit code; they are ordinary pytest files in tests/
# now, and the shim is gone.

[doc("Run the whole suite: this agent's contracts and the ld- producer suites.")]
test:
    uv run --no-project --python 3.13 --with pytest==8.4.2 --with pyyaml==6.0.2 pytest -q tests/
