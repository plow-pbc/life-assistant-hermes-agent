#!/usr/bin/env python3
"""Equivalence test for the shared python3 ld-config gate (ld_config_gate.py).

The gate runs ON THE PI, where jq is not provisioned; it replaces the jq filter
that each seed's install + verify steps used to carry verbatim. This test pins
the gate's output to the EXACT contract of that old jq filter:

  - the documented outcomes (issue #9): valid → pass; blank owner name; blank/
    missing family.timezone; sources not a non-empty array; blank source account;
    leftover placeholder; invalid JSON.
  - whenever jq IS available on the test machine (it is on the dev box; it is
    NOT on the Pi), every fixture below is ALSO run through the original jq
    filter and asserted byte-identical — proving the port is faithful, not just
    that it self-agrees.

`just test` does not require jq; the jq cross-check is skipped (with a printed
note) when jq is absent so the suite still runs Pi-side.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GATE = Path(__file__).resolve().parent / "ld_config_gate.py"

# The ORIGINAL jq filter, verbatim from the seeds' install-time ld_config_gate()
# and verify-time v-ld-config — the byte-for-byte spec the python gate must match.
JQ_FILTER = r"""
    [ if ((.family.owner.name     // "") | test("\\S")) then empty else "family.owner.name is blank" end,
      if ((.family.timezone       // "") | test("\\S")) then empty else "family.timezone is blank" end,
      if ((.calendar.sources | type) == "array" and (.calendar.sources | length) >= 1)
        then empty else "calendar.sources is not a non-empty array" end,
      if ([.calendar.sources[]? | select(((.account // "") | test("\\S")) | not)] | length) == 0
        then empty else "a calendar.sources[].account is blank" end,
      if ([.. | strings | select(test("^\\[[A-Z][A-Z0-9_]*\\]$"))] | length) == 0
        then empty else "an unfilled [UPPER_SNAKE] placeholder remains" end
    ] | join("; ")
"""

VALID = {
    "family": {"owner": {"name": "Sam"}, "timezone": "America/Los_Angeles"},
    "calendar": {"sources": [{"account": "owner@example.test", "calendar_id": "primary", "name": "Personal"}]},
    "weather": {"location": "Mountain View", "lat": 37.386, "lon": -122.083},
}

# (label, raw-bytes-written-to-file, expected-output, jq_crosscheck) — raw bytes
# so we can feed malformed JSON too. Expected outputs are the documented (a)-(f)
# outcomes. jq_crosscheck=False on the lone case where we DELIBERATELY diverge
# from jq's accidental behavior (see the empty-file case).
CASES = [
    ("(a) fully valid", json.dumps(VALID), "", True),
    ("(b) blank owner name",
     json.dumps({**VALID, "family": {**VALID["family"], "owner": {"name": "   "}}}),
     "family.owner.name is blank", True),
    ("(b') missing owner name",
     json.dumps({**VALID, "family": {**VALID["family"], "owner": {}}}),
     "family.owner.name is blank", True),
    ("(c) sources empty array",
     json.dumps({**VALID, "calendar": {"sources": []}}),
     "calendar.sources is not a non-empty array", True),
    ("(c') sources missing",
     json.dumps({"family": VALID["family"]}),
     "calendar.sources is not a non-empty array", True),
    ("(c'') sources non-array",
     json.dumps({**VALID, "calendar": {"sources": "primary"}}),
     "calendar.sources is not a non-empty array", True),
    ("(d) blank source account",
     json.dumps({**VALID, "calendar": {"sources": [{"account": "  ", "name": "Personal"}]}}),
     "a calendar.sources[].account is blank", True),
    ("(d') missing source account",
     json.dumps({**VALID, "calendar": {"sources": [{"name": "Personal"}]}}),
     "a calendar.sources[].account is blank", True),
    ("(e) leftover placeholder (account)",
     json.dumps({**VALID, "calendar": {"sources": [{"account": "[OWNER_EMAIL]"}]}}),
     "an unfilled [UPPER_SNAKE] placeholder remains", True),
    ("(e') leftover placeholder (nested)",
     json.dumps({**VALID, "weather": {"location": "[CITY_NAME]"}}),
     "an unfilled [UPPER_SNAKE] placeholder remains", True),
    ("(f) blank timezone",
     json.dumps({**VALID, "family": {**VALID["family"], "timezone": "   "}}),
     "family.timezone is blank", True),
    ("(f') missing timezone",
     json.dumps({**VALID, "family": {"owner": {"name": "Sam"}}}),
     "family.timezone is blank", True),
    ("(g) invalid JSON", "{ not json", "not valid JSON", True),
    # An empty file is the lone DELIBERATE divergence: jq with no input emits
    # nothing and exits 0, so the old gate fail-OPEN-passed an empty config (a
    # latent bug — verify.sh's own `jq -e .` pre-check already rejected it). The
    # python gate fail-CLOSES it as "not valid JSON" (empty is not valid JSON),
    # which is strictly safer; we skip the jq cross-check for this one case only.
    ("(g') empty file → fail-closed (diverges from jq's fail-open)",
     "", "not valid JSON", False),
    # multiple simultaneous failures join with "; " in filter order.
    ("multi: blank name + missing tz + empty sources",
     json.dumps({"family": {"owner": {"name": ""}}, "calendar": {"sources": []}}),
     "family.owner.name is blank; family.timezone is blank; calendar.sources is not a non-empty array", True),
    # jq // "" semantics: a false value coalesces to "" (blank), not an error.
    ("name false → blank",
     json.dumps({**VALID, "family": {**VALID["family"], "owner": {"name": False}}}),
     "family.owner.name is blank", True),
    # jq errors (caught → "not valid JSON") on indexing a non-object / non-string test.
    ("name is a number → not valid JSON",
     json.dumps({**VALID, "family": {**VALID["family"], "owner": {"name": 5}}}),
     "not valid JSON", True),
    ("family is non-object → not valid JSON",
     json.dumps({**VALID, "family": "oops"}),
     "not valid JSON", True),
    ("a source is non-object → not valid JSON",
     json.dumps({**VALID, "calendar": {"sources": ["x"]}}),
     "not valid JSON", True),
    # A blank-account object FOLLOWED by a non-object element: jq's `?` suppresses
    # only the `.[]` iteration error, so it still evaluates `"x".account` and the
    # whole filter collapses to "not valid JSON" — it does NOT short-circuit on the
    # earlier blank account. Pins the gate to sweep ALL elements (no early break).
    ("blank account then non-object source → not valid JSON",
     json.dumps({**VALID, "calendar": {"sources": [{"account": "  "}, "x"]}}),
     "not valid JSON", True),
    ("top-level array → not valid JSON", json.dumps([1, 2, 3]), "not valid JSON", True),
]


def _run_on_tempfile(runner, raw):
    """Write raw to a temp .json, run `runner + [path]`, return (returncode, stdout).

    stdout is stripped of one trailing newline — both gates print exactly one,
    so this normalizes them for comparison.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        f.write(raw)
        path = f.name
    try:
        proc = subprocess.run(runner + [path], capture_output=True, text=True)
        return proc.returncode, proc.stdout.rstrip("\n")
    finally:
        Path(path).unlink(missing_ok=True)


def run_gate(runner, raw):
    """Run a gate command (list prefix) on raw bytes; return its verdict line."""
    rc, out = _run_on_tempfile(runner, raw)
    # The python gate returns 0 for every config verdict (valid / failures /
    # "not valid JSON"); a non-zero exit is a crash or usage bug, never a
    # verdict. Surface it so it can't masquerade as an empty-stdout PASS.
    return out if rc == 0 else f"<gate exited nonzero: {rc}>"


def jq_gate(raw):
    """The original jq filter, wrapped exactly as the shell gate wrapped it."""
    rc, out = _run_on_tempfile(["jq", "-r", JQ_FILTER], raw)
    return out if rc == 0 else "not valid JSON"  # the `|| echo "not valid JSON"` arm


def main():
    py_runner = [sys.executable, str(GATE)]
    have_jq = shutil.which("jq") is not None
    passed = failed = 0

    if not have_jq:
        print("NOTE: jq not present — running spec assertions only (Pi-side mode), "
              "skipping the byte-for-byte jq cross-check.")

    for label, raw, expected, jq_crosscheck in CASES:
        got = run_gate(py_runner, raw)
        if got == expected:
            passed += 1
            print(f"PASS - {label}: {got!r}")
        else:
            failed += 1
            print(f"FAIL - {label}: expected {expected!r}, got {got!r}")

        if have_jq and jq_crosscheck:
            jq_out = jq_gate(raw)
            if got == jq_out:
                passed += 1
                print(f"PASS - {label} [py==jq]: {jq_out!r}")
            else:
                failed += 1
                print(f"FAIL - {label} [py==jq]: python {got!r} != jq {jq_out!r}")

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
