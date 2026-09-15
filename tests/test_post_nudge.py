"""tests/test_post_nudge.py — behavior tests for the nudge's posting coordinator.

post_nudge.py is the one command the sheet runs: validate the chat config,
read the handoffs, kiosk leg (the wall-calendar card, when there is one, over
the stdin transport), chat leg (every reminder), consume once. These tests import the module and fake the
two shared post_to_kiosk seams (main / post_bearer_json) — a seam reachable
only by an importer, never by the CLI the sheet invokes. The wire behavior
of those seams is owned by tests/test_post_to_kiosk.py
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
    """The handoff on disk, the credential in the environment first boot fills,
    the two seams faked."""
    handoff = tmp_path / "calendar-nudge-text"
    handoff.write_text(LINE1 + "\n" + LINE2 + "\n")
    card = tmp_path / "calendar-nudge-card"
    card.write_text(LINE2 + "\n")  # only the later meeting is on the wall
    monkeypatch.setattr(pn, "HANDOFF", str(handoff))
    monkeypatch.setattr(pn, "CARD", str(card))
    for name, value in (("PLOW_API_BASE", "https://plow.test"),
                        ("PLOW_HOME_CHANNEL", "cht_home"),
                        ("PLOW_AGENT_TOKEN", "tok_agent")):
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["post_nudge.py"])

    calls = []
    # The fake kiosk leg records what arrived on the stdin transport — the
    # coordinator's importer-only seam for the one kiosk line.
    monkeypatch.setattr(pn.post_to_kiosk, "main",
                        lambda: calls.append(("kiosk", sys.stdin.read().strip())))
    monkeypatch.setattr(
        pn.post_to_kiosk, "post_bearer_json",
        lambda url, token, body, label: calls.append(("chat", url, token, body)))
    return SimpleNamespace(handoff=handoff, card=card, calls=calls,
                           monkeypatch=monkeypatch)


@pytest.mark.parametrize("on_wall", [True, False], ids=["wall-card", "chat-only"])
def test_success_posts_the_wall_card_and_every_reminder_to_chat_then_consumes(rig, on_wall):
    if not on_wall:
        rig.card.unlink()
    pn.main()
    chat = ("chat", "https://plow.test/v1/chats/cht_home/messages",
            "tok_agent", {"body": LINE1 + "\n" + LINE2})
    assert rig.calls == ([("kiosk", LINE2)] if on_wall else []) + [chat]
    assert not rig.handoff.exists() and not rig.card.exists(), (
        "the coordinator owns consume-on-success, once, after both legs"
    )


def test_the_retired_names_are_not_read(rig):
    """These are the names the pre-unification adapter used. Nothing reads
    them any more: an environment carrying only those refuses, loudly, rather
    than half-delivering. An agent still on them re-activates."""
    for name in ("PLOW_API_BASE", "PLOW_HOME_CHANNEL", "PLOW_AGENT_TOKEN"):
        rig.monkeypatch.delenv(name, raising=False)
    for name, value in (("PLOW_CHAT_BASE_URL", "https://retired.test"),
                        ("PLOW_CHAT_CHAT_UID", "cht_retired"),
                        ("PLOW_CHAT_TOKEN", "tok_retired")):
        rig.monkeypatch.setenv(name, value)
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
    rig.monkeypatch.delenv(missing, raising=False)
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
    ("post_bearer_json", [("kiosk", LINE2)]),  # chat fails after kiosk
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
    assert rig.handoff.exists() and rig.card.exists()


def test_dry_run_previews_without_consuming(rig, capsys):
    rig.monkeypatch.setattr(sys, "argv", ["post_nudge.py", "--dry-run"])
    pn.main()
    assert rig.calls == [("kiosk", LINE2)], "no chat POST on a dry run"
    assert rig.handoff.exists()
    assert "dry-run" in capsys.readouterr().out
