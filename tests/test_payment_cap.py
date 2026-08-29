"""tests/test_payment_cap.py — behavior tests for the advisory daily guideline.

The assertion is the advisory verdict a caller reads (WITHIN / EXCEEDS) and the
exit code, driven through the same CLI the SKILL.md tells the agent to run — so
a change to the cap value or the boundary rule fails here rather than as a
mis-decided payment.
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ld-payments" / "scripts" / "payment_cap.py"


def run(spent, amount):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--spent-today", str(spent),
         "--amount", str(amount)],
        capture_output=True, text=True,
    )


@pytest.mark.parametrize(("spent", "amount", "verdict"), [
    (0, 50, "WITHIN"),
    (150, 50, "WITHIN"),     # lands exactly on the cap — at-or-under is within
    (150, 50.01, "EXCEEDS"),  # a cent over the cap
    (0, 200, "WITHIN"),
    (0, 200.01, "EXCEEDS"),
    (200, 0.01, "EXCEEDS"),   # the day is already at the cap
    (0, 0, "WITHIN"),
], ids=["fresh", "exactly-at-cap", "cent-over", "single-at-cap",
        "single-over", "already-maxed", "zero"])
def test_verdict_is_the_first_token_and_the_boundary_is_inclusive(spent, amount, verdict):
    r = run(spent, amount)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[0] == verdict


@pytest.mark.parametrize(("spent", "amount"), [
    (-1, 50),
    (50, -1),
    ("nan", 50),
    (50, "inf"),
    ("-inf", 50),
], ids=["negative-spent", "negative-amount", "nan-spent", "inf-amount",
        "neg-inf-spent"])
def test_malformed_inputs_fail_loudly(spent, amount):
    # A negative or non-finite amount is upstream drift, not a payment — exit
    # loud (a controlled exit 2, never a raw traceback), don't silently treat a
    # negative as a credit that frees up budget or let nan/inf crash the check.
    r = run(spent, amount)
    assert r.returncode == 2
    assert r.stdout == ""


def test_missing_arguments_fail_loudly():
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--amount", "50"],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
