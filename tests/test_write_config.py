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


@pytest.mark.parametrize("answers,expected_chat_db", [
    (FULL, "/Users/rowan/Library/Messages/chat.db"),
    (MINIMAL, ""),
], ids=["every-answer", "required-only"])
def test_answers_become_a_config_the_shared_gate_accepts(answers, expected_chat_db):
    config = wc.build(answers, ENV, geocoder=fake_geocode)
    assert gate(config) == ""
    assert config["calendar"]["account"] == "rowan@example.test"
    assert config["calendar"]["sources"][0]["calendar_id"] == "rowan@example.test"
    assert config["calendar_nudge"]["owner_identities"] == ["rowan@example.test"]
    assert config["weather"] == {"location": "Chicago", "lat": 41.85, "lon": -87.65}
    assert config["family"]["timezone"] == TZ
    assert config["morning_triage"]["chat_db_path"] == expected_chat_db


@pytest.mark.parametrize("drop", sorted(wc.REQUIRED))
def test_a_missing_required_answer_refuses_by_name(drop):
    with pytest.raises(SystemExit) as e:
        wc.build({k: v for k, v in FULL.items() if k != drop}, ENV, geocoder=fake_geocode)
    assert drop in str(e.value)


@pytest.mark.parametrize("has_mac", ["yes", "false", 1], ids=["yes", "false-string", "one"])
def test_a_non_boolean_has_mac_refuses_by_name(has_mac):
    """has_mac decides whether the Messages db path is written at all, so a
    truthy string or a 1 must refuse rather than quietly resolve as a Mac."""
    with pytest.raises(SystemExit) as e:
        wc.build({**FULL, "has_mac": has_mac}, ENV, geocoder=fake_geocode)
    assert "has_mac" in str(e.value)


def test_has_mac_true_without_mac_username_refuses_by_name():
    """Silently dropping to an empty chat_db_path would disable morning_triage
    with no diagnostic, unlike every other missing-answer case in this file."""
    with pytest.raises(SystemExit) as e:
        wc.build({k: v for k, v in FULL.items() if k != "mac_username"}, ENV, geocoder=fake_geocode)
    assert "mac_username" in str(e.value)


def test_a_timezone_that_is_not_the_containers_refuses_and_names_agent_tz():
    with pytest.raises(SystemExit) as e:
        wc.build(FULL, {"TZ": "America/Los_Angeles"}, geocoder=fake_geocode)
    msg = str(e.value)
    assert "America/Chicago" in msg and "America/Los_Angeles" in msg and "AGENT_TZ" in msg


def test_main_writes_the_file_mode_600_and_reports_the_gate(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wc, "geocode", fake_geocode)
    target = tmp_path / "ld" / "config.json"
    assert wc.main(stdin=io.StringIO(json.dumps(FULL)), env=ENV, config_path=str(target)) == 0
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    assert gate(json.loads(target.read_text())) == ""
    assert "gate: PASS" in capsys.readouterr().out


# --- --patch: a later change, without re-running the interview -------------
#
# The mode exists because the whole-config path resets every answer the owner
# is not restating. So these test what survives a patch, not just what it sets.


def live_config():
    return wc.build(FULL, ENV, geocoder=fake_geocode)


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
    current = live_config()
    merged = wc.apply_patch(
        {"sports": {"followed": [{"abbr": "bos", "sport": "baseball", "league": "mlb"}]}},
        current, ENV)
    assert [t["abbr"] for t in merged["sports"]["followed"]] == ["bos"]


def test_a_new_city_takes_its_coordinates_with_it():
    """The one patch that fails silently: the card's title becomes the new city
    and the forecast stays the old one's."""
    merged = wc.apply_patch({"weather": {"location": "Denver"}}, live_config(), ENV,
                            geocoder=lambda city: (39.74, -104.99))
    assert merged["weather"] == {"location": "Denver", "lat": 39.74, "lon": -104.99}


def test_a_misspelled_section_is_refused_rather_than_merged_in():
    """A patch is composed by a model from a sentence. A dead section would
    merge cleanly, pass the gate, and tell the owner the change landed."""
    with pytest.raises(SystemExit) as e:
        wc.apply_patch({"wether": {"location": "Denver"}}, live_config(), ENV)
    assert "wether" in str(e.value)


def test_a_patched_timezone_the_container_does_not_share_refuses():
    with pytest.raises(SystemExit) as e:
        wc.apply_patch({"family": {"timezone": "America/Los_Angeles"}},
                       live_config(), ENV)
    assert "AGENT_TZ" in str(e.value)


def test_a_patch_the_gate_would_refuse_never_reaches_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(wc, "geocode", fake_geocode)
    target = tmp_path / "ld" / "config.json"
    wc.main(stdin=io.StringIO(json.dumps(FULL)), env=ENV, config_path=str(target))
    before = target.read_text()

    with pytest.raises(SystemExit) as e:
        wc.main(["--patch"], env=ENV, config_path=str(target),
                stdin=io.StringIO(json.dumps({"calendar_nudge": {"owner_identities": []}})))
    assert "the gate says" in str(e.value)
    assert target.read_text() == before


def test_a_patch_reports_what_registering_the_crons_said(tmp_path, monkeypatch, capsys):
    """A patch that filled a producer's last missing field has not turned that
    producer on until its job exists, and a chat turn drops the exit code."""
    monkeypatch.setattr(wc, "geocode", fake_geocode)
    target = tmp_path / "ld" / "config.json"
    wc.main(stdin=io.StringIO(json.dumps(FULL)), env=ENV, config_path=str(target))

    calls = []

    class Result:
        returncode, stdout, stderr = 1, "", "hermes not found"

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return Result()

    monkeypatch.setattr(wc.subprocess, "run", fake_run)
    code = wc.main(["--patch"], env=ENV, config_path=str(target),
                   stdin=io.StringIO(json.dumps({"family": {"owner": {"name": "Ro"}}})))

    assert code == 1
    assert calls and calls[0][1].endswith("register_crons.py")
    out = capsys.readouterr().out
    assert "hermes not found" in out
    assert "schedule registration failed" in out
    # Saved first: the change is on disk even though registration did not land.
    assert json.loads(target.read_text())["family"]["owner"]["name"] == "Ro"
