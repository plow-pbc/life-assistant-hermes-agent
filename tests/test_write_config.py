"""The interview's answers become the config every producer trusts.

Behavior, not shape: each row is judged by the SHARED gate
(ld-shared/scripts/ld_config_gate.py, imported), because that gate is the
single definition of "installed" and a config it refuses is a 06:00 failure
in front of nobody. The timezone check lives here too -- the owner is the
one answering, and AGENT_TZ is the operator's to change, so the refusal has
to name it for the owner to relay.
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wc = load("write_config", "ld-setup/scripts/write_config.py")
gate = load("ld_config_gate", "ld-shared/scripts/ld_config_gate.py").gate

TZ = "America/Chicago"
ENV = {"TZ": TZ}
FULL = {
    "owner_name": "Rowan", "owner_email": "rowan@example.test",
    "owner_imessage": "+15550001111", "city": "Chicago", "timezone": TZ,
    "has_mac": True, "mac_username": "rowan",
    "extra_calendar_ids": ["fam@group.calendar.google.com"],
    "people": ["Mary"], "teams": [{"abbr": "chc", "sport": "baseball", "league": "mlb"}],
    "digest_length": "short",
}
MINIMAL = {"owner_name": "Rowan", "owner_email": "rowan@example.test",
           "city": "Chicago", "timezone": TZ, "has_mac": False}


def fake_geocode(city):
    assert city == "Chicago"
    return 41.85, -87.65








def live_config():
    # Built here rather than through a whole-config mode: that mode was a form,
    # and it is gone. This is the shape --patch expects to find on disk.
    return {
        "family": {"owner": {"name": FULL["owner_name"], "imessage": FULL["owner_imessage"]},
                   "people": list(FULL["people"]), "timezone": TZ},
        "calendar": {"account": FULL["owner_email"],
                     "sources": [{"calendar_id": FULL["owner_email"], "name": "Personal"},
                                 {"calendar_id": FULL["extra_calendar_ids"][0],
                                  "name": FULL["extra_calendar_ids"][0]}]},
        "weekly_digest": {"length": FULL["digest_length"], "long_lead": []},
        "morning_triage": {"chat_db_path": f"/Users/{FULL['mac_username']}/Library/Messages/chat.db",
                           "ranking_instructions": "", "exclude": {"imessage_handles": []}},
        "calendar_nudge": {"lookahead_virtual_minutes": 30, "lookahead_in_person_minutes": 60,
                           "owner_identities": [FULL["owner_email"]]},
        "weather": {"location": FULL["city"], "lat": 41.85, "lon": -87.65},
        "sports": {"followed": list(FULL["teams"])},
    }


def test_a_patch_changes_one_setting_and_leaves_the_rest_alone():
    current = live_config()
    merged = wc.apply_patch({"family": {"owner": {"name": "Ro"}}}, current, ENV)
    assert merged["family"]["owner"]["name"] == "Ro"
    # Everything the owner did not restate: the sibling key inside the object
    # that was patched, and every other section.
    assert merged["family"]["owner"]["imessage"] == FULL["owner_imessage"]
    assert merged["calendar"] == current["calendar"]
    assert merged["weather"] == current["weather"]
    assert gate(merged) == ""


def test_a_patched_list_replaces_rather_than_grows():
    """followed teams and calendar sources are sets the owner states in full;
    a patch that could only append would have no way to drop one."""
    merged = wc.apply_patch(
        {"sports": {"followed": [{"abbr": "bos", "sport": "baseball", "league": "mlb"}]}},
        live_config(), ENV)
    assert [t["abbr"] for t in merged["sports"]["followed"]] == ["bos"]


def test_a_new_city_takes_its_coordinates_with_it():
    """The one patch that fails silently: the card's title becomes the new city
    and the forecast stays the old one's."""
    merged = wc.apply_patch({"weather": {"location": "Denver"}}, live_config(), ENV,
                            geocoder=lambda city: (39.74, -104.99))
    assert merged["weather"] == {"location": "Denver", "lat": 39.74, "lon": -104.99}


# A patch is composed by a model from a sentence, so a misspelling merges in
# BESIDE the real key rather than failing -- the gate then passes on the old
# value and the owner is told a change landed that never did. The nested and
# list-item rows are the half a top-level section check cannot see.
@pytest.mark.parametrize("patch,expected", [
    ({"wether": {"location": "Denver"}}, "'wether'"),
    ({"family": {"owner": {"nme": "Ro"}}}, "'family.owner.nme'"),
    ({"sports": {"followed": [{"abbr": "bos", "leage": "mlb"}]}},
     "'sports.followed[0].leage'"),
    ({"family": {"timezone": "America/Los_Angeles"}}, "AGENT_TZ"),
], ids=["section", "nested", "list-item", "timezone"])
def test_an_invalid_patch_refuses_and_names_what_is_wrong(patch, expected):
    with pytest.raises(SystemExit) as e:
        wc.apply_patch(patch, live_config(), ENV)
    assert expected in str(e.value)


def test_a_patch_the_gate_would_refuse_never_reaches_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(wc, "geocode", fake_geocode)
    target = tmp_path / "ld" / "config.json"
    target.parent.mkdir(parents=True, exist_ok=True); target.write_text(json.dumps(live_config()))
    before = target.read_text()

    with pytest.raises(SystemExit) as e:
        wc.main(["--patch"], env=ENV, config_path=str(target),
                stdin=io.StringIO(json.dumps({"calendar_nudge": {"owner_identities": []}})))
    assert "the gate says" in str(e.value)
    assert target.read_text() == before


def test_the_installed_command_line_actually_reaches_the_patch_path(tmp_path):
    """Through __main__, not through main() -- the seam every other test here
    skips, and the one the documented invocation actually uses.

    `main()` defaults its argv to [] on purpose, so a library caller never picks
    up pytest's own argv; the entry point has to pass sys.argv[1:] itself. When
    it passed [] instead, `--patch` was parsed as no flags and the partial config
    went through the first-run path, which refuses it as missing answers.

    Run in place, against the real /opt/data path that does not exist here: the
    refusal it reaches is the proof. "could not read ... config.json" is the
    patch path; "missing required answer(s)" is the first-run path.
    """
    script = ROOT / "ld-setup" / "scripts" / "write_config.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--patch"],
        input=json.dumps({"family": {"owner": {"name": "Ro"}}}),
        capture_output=True, text=True, env={**os.environ, "TZ": TZ})

    combined = proc.stdout + proc.stderr
    assert "missing required answer(s)" not in combined, (
        "--patch was dropped and the partial config went through build()")
    assert "refusing to patch" in combined, combined


def test_a_write_that_fails_leaves_the_previous_config_intact(tmp_path, monkeypatch):
    """The config is the only copy of every preference the owner is NOT
    restating, which is the whole point of --patch. A truncate-then-write
    destroys it before the replacement exists, so a failure in that window
    leaves an empty file -- and an unreadable config stands every producer
    down at once, silently."""
    monkeypatch.setattr(wc, "geocode", fake_geocode)
    target = tmp_path / "ld" / "config.json"
    target.parent.mkdir(parents=True, exist_ok=True); target.write_text(json.dumps(live_config()))
    before = target.read_bytes()

    def boom(*_args):
        raise OSError("no space left on device")

    monkeypatch.setattr(wc.os, "replace", boom)
    with pytest.raises(OSError):
        wc.main(["--patch"], env=ENV, config_path=str(target),
                stdin=io.StringIO(json.dumps({"family": {"owner": {"name": "Ro"}}})))

    assert target.read_bytes() == before
    # And nothing half-written left beside it for the next run to trip over.
    assert [p.name for p in target.parent.iterdir()] == ["config.json"]


@pytest.mark.parametrize("token", ["Infinity", "-Infinity", "NaN"], ids=["inf", "-inf", "nan"])
def test_a_patch_carrying_a_non_standard_json_constant_is_refused(
    tmp_path, monkeypatch, token
):
    """Python parses NaN/Infinity and writes them back, and the shared gate
    passes them (`float("inf") > 0`). Written out, ld_config_gate's own reader
    -- which already refuses those tokens -- calls the live config "not valid
    JSON" and stands every producer down. Refuse at the door instead."""
    monkeypatch.setattr(wc, "geocode", fake_geocode)
    target = tmp_path / "ld" / "config.json"
    target.parent.mkdir(parents=True, exist_ok=True); target.write_text(json.dumps(live_config()))
    before = target.read_bytes()

    with pytest.raises(SystemExit) as e:
        wc.main(["--patch"], env=ENV, config_path=str(target),
                stdin=io.StringIO(
                    '{"calendar_nudge": {"lookahead_virtual_minutes": %s}}' % token))

    assert token in str(e.value)
    assert target.read_bytes() == before

