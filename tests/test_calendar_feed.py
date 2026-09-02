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
sys.path.insert(0, str(SCRIPTS))
import post_to_kiosk  # noqa: E402


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
        response = type(self).answer(body) if self.path.endswith("/mcp") else {"ok": True}
        encoded = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    @classmethod
    def answer(cls, body):
        """Stand in for the Mac, by tool and argv rather than by call order."""
        params = body["params"]
        arguments = params.get("arguments", {})
        if params["name"] == "plow_write_file":
            return ok_text("wrote")
        if arguments.get("argv", [""])[0] == "gog":
            return cls.relay_responses.pop(0)
        return completed('{"ok":true}')      # the Mac-side curl

    def log_message(self, *_args):
        pass


def ok_text(text):
    return {"jsonrpc": "2.0", "id": 1,
            "result": {"content": [{"type": "text", "text": text}]}}


def completed(output):
    return ok_text(json.dumps(
        {"status": "completed", "exit_code": 0, "output": output}))


def relay_ok(events):
    """A successful gog gather, behind Latch's preamble line."""
    return completed("Note: Using direct access token\n" + json.dumps(events))


def event(uid, ical, start, end, **extra):
    return {"id": uid, "iCalUID": ical, "status": "confirmed",
            "summary": f"Event {uid}",
            "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}


CONFIG = {
    "family": {"timezone": "America/Los_Angeles"},
    "calendar": {"account": "ada@example.com",
                 "sources": [{"calendar_id": "ada@example.com"}]},
}


validated = []


@pytest.fixture
def feed(tmp_path, monkeypatch):
    Handler.relay_responses, Handler.requests, Handler.redirect_paths = [], [], set()
    validated.clear()
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
    # Everything through the dotenv, nothing through the process env: the unit
    # loads no EnvironmentFile, and a value arriving as env would skip the
    # household-network check that guards a dotenv-sourced endpoint.
    (tmp_path / "dotenv").write_text(
        "DOMO_DEVICE_UID=dev1\n"
        "DOMO_MCP_TOKEN=relay-token\n"
        f"DASHBOARD_ENDPOINT_URL={base}/api/message\n"
        "DASHBOARD_TOKEN=kiosk-token\n"
        # What mint_wall_token.py writes on every set-up instance, on both its
        # paths. Testing the direct path instead would test the mode no real
        # household is in.
        "DASHBOARD_DELIVERY=latch\n")
    monkeypatch.setattr(module, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(module, "DOTENV", str(tmp_path / "dotenv"))
    monkeypatch.setattr(module, "RELAY_ORIGIN", base)
    # post_to_kiosk's endpoint validator pins the Pi's exact `:5174/api/message`
    # shape, which a loopback server on an ephemeral port cannot wear. Its rules
    # are that module's contract and are tested there; what matters HERE is that
    # a dotenv-sourced endpoint is routed THROUGH it rather than around it — so
    # it is replaced by a recorder and the routing is asserted.
    monkeypatch.setattr(post_to_kiosk, "_validate_dotenv_endpoint", validated.append)
    try:
        yield module, base
    finally:
        server.shutdown()
        server.server_close()


def relay_calls(name=None):
    calls = [r["body"]["params"] for r in Handler.requests
             if r["path"].endswith("/mcp")]
    return [c for c in calls if name is None or c["name"] == name]


def published():
    """The feed body, read out of the file staged on the Mac."""
    return [json.loads(c["arguments"]["content"])
            for c in relay_calls("plow_write_file")]


def test_a_private_occurrence_takes_its_siblings_off_the_wall(feed):
    feed, _ = feed
    """One invite is on several calendars, all copies sharing an iCalUID. Drop
    only the private copy and a default-visibility sibling publishes the title
    to a display everyone in the house can read."""
    Handler.relay_responses = [relay_ok([
        event("p1", "ical-p", "2026-09-02T09:00:00-07:00", "2026-09-02T10:00:00-07:00",
              visibility="private", summary="Therapy"),
        # Same occurrence seen through a second calendar, but the copy was
        # edited and its END differs. Keying on the end as well as the start
        # made this copy a different occurrence, so it survived the prepass
        # and published the private title.
        event("p2", "ical-p", "2026-09-02T09:00:00-07:00", "2026-09-02T10:30:00-07:00",
              summary="Therapy"),
        event("c", "ical-c", "2026-09-02T11:00:00-07:00", "2026-09-02T12:00:00-07:00",
              status="cancelled"),
        event("ok", "ical-ok", "2026-09-02T13:00:00-07:00", "2026-09-02T14:00:00-07:00"),
    ])]

    assert feed.main(now=1_756_700_000) == 0

    wire = published()[0]
    assert [e["uid"] for e in wire["events"]] == ["ok"]
    assert "Therapy" not in json.dumps(wire)


def test_the_relay_bearer_is_never_forwarded_through_a_redirect(feed, capsys):
    feed, _ = feed
    """urllib follows 3xx AND re-sends Authorization to the new origin, so a
    rewritten endpoint would walk the relay credential off the household
    network. The request must fail instead."""
    Handler.redirect_paths = {"/v1/relay/devices/dev1/mcp"}

    assert feed.main(now=1_756_700_000) == 0

    assert [r["path"] for r in Handler.requests] == ["/v1/relay/devices/dev1/mcp"]
    assert capsys.readouterr().out.startswith(
        "calendar feed failed: relay returned HTTP 302")


def test_the_strip_is_ordered_by_when_things_start(feed):
    feed, base = feed
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

    wire = published()[0]
    # The all-day row sorts by local midnight, ahead of the same day's 9am.
    assert [e["uid"] for e in wire["events"]] == ["allday", "a", "b"]
    assert wire["window_days"] == 7
    # Byte-identical to ld-weekly-digest's already-approved seven-day argv.
    # A fourth shape strands every unattended run on a Latch approval card.
    # The dotenv endpoint went through post_to_kiosk's validator, not around
    # it. Loading it into the process env instead (an EnvironmentFile in the
    # unit) is what used to skip this and hand the bearer to an injected host.
    assert validated == [f"{base}/api/message"]
    assert relay_calls("plow_run_command")[0]["arguments"]["argv"] == [
        "gog", "calendar", "events", "list",
        "--account=ada@example.com", "--calendars=ada@example.com",
        "--days=7", "--json", "--results-only", "--sort=start", "--max=250",
    ]


def test_latch_delivery_makes_the_two_documented_calls_itself(feed):
    """Every set-up household is on DASHBOARD_DELIVERY=latch — mint_wall_token
    writes it unconditionally — so this is the path that actually runs. The two
    calls are latch-delivery.md's, in its order, made here because there is no
    model in a feed run to make them."""
    module, base = feed
    Handler.relay_responses = [relay_ok([
        event("a", "ical-a", "2026-09-02T09:00:00-07:00", "2026-09-02T10:00:00-07:00")])]

    assert module.main(now=1_756_700_000) == 0

    names = [c["name"] for c in relay_calls()]
    assert names == ["plow_run_command", "plow_write_file", "plow_run_command"]
    curl = relay_calls("plow_run_command")[1]["arguments"]
    assert curl["network"] is True
    assert curl["argv"] == ["sh", "-c",
        "curl -fsS -H @$HOME/Plow/ld/dashboard.hdr "
        "-H 'Content-Type: application/json' "
        f"--data-binary @$HOME/Plow/ld/calendar.json {base}/api/calendar"]
    # The wall's bearer stays on the Mac in dashboard.hdr; it is never read
    # here and must not appear anywhere in what crosses the relay.
    assert "kiosk-token" not in json.dumps(Handler.requests)
    # Nothing was POSTed from this container -- it is not on the Pi's LAN.
    assert [r["path"] for r in Handler.requests if r["path"] == "/api/calendar"] == []

