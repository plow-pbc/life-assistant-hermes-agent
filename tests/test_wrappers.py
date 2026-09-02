#!/usr/bin/env python3
"""Wrapper-contract test for the ld- producers this repo ships.

The shared POST helper (post_to_kiosk.py) and its own tests came from
plow-pbc/life-dashboard-skills and now live in-tree as ld-shared/ — there is no
sync step, so this runs against the checkout as-is. It verifies the part each
producer owns: its thin wrapper sets the right CARD / BODY_TYPE on the shared
module at import, per the pinned producer→card mapping the viewer renders
(1=alert, 2=affirmation, 3=weather, 4=digest, 5=sports). On Hermes every
producer — including weather and sports — runs as an LLM cron job that calls a
Python wrapper.

Six rows, one per producer wrapper: weather and sports (public feeds),
morning-triage (the Mac's iMessage DB through Latch), and the three calendar
producers (morning-updates, weekly-digest, calendar-nudge) on Latch's vendored
gog.

Each wrapper runs in a fresh interpreter: an in-process import would find
post_to_kiosk already in sys.modules and mask a broken relative sys.path in the
wrapper. A subprocess makes the wrapper's import path actually load-bearing.
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


SNIPPET = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('wrapper', sys.argv[1])\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    "import post_to_kiosk\n"
    "print(post_to_kiosk.CARD)\n"
    "print(post_to_kiosk.BODY_TYPE)\n"
)

WRAPPERS = (
    ("ld-morning-triage/scripts/post_alert.py", "1", "alert"),
    ("ld-calendar-nudge/scripts/post_nudge.py", "1", "alert"),
    ("ld-morning-updates/scripts/post_message.py", "2", "affirmation"),
    ("ld-weather/scripts/post_weather.py", "3", "weather"),
    ("ld-weekly-digest/scripts/post_digest.py", "4", "digest"),
    ("ld-sports/scripts/post_sports.py", "5", "sports"),
)


def test_the_shared_helper_is_in_the_checkout():
    """Every wrapper imports it off its own relative sys.path; without it the
    per-wrapper tests below would fail for a reason that is not theirs."""
    assert (REPO_ROOT / "ld-shared" / "scripts" / "post_to_kiosk.py").exists()


@pytest.mark.parametrize(
    "rel_path,expected_card,expected_type", WRAPPERS,
    ids=[w[0].split("/")[0] for w in WRAPPERS])
def test_each_wrapper_imports_and_declares_its_card(
        rel_path, expected_card, expected_type):
    """The contract each producer has with the shared POST helper: it imports
    cleanly through its own sys.path hop, and it sets the two constants the
    helper reads. Run as a subprocess so a wrapper cannot be satisfied by
    post_to_kiosk already sitting in sys.modules from another test."""
    wrapper = REPO_ROOT / rel_path
    assert wrapper.exists(), f"{rel_path} is missing"
    proc = subprocess.run(
        [sys.executable, "-c", SNIPPET, str(wrapper)], capture_output=True, text=True)
    assert proc.returncode == 0, f"{rel_path} did not import: {proc.stderr.strip()}"
    card, body_type = proc.stdout.strip().split("\n")
    assert card == expected_card
    assert body_type == expected_type
