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
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
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


class _RedirectTarget(BaseHTTPRequestHandler):
    """A real, reachable server that WOULD answer the redirect if it were
    followed -- so a broken/removed _NoRedirect makes geocode() succeed
    with these bogus coordinates instead of raising, discriminating "the
    redirect was blocked" from "the redirect was followed and then failed"."""
    hits = []

    def do_GET(self):
        type(self).hits.append(self.path)
        body = json.dumps({"results": [{"latitude": 99.0, "longitude": 99.0}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class _Redirecting(BaseHTTPRequestHandler):
    location = ""

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", type(self).location)
        self.end_headers()

    def log_message(self, *_args):
        pass


def test_geocode_refuses_cleanly_on_a_blocked_redirect(monkeypatch):
    """_NoRedirect must stop a 3xx from being followed (it would carry the
    request elsewhere); the fix is that a blocked redirect refuses by name
    instead of crashing json.load on the redirect's non-JSON body."""
    _RedirectTarget.hits = []
    target = HTTPServer(("127.0.0.1", 0), _RedirectTarget)
    threading.Thread(target=target.serve_forever, daemon=True).start()
    _Redirecting.location = f"http://127.0.0.1:{target.server_address[1]}/steal"
    server = HTTPServer(("127.0.0.1", 0), _Redirecting)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(wc, "GEOCODE_URL", f"http://127.0.0.1:{server.server_address[1]}/?name=")
    try:
        with pytest.raises(SystemExit) as e:
            wc.geocode("Chicago")
    finally:
        server.shutdown()
        server.server_close()
        target.shutdown()
        target.server_close()
    assert "could not look up" in str(e.value) and "Chicago" in str(e.value)
    assert _RedirectTarget.hits == []


def test_main_writes_the_file_mode_600_and_reports_the_gate(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wc, "geocode", fake_geocode)
    target = tmp_path / "ld" / "config.json"
    assert wc.main(stdin=io.StringIO(json.dumps(FULL)), env=ENV, config_path=str(target)) == 0
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    assert gate(json.loads(target.read_text())) == ""
    assert "gate: PASS" in capsys.readouterr().out
