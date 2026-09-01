"""tests/test_post_nudge.py — behavior tests for the nudge's posting coordinator.

post_nudge.py is the one command the sheet runs: validate the chat config,
read the ONE handoff, kiosk leg (first line, over the stdin transport), chat
leg (whole body), consume once. These tests import the module and fake the
two shared post_to_kiosk seams (main / post_bearer_json) — a seam reachable
only by an importer, never by the CLI the sheet invokes. The wire behavior
of those seams is owned by the vendored test_post_to_kiosk.py suite
(redirect refusal included, through the shared post_bearer_json); what THIS
suite pins is the coordinator's ordering and consume contract.
"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "ld-calendar-nudge" / "scripts" / "post_nudge.py"

spec = importlib.util.spec_from_file_location("post_nudge", SCRIPT)
pn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pn)

LINE1 = 'Heads up: "Standup" at 12:20pm (20m).'
LINE2 = 'Heads up: "Sync" at 12:40pm (40m).'


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """The handoff + dotenv on disk, env cleared, the two seams faked."""
    handoff = tmp_path / "calendar-nudge-text"
    handoff.write_text(LINE1 + "\n" + LINE2 + "\n")
    dotenv = tmp_path / ".env"
    dotenv.write_text("PLOW_API_BASE=https://dotenv.test\n"
                      "PLOW_HOME_CHANNEL=cht_dotenv\n"
                      "PLOW_AGENT_TOKEN=tok_dotenv\n")
    monkeypatch.setattr(pn, "HANDOFF", str(handoff))
    monkeypatch.setattr(pn, "DOTENV", str(dotenv))
    for name in ("PLOW_API_BASE", "PLOW_HOME_CHANNEL", "PLOW_AGENT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["post_nudge.py"])

    calls = []
    # The fake kiosk leg records what arrived on the stdin transport — the
    # coordinator's importer-only seam for the one kiosk line.
    monkeypatch.setattr(pn.post_to_kiosk, "main",
                        lambda: calls.append(("kiosk", sys.stdin.read().strip())))
    monkeypatch.setattr(
        pn.post_to_kiosk, "post_bearer_json",
        lambda url, token, body, label: calls.append(("chat", url, token, body)))
    return SimpleNamespace(handoff=handoff, dotenv=dotenv, calls=calls,
                           monkeypatch=monkeypatch)


def test_success_posts_first_line_to_kiosk_full_body_to_chat_then_consumes(rig):
    pn.main()
    assert rig.calls == [
        ("kiosk", LINE1),
        ("chat", "https://dotenv.test/v1/chats/cht_dotenv/messages",
         "tok_dotenv", {"body": LINE1 + "\n" + LINE2}),
    ]
    assert not rig.handoff.exists(), (
        "the coordinator owns consume-on-success, once, after both legs"
    )


def test_env_wins_over_dotenv(rig):
    rig.monkeypatch.setenv("PLOW_HOME_CHANNEL", "cht_env")
    pn.main()
    assert "/v1/chats/cht_env/messages" in rig.calls[1][1]


def test_the_retired_names_are_not_read(rig):
    """These are the names the pre-unification adapter used. Nothing reads
    them any more: a dotenv carrying only those refuses, loudly, rather than
    half-delivering. An instance still on them runs `agent-mgr
    migrate-plugin-env` or re-activates."""
    rig.dotenv.write_text("PLOW_CHAT_BASE_URL=https://fleet.test\n"
                          "PLOW_CHAT_CHAT_UID=cht_fleet\n"
                          "PLOW_CHAT_TOKEN=tok_fleet\n")
    with pytest.raises(SystemExit) as excinfo:
        pn.main()
    assert "PLOW_API_BASE" in str(excinfo.value)
    assert rig.calls == [], "nothing may post on a broken chat config"
    assert rig.handoff.exists()


@pytest.mark.parametrize("missing", [
    "PLOW_API_BASE", "PLOW_HOME_CHANNEL", "PLOW_AGENT_TOKEN"])
def test_a_missing_credential_refuses_before_anything_posts(rig, missing):
    """The half-delivered trap this ordering exists for: a blank chat config
    must stop the run BEFORE the kiosk posts."""
    rig.dotenv.write_text("".join(
        f"{n}=value\n" for n in
        ("PLOW_API_BASE", "PLOW_HOME_CHANNEL", "PLOW_AGENT_TOKEN")
        if n != missing))
    with pytest.raises(SystemExit) as excinfo:
        pn.main()
    assert missing in str(excinfo.value)
    assert rig.calls == [], "nothing may post on a broken chat config"
    assert rig.handoff.exists()


@pytest.mark.parametrize("mutate", [
    lambda handoff: handoff.write_text("   "),
    lambda handoff: handoff.unlink(),
], ids=["empty", "missing"])
def test_a_bad_handoff_refuses_before_anything_posts(rig, mutate):
    mutate(rig.handoff)
    with pytest.raises(SystemExit):
        pn.main()
    assert rig.calls == []


@pytest.mark.parametrize(("failing_seam", "calls_before"), [
    ("main", []),                       # kiosk fails: chat never runs
    ("post_bearer_json", [("kiosk", LINE1)]),  # chat fails after kiosk
], ids=["kiosk-fails", "chat-fails"])
def test_a_delivery_failure_leaves_the_handoff(rig, failing_seam, calls_before):
    """Either leg failing must leave the handoff for a retry — a chat retry
    re-posts the kiosk (harmless latest-wins) rather than finding the
    handoff already consumed."""
    rig.monkeypatch.setattr(pn.post_to_kiosk, failing_seam,
                            lambda *a: sys.exit("error: HTTP 500"))
    with pytest.raises(SystemExit):
        pn.main()
    assert rig.calls == calls_before
    assert rig.handoff.exists()


def test_dry_run_previews_without_consuming(rig, capsys):
    rig.monkeypatch.setattr(sys, "argv", ["post_nudge.py", "--dry-run"])
    pn.main()
    assert rig.calls == [("kiosk", LINE1)], "no chat POST on a dry run"
    assert rig.handoff.exists()
    assert "dry-run" in capsys.readouterr().out
