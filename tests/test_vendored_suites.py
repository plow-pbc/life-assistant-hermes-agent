"""The vendored ld- suites run as subprocesses, because their exit code is the
only signal that can fail.

All three define `def test_*()` names, so pytest collects them on sight -- but
they record outcomes through a module-global `check()` counter and never raise.
Measured on this checkout with one assertion deliberately inverted: run as a
script the file reports "44 passed, 1 failed" and exits non-zero, while
`pytest -q ld-shared/scripts/test_post_to_kiosk.py` on the same bytes reports
"11 passed". A suite that cannot go red is worse than no suite, because it is
the one thing a green run is supposed to rule out.

So two things hold the line together and neither is sufficient alone: the
justfile scopes collection to tests/ so pytest never imports them as tests, and
this file runs each one the way its author intended -- one process, exit code as
the verdict -- surfacing the child's own summary line on failure.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

def _discover():
    """Every test_*.py outside tests/, found rather than listed.

    A hand-kept list has the same hole as the suites it runs: a new vendored
    suite is excluded from pytest by the tests/ scope AND absent from the list,
    so it silently never runs -- a suite that cannot go red by omission instead
    of by counter.

    Bounded to the repo root and the mounted skill trees rather than walking
    everything. An unbounded rglob EXECUTES what it finds, so a .venv or a
    vendored dependency tree would run hundreds of third-party suites as
    subprocesses, and an untracked scratch test_*.py an operator drops in the
    checkout would turn the suite red for no reason. These two globs are also
    exactly the surface compose.override.yml mounts, which is the boundary the
    rest of this repo already reasons about. Sorted so the ids are stable."""
    found = set(ROOT.glob("test_*.py"))
    for skill in ROOT.glob("ld-*"):
        if skill.is_dir():
            found |= set(skill.rglob("test_*.py"))
    return sorted(str(p.relative_to(ROOT)) for p in found)


SUITES = _discover()


@pytest.mark.parametrize("rel", SUITES)
def test_vendored_suite_passes(rel):
    script = ROOT / rel
    assert script.is_file(), f"{rel} is missing from the checkout"
    proc = subprocess.run(
        [sys.executable, str(script)], cwd=ROOT, capture_output=True, text=True
    )
    # stdout carries the child's per-check PASS/FAIL lines and its summary; show
    # the whole thing rather than a count, so a failure names which check broke.
    assert proc.returncode == 0, f"{rel} failed:\n{proc.stdout}\n{proc.stderr}"


def test_collection_is_scoped_away_from_the_vendored_suites():
    """The justfile must not run a bare `pytest`.

    This is the half of the guard that cannot be expressed in this file: if the
    recipe drops the tests/ path, pytest collects the suites above directly and
    every one of them reports green no matter what it found -- and the runner
    test would still pass beside them, so nothing here would notice.
    """
    text = (ROOT / "justfile").read_text()
    # The recipe BODY, not the file. A substring check over the whole text is
    # satisfied by the explanatory comment above the recipe, which quotes the
    # very string it looks for -- so reverting line 24 to a bare `pytest -q`
    # left this passing on the comment alone. Measured: it did.
    runs = [l for l in text.splitlines() if l[:1] in " \t" and "pytest" in l]
    assert runs, "no indented pytest invocation found in the justfile"
    for line in runs:
        assert line.rstrip().endswith("tests/"), (
            f"unscoped pytest in the test recipe: {line.strip()!r} -- an "
            "unscoped run collects the vendored suites, whose test functions "
            "never raise and therefore always report passed"
        )
