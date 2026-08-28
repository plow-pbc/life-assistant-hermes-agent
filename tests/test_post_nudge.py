"""tests/test_post_nudge.py — behavior tests for the nudge's posting coordinator.

post_nudge.py is the one command the sheet runs: validate the chat config,
kiosk leg, chat leg, then consume both handoffs. These tests import the
module and fake the two shared post_to_kiosk seams (main / post_bearer_json)
— a seam reachable only by an importer, never by the CLI the sheet invokes.
The wire behavior of those seams is owned by the vendored
test_post_to_kiosk.py suite (redirect refusal included, through the shared
post_bearer_json); what THIS suite pins is the coordinator's ordering and
consume contract.
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


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """Both handoffs + dotenv on disk, env cleared, the two seams faked."""
    kiosk = tmp_path / "calendar-nudge-text"
    kiosk.write_text('Heads up: "Standup" at 12:20pm (20m).\n')
    chat = tmp_path / "calendar-nudge-chat"
    chat.write_text('Heads up: "Standup" at 12:20pm (20m).\n'
                    'Heads up: "Sync" at 12:40pm (40m).\n')
    dotenv = tmp_path / ".env"
    dotenv.write_text("PLOW_CHAT_BASE_URL=https://dotenv.test\n"
                      "PLOW_CHAT_CHAT_UID=cht_dotenv\n"
                      "PLOW_CHAT_TOKEN=tok_dotenv\n")
    monkeypatch.setattr(pn.post_to_kiosk, "MESSAGE_FILE", str(kiosk))
    monkeypatch.setattr(pn, "CHAT_FILE", str(chat))
    monkeypatch.setattr(pn, "DOTENV", str(dotenv))
    for name in ("PLOW_CHAT_BASE_URL", "PLOW_CHAT_CHAT_UID", "PLOW_CHAT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["post_nudge.py"])

    calls = []
    monkeypatch.setattr(pn.post_to_kiosk, "main",
                        lambda consume=True: calls.append(("kiosk", consume)))
    monkeypatch.setattr(
        pn.post_to_kiosk, "post_bearer_json",
        lambda url, token, body, label: calls.append(("chat", url, token, body)))
    return SimpleNamespace(kiosk=kiosk, chat=chat, dotenv=dotenv,
                           calls=calls, monkeypatch=monkeypatch)


def test_success_runs_kiosk_then_chat_then_consumes_both(rig):
    pn.main()
    assert rig.calls == [
        ("kiosk", False),
        ("chat", "https://dotenv.test/v1/chats/cht_dotenv/messages",
         "tok_dotenv", {"body": 'Heads up: "Standup" at 12:20pm (20m).\n'
                        'Heads up: "Sync" at 12:40pm (40m).'}),
    ]
    assert not rig.kiosk.exists() and not rig.chat.exists(), (
        "the coordinator owns consume-on-success for both handoffs"
    )


def test_env_wins_over_dotenv(rig):
    rig.monkeypatch.setenv("PLOW_CHAT_CHAT_UID", "cht_env")
    pn.main()
    assert "/v1/chats/cht_env/messages" in rig.calls[1][1]


@pytest.mark.parametrize("missing", [
    "PLOW_CHAT_BASE_URL", "PLOW_CHAT_CHAT_UID", "PLOW_CHAT_TOKEN"])
def test_a_missing_credential_refuses_before_anything_posts(rig, missing):
    """The half-delivered trap this ordering exists for: a blank chat config
    must stop the run BEFORE the kiosk posts and consumes its handoff."""
    rig.dotenv.write_text("".join(
        f"{n}=value\n" for n in
        ("PLOW_CHAT_BASE_URL", "PLOW_CHAT_CHAT_UID", "PLOW_CHAT_TOKEN")
        if n != missing))
    with pytest.raises(SystemExit) as excinfo:
        pn.main()
    assert missing in str(excinfo.value)
    assert rig.calls == [], "nothing may post on a broken chat config"
    assert rig.kiosk.exists() and rig.chat.exists()


def test_an_empty_chat_handoff_refuses_before_anything_posts(rig):
    rig.chat.write_text("   ")
    with pytest.raises(SystemExit):
        pn.main()
    assert rig.calls == []
    assert rig.kiosk.exists()


def test_a_kiosk_failure_stops_before_chat_and_leaves_both_files(rig):
    rig.monkeypatch.setattr(
        pn.post_to_kiosk, "main",
        lambda consume=True: sys.exit("error: message API returned HTTP 500"))
    with pytest.raises(SystemExit):
        pn.main()
    assert rig.calls == []
    assert rig.kiosk.exists() and rig.chat.exists()


def test_a_chat_failure_leaves_both_files_for_a_retry(rig):
    rig.monkeypatch.setattr(
        pn.post_to_kiosk, "post_bearer_json",
        lambda *a: sys.exit("error: Plow Chat returned HTTP 500"))
    with pytest.raises(SystemExit):
        pn.main()
    assert rig.calls == [("kiosk", False)]
    assert rig.kiosk.exists() and rig.chat.exists(), (
        "a chat retry re-posts the kiosk (harmless latest-wins) rather than "
        "finding its handoff already consumed"
    )


def test_dry_run_previews_without_consuming(rig, capsys):
    rig.monkeypatch.setattr(sys, "argv", ["post_nudge.py", "--dry-run"])
    pn.main()
    assert rig.calls == [("kiosk", False)], "no chat POST on a dry run"
    assert rig.kiosk.exists() and rig.chat.exists()
    assert "dry-run" in capsys.readouterr().out
