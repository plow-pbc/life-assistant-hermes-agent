"""tests/test_payment_cap.py — behavior tests for the ld-payments daily cap.

The cap is the soft secondary ceiling; the assertion is the verdict a caller
reads (WITHIN / EXCEEDS) and the exit code, driven through the same CLI the
SKILL.md tells the agent to run — so a change to the cap value or the boundary
rule fails here rather than as a mis-decided payment.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ld-payments" / "scripts" / "payment_cap.py"


def _module():
    spec = importlib.util.spec_from_file_location("payment_cap", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(spent, amount):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--spent-today", str(spent),
         "--amount", str(amount)],
        capture_output=True, text=True,
    )


CAP = _module().DAILY_PAYMENT_CAP_USD


def test_the_v1_default_cap_is_200():
    # Pins the conservative v1 default the SKILL.md documents; a bump is a
    # deliberate edit, not a drift.
    assert CAP == 200.0


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


def test_within_cap_matches_the_cli_at_the_boundary():
    mod = _module()
    assert mod.within_cap(150, 50) is True
    assert mod.within_cap(150, 50.01) is False


@pytest.mark.parametrize(("spent", "amount"), [
    (-1, 50),
    (50, -1),
], ids=["negative-spent", "negative-amount"])
def test_negative_inputs_fail_loudly(spent, amount):
    # A negative amount is upstream drift, not a payment — exit loud, don't
    # silently treat it as a credit that frees up budget.
    r = run(spent, amount)
    assert r.returncode == 2
    assert r.stdout == ""


def test_missing_arguments_fail_loudly():
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--amount", "50"],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
