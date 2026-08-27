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

# Every vendored suite in the repo. A new one added under ld-shared/ or beside a
# producer belongs here: nothing else runs it, because collection is scoped away
# from both locations.
SUITES = [
    "ld-shared/scripts/test_post_to_kiosk.py",
    "ld-shared/scripts/test_ld_config_gate.py",
    "test_wrappers.py",
]


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
    recipe = (ROOT / "justfile").read_text()
    assert "pytest -q tests/" in recipe, (
        "the test recipe must scope pytest to tests/ -- an unscoped run collects "
        "the vendored suites, whose test functions never raise and therefore "
        "always report passed"
    )
