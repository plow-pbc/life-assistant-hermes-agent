"""calendar_feed.py — the behaviours whose failure is silent.

One test per way this producer can be wrong without anyone noticing: a private
title reaching a display the whole house reads, a strip whose rows are in the
order two calendars happened to return them, a delivery that is not the two
documented calls from the Mac, and a stand-down that is not one line.

The seam is the module's fixed paths and a fake `latch` on PATH (conftest),
which answers by subcommand and records every call the script really made.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "ld-shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import post_to_kiosk  # noqa: E402

KIOSK = "http://192.0.2.10:5174"


def gathered(events):
    """latch's fan-out answer for an events read: each event names its account."""
    return {"items": [{**e, "account": "ada@example.com"} for e in events],
            "accounts": [{"account": "ada@example.com", "status": "ok"}]}


def event(uid, ical, start, end, **extra):
    return {"id": uid, "iCalUID": ical, "status": "confirmed",
            "summary": f"Event {uid}",
            "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}


EVENTS = [event("a", "ical-a",
                "2026-09-02T09:00:00-07:00", "2026-09-02T10:00:00-07:00")]

CONFIG = {
    "family": {"timezone": "America/Los_Angeles"},
    "calendar": {"account": "ada@example.com",
                 "sources": [{"calendar_id": "ada@example.com"}]},
}


validated = []


@pytest.fixture
def feed(tmp_path, monkeypatch, fake_latch):
    validated.clear()
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(
            "calendar_feed", SCRIPTS / "calendar_feed.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPTS))

    (tmp_path / "config.json").write_text(json.dumps(CONFIG))
    # The wall's own values through the dotenv, nothing else: a kiosk endpoint
    # arriving as process env would skip the household-network check that
    # guards a dotenv-sourced one.
    (tmp_path / "dotenv").write_text(
        f"DASHBOARD_ENDPOINT_URL={KIOSK}/api/message\n"
        "DASHBOARD_TOKEN=kiosk-token\n"
        # What mint_wall_token.py writes on every set-up instance.
        "DASHBOARD_DELIVERY=latch\n")
    monkeypatch.setattr(module, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(module, "AGENT_DOTENV", str(tmp_path / "dotenv"))
    # post_to_kiosk's endpoint validator pins the Pi's exact address shape; its
    # rules are that module's contract and are tested there. What matters HERE
    # is that a dotenv-sourced endpoint is routed THROUGH it.
    monkeypatch.setattr(post_to_kiosk, "_validate_dotenv_endpoint", validated.append)
    # The Mac asleep is not this suite's default: cloud has Google, mac has
    # Messages, so the gather and the delivery land on different hosts.
    fake_latch.hosts(("mba", False), ("cloud", True, "google"),
                     ("mac", True, "google", "messages"))
    fake_latch.answer("google", gathered(EVENTS))
    fake_latch.answer("file", {"path": "/Users/ada/Plow/ld/calendar.json"})
    fake_latch.answer("exec", {"exit_code": 0, "stdout": "{\"ok\":true}", "stderr": ""})
    return module, fake_latch


def published(latch):
    """The feed bodies, as put on the Mac."""
    return [json.loads(c["content"]) for c in latch.calls("file")]


def test_a_private_occurrence_takes_its_siblings_off_the_wall(feed):
    """One invite is on several calendars, all copies sharing an iCalUID. Drop
    only the private copy and a default-visibility sibling publishes the title
    to a display everyone in the house can read."""
    module, latch = feed
    latch.answer("google", gathered([
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
    ]))

    assert module.main(now=1_756_700_000) == 0

    wire = published(latch)[0]
    assert [e["uid"] for e in wire["events"]] == ["ok"]
    assert "Therapy" not in json.dumps(wire)


def test_the_strip_is_ordered_by_when_things_start(feed):
    """gog sorts within one calendar; the merged result is in whatever order
    the calendars came back. An unsorted strip is wrong in a way that reads as
    a rendering bug, so it survives on the wall."""
    module, latch = feed
    latch.answer("google", gathered([
        event("b", "ical-b", "2026-09-03T09:00:00-07:00", "2026-09-03T10:00:00-07:00"),
        {"id": "allday", "iCalUID": "ical-d", "status": "confirmed",
         "summary": "Holiday", "start": {"date": "2026-09-02"},
         "end": {"date": "2026-09-03"}},
        event("a", "ical-a", "2026-09-02T09:00:00-07:00", "2026-09-02T10:00:00-07:00"),
    ]))

    assert module.main(now=1_756_700_000) == 0

    wire = published(latch)[0]
    # The all-day row sorts by local midnight, ahead of the same day's 9am.
    assert [e["uid"] for e in wire["events"]] == ["allday", "a", "b"]
    assert wire["window_days"] == 7
    # The dotenv endpoint went through post_to_kiosk's validator, not around it.
    assert validated == [f"{KIOSK}/api/message"]
    # The first online Google host, for the home chat, naming gog's fields: the
    # privacy filter needs visibility and iCalUID, which latch's compact
    # default leaves out.
    assert [c["argv"] for c in latch.calls("google")] == [[
        "google", "--host", "cloud", "--plow-chat", "cht_home", "--",
        "calendar", "events", "--account=ada@example.com",
        "--calendars=ada@example.com", "--days=7", "--sort=start", "--max=250",
        "--select=id,iCalUID,status,visibility,summary,location,start,end",
    ]]


def test_latch_delivery_makes_the_two_documented_calls_from_the_mac(feed, capsys):
    """Every set-up household is on DASHBOARD_DELIVERY=latch, so this is the path
    that actually runs: latch-delivery.md's two calls, in its order, on the Mac,
    made here because there is no model in a feed run to make them."""
    module, latch = feed

    assert module.main(now=1_756_700_000) == 0

    assert [c["argv"][0] for c in latch.calls()] == ["status", "google", "file", "exec"]
    put = latch.calls("file")[0]["argv"]
    assert put[:6] == ["file", "put", "--host", "mac", "--plow-chat", "cht_home"]
    assert put[-1] == "~/Plow/ld/calendar.json"
    assert latch.calls("exec")[0]["argv"] == [
        "exec", "--host", "mac", "--plow-chat", "cht_home", "--network",
        "--timeout", "600", "--", "sh", "-c",
        "curl -fsS -H @$HOME/Plow/ld/dashboard.hdr "
        "-H 'Content-Type: application/json' "
        f"--data-binary @$HOME/Plow/ld/calendar.json {KIOSK}/api/calendar"]
    # The wall's bearer stays on the Mac in dashboard.hdr; it never crosses latch.
    assert "kiosk-token" not in json.dumps(latch.calls())
    assert capsys.readouterr().out == "calendar feed: 1 events; shipped through the Mac\n"


@pytest.mark.parametrize("setup, printed", [
    pytest.param(lambda latch: latch.hosts(("mba", False), ("cloud", True, "google")),
                 "calendar feed failed: no online host has messages on", id="mac-asleep"),
    pytest.param(lambda latch: latch.answer("exec", ("error", "gatekeeper_denied", 4)),
                 "calendar feed failed: latch exec: gatekeeper_denied", id="gatekeeper-refused"),
    pytest.param(lambda latch: latch.answer("exec", {"exit_code": 22, "stdout": "", "stderr": "PRIVATE"}),
                 "calendar feed failed: sh on mac exited 22", id="curl-failed"),
    pytest.param(lambda latch: latch.answer("google", ("error", "google_needs_sign_in", 1)),
                 "calendar feed failed: latch google: google_needs_sign_in", id="needs-sign-in"),
    pytest.param(lambda latch: latch.answer("google", {"accounts": []}),
                 "calendar feed failed: gather answered with no event list", id="no-items"),
])
def test_every_failure_is_one_line_and_exit_zero(feed, capsys, setup, printed):
    """Unattended: nobody reads a non-zero exit, and a traceback carrying
    calendar text is a leak. The next tick is the retry."""
    module, latch = feed
    setup(latch)

    assert module.main(now=1_756_700_000) == 0

    assert capsys.readouterr().out == printed + "\n"


def test_no_home_chat_is_a_stand_down(feed, monkeypatch, capsys):
    """Every call acts for the home chat; without one latch would ask which."""
    module, latch = feed
    monkeypatch.delenv("PLOW_HOME_CHANNEL")

    assert module.main(now=1_756_700_000) == 0

    assert capsys.readouterr().out == "calendar feed failed: PLOW_HOME_CHANNEL is not set\n"
    assert latch.calls("google") == []
