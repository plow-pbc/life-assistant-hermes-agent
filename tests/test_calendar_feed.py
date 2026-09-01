"""calendar_feed.py — the three behaviours whose failure is silent.

Here rather than beside the script, and pytest rather than the vendored counter
style, deliberately: a `test_*.py` under ld-shared/ is executed as a SUBPROCESS
by tests/test_vendored_suites.py, so a pytest-shaped file there would exit 0
having run nothing and report green forever.

Three tests, one per way this producer can be wrong without anyone noticing —
a private title reaching a display the whole house reads, the kiosk bearer
following a redirect off the household network, and a strip whose rows are in
the order two calendars happened to return them. Each was confirmed to go red
with the corresponding line removed from the script.

The seam is the module's fixed paths and credential sources, rebound to a
tmp_path and a loopback server standing in for both the Plow relay and the Pi.
"""
import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "ld-shared" / "scripts"


class Handler(BaseHTTPRequestHandler):
    relay_responses: ClassVar[list] = []
    requests: ClassVar[list] = []
    redirect_paths: ClassVar[set] = set()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append({"path": self.path, "body": body,
                                    "authorization": self.headers.get("Authorization")})
        if self.path in type(self).redirect_paths:
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/stolen")
            self.end_headers()
            return
        response = (type(self).relay_responses.pop(0)
                    if self.path.endswith("/mcp") else {"ok": True})
        encoded = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        pass


def relay_ok(events):
    """The relay's success envelope: MCP result -> plow_run_command payload ->
    gog stdout behind Latch's preamble line."""
    payload = {"status": "completed", "exit_code": 0,
               "output": "Note: Using direct access token\n" + json.dumps(events)}
    return {"jsonrpc": "2.0", "id": 1,
            "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}


def event(uid, ical, start, end, **extra):
    return {"id": uid, "iCalUID": ical, "status": "confirmed",
            "summary": f"Event {uid}",
            "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}


CONFIG = {
    "family": {"timezone": "America/Los_Angeles"},
    "calendar": {"account": "ada@example.com",
                 "sources": [{"calendar_id": "primary"}]},
}


@pytest.fixture
def feed(tmp_path, monkeypatch):
    Handler.relay_responses, Handler.requests, Handler.redirect_paths = [], [], set()
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(
            "calendar_feed", SCRIPTS / "calendar_feed.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPTS))

    (tmp_path / "config.json").write_text(json.dumps(CONFIG))
    (tmp_path / "dotenv").write_text("")
    monkeypatch.setattr(module, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(module, "STATE_FILE", str(tmp_path / "feed.state"))
    monkeypatch.setattr(module, "DOTENV", str(tmp_path / "dotenv"))
    monkeypatch.setenv("PLOW_API_BASE", base)
    monkeypatch.setenv("DOMO_DEVICE_UID", "dev1")
    monkeypatch.setenv("DOMO_MCP_TOKEN", "relay-token")
    monkeypatch.setenv("DASHBOARD_ENDPOINT_URL", f"{base}/api/message")
    monkeypatch.setenv("DASHBOARD_TOKEN", "kiosk-token")
    try:
        yield module
    finally:
        server.shutdown()
        server.server_close()


def published():
    return [r for r in Handler.requests if r["path"] == "/api/calendar"]


def test_a_private_occurrence_takes_its_siblings_off_the_wall(feed):
    """One invite is on several calendars, all copies sharing an iCalUID. Drop
    only the private copy and a default-visibility sibling publishes the title
    to a display everyone in the house can read."""
    Handler.relay_responses = [relay_ok([
        event("p1", "ical-p", "2026-09-02T09:00:00-07:00", "2026-09-02T10:00:00-07:00",
              visibility="private", summary="Therapy"),
        event("p2", "ical-p", "2026-09-02T09:00:00-07:00", "2026-09-02T10:00:00-07:00",
              summary="Therapy"),
        event("c", "ical-c", "2026-09-02T11:00:00-07:00", "2026-09-02T12:00:00-07:00",
              status="cancelled"),
        event("ok", "ical-ok", "2026-09-02T13:00:00-07:00", "2026-09-02T14:00:00-07:00"),
    ])]

    assert feed.main(now=1_756_700_000) == 0

    wire = published()[0]["body"]
    assert [e["uid"] for e in wire["events"]] == ["ok"]
    assert "Therapy" not in json.dumps(wire)


def test_the_relay_bearer_is_never_forwarded_through_a_redirect(feed, capsys):
    """urllib follows 3xx AND re-sends Authorization to the new origin, so a
    rewritten endpoint would walk the relay credential off the household
    network. The request must fail instead."""
    Handler.redirect_paths = {"/v1/relay/devices/dev1/mcp"}

    assert feed.main(now=1_756_700_000) == 0

    assert [r["path"] for r in Handler.requests] == ["/v1/relay/devices/dev1/mcp"]
    assert capsys.readouterr().out.startswith(
        "calendar feed failed: relay returned HTTP 302")


def test_the_strip_is_ordered_by_when_things_start(feed):
    """gog sorts within one calendar; the merged result is in whatever order
    the calendars came back. An unsorted strip is wrong in a way that reads as
    a rendering bug, so it survives on the wall."""
    Handler.relay_responses = [relay_ok([
        event("b", "ical-b", "2026-09-03T09:00:00-07:00", "2026-09-03T10:00:00-07:00"),
        {"id": "allday", "iCalUID": "ical-d", "status": "confirmed",
         "summary": "Holiday", "start": {"date": "2026-09-02"},
         "end": {"date": "2026-09-03"}},
        event("a", "ical-a", "2026-09-02T09:00:00-07:00", "2026-09-02T10:00:00-07:00"),
    ])]

    assert feed.main(now=1_756_700_000) == 0

    wire = published()[0]["body"]
    # The all-day row sorts by local midnight, ahead of the same day's 9am.
    assert [e["uid"] for e in wire["events"]] == ["allday", "a", "b"]
    assert wire["window_days"] == 7
