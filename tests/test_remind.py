"""The reminder resolver: it computes the one-shot delay, in the household zone,
and it asks rather than inventing a time. Run as a subprocess so exit codes --
the ask/answer boundary -- are asserted exactly as the skill reads them."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WHEN = ROOT / "ld-remind" / "scripts" / "when.py"


def run(phrase, tz="America/Los_Angeles", now="2026-09-29T19:00:00-07:00"):
    p = subprocess.run(
        [sys.executable, str(WHEN), phrase, "--tz", tz, "--now", now],
        capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout or "{}")


def test_a_relative_delay_is_zone_independent():
    code, out = run("in 2 hours")
    assert code == 0
    assert out["schedule"] == "120m" and out["repeat"] == 1


def test_a_wall_clock_time_lands_in_the_household_zone():
    code, out = run("tomorrow at 9am")
    assert code == 0
    assert out["instant"] == "2026-09-30T09:00:00-07:00"


def test_the_same_words_are_a_different_instant_in_a_different_zone():
    """The whole reason the zone is an input: 9am is not one moment."""
    _, la = run("tomorrow at 9am", tz="America/Los_Angeles")
    _, ny = run("tomorrow at 9am", tz="America/New_York",
                now="2026-09-29T22:00:00-04:00")
    assert la["instant"] != ny["instant"]


def test_no_time_is_asked_for_never_invented():
    code, out = run("")
    assert code == 2 and "ask" in out


def test_a_time_already_past_is_refused():
    code, out = run("yesterday at 9am")
    assert code == 2 and "ask" in out


def test_a_bare_hour_with_no_am_pm_is_asked():
    code, out = run("at 9")
    assert code == 2 and "ask" in out


def test_the_schedule_is_a_one_shot_the_cli_accepts():
    """`<N>m` + repeat 1 is exactly what SKILL.md passes to `hermes cron`."""
    code, out = run("in 45 minutes")
    assert code == 0
    assert out["schedule"] == "45m" and out["delay_minutes"] == 45
