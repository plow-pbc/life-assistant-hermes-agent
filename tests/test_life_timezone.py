"""tests/test_life_timezone.py — the boot step that decides where this agent lives."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STEP = ROOT / "image" / "cont-init.d" / "10-life-timezone"


def run(tmp_path, config=None):
    """Run the real script with its three paths pointed at tmp."""
    cfg = tmp_path / "config.json"
    if config is not None:
        cfg.write_text(config if isinstance(config, str) else json.dumps(config))
    out = tmp_path / "TZ"
    proc = subprocess.run(
        ["sh", str(STEP)], capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "LIFE_CONFIG": str(cfg),
             "LIFE_TZ_OUT": str(out), "LIFE_PYTHON": sys.executable})
    return proc, (out.read_text() if out.exists() else None)


def test_the_household_zone_becomes_the_process_zone(tmp_path):
    proc, tz = run(tmp_path, {"family": {"timezone": "America/Chicago"}})
    assert proc.returncode == 0, proc.stderr
    assert tz == "America/Chicago", "no trailing newline: with-contenv takes the file verbatim"


@pytest.mark.parametrize("config", [
    None,                                             # first boot, no config yet
    "{not json",                                      # half-written file
    {"family": {}},                                   # onboarding got this far
    {"family": {"timezone": "   "}},                  # blank
    {"family": {"timezone": "Mars/Olympus_Mons"}},    # not in the tz database
], ids=["absent", "malformed", "no-zone", "blank", "unknown-zone"])
def test_an_unusable_config_yields_utc_and_never_fails_the_boot(tmp_path, config):
    """Empty is worse than wrong: register_crons.py refuses to register at all
    on a blank zone, so an unset TZ would turn "not set up yet" into a hard
    failure with no path out. And a non-zero exit here takes the gateway with
    it, over a file the agent itself writes."""
    proc, tz = run(tmp_path, config)
    assert proc.returncode == 0, proc.stderr
    assert tz == "UTC"


def test_it_says_which_zone_it_chose(tmp_path):
    """The line is the evidence. A schedule firing at the wrong local hour is
    silent everywhere else."""
    proc, _ = run(tmp_path, {"family": {"timezone": "Europe/Lisbon"}})
    assert "life-timezone: TZ=Europe/Lisbon" in proc.stderr
