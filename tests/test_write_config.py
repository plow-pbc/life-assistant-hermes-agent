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


@pytest.mark.parametrize("answers", [FULL, MINIMAL], ids=["every-answer", "required-only"])
def test_answers_become_a_config_the_shared_gate_accepts(answers):
    config = wc.build(answers, ENV, geocoder=fake_geocode)
    assert gate(config) == ""
    assert config["calendar"]["sources"][0]["calendar_id"] == "rowan@example.test"
    assert config["calendar_nudge"]["owner_identities"] == ["rowan@example.test"]
    assert config["weather"] == {"location": "Chicago", "lat": 41.85, "lon": -87.65}
    assert config["family"]["timezone"] == TZ


def test_a_mac_owner_gets_a_messages_db_path_and_a_mac_less_one_does_not():
    assert (wc.build(FULL, ENV, geocoder=fake_geocode)["morning_triage"]["chat_db_path"]
            == "/Users/rowan/Library/Messages/chat.db")
    assert wc.build(MINIMAL, ENV, geocoder=fake_geocode)["morning_triage"]["chat_db_path"] == ""


@pytest.mark.parametrize("drop", sorted(wc.REQUIRED))
def test_a_missing_required_answer_refuses_by_name(drop):
    with pytest.raises(SystemExit) as e:
        wc.build({k: v for k, v in FULL.items() if k != drop}, ENV, geocoder=fake_geocode)
    assert drop in str(e.value)


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
