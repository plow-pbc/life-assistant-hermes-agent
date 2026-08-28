"""The nudge poster's own leg: ordering and the silent-drop refusals.

The kiosk leg is post_to_kiosk's and tested there. What this wrapper adds --
and what nothing else covers -- is the chat leg: it must refuse loudly when
its env is absent (a half-configured install would otherwise post the card
and message nobody, every tick, in front of nobody), it must not run at all
when the kiosk post failed, and its dry run must send nothing.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(tmp_path, text="Heads up: \"Standup\" at 9:00am (25m) — online."):
    spec = importlib.util.spec_from_file_location(
        "post_nudge", ROOT / "ld-calendar-nudge" / "scripts" / "post_nudge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    handoff = tmp_path / "calendar-nudge-text"
    handoff.write_text(text)
    mod.post_to_kiosk.MESSAGE_FILE = str(handoff)
    return mod


CHAT_ENV = {
    "PLOW_CHAT_BASE_URL": "http://gateway.test",
    "PLOW_CHAT_CHAT_UID": "cht_test",
    "PLOW_CHAT_TOKEN": "tok_test",
}


@pytest.mark.parametrize("missing", sorted(CHAT_ENV))
def test_a_missing_chat_variable_refuses_by_name(tmp_path, monkeypatch, missing):
    mod = load(tmp_path)
    for name, value in CHAT_ENV.items():
        monkeypatch.setenv(name, "" if name == missing else value)
    with pytest.raises(SystemExit) as excinfo:
        mod.send_chat("hi", dry_run=False)
    assert missing in str(excinfo.value)


def test_a_failed_kiosk_post_never_reaches_chat(tmp_path, monkeypatch):
    """The sheet's ordering rule, as code: kiosk first, chat only on success."""
    mod = load(tmp_path)
    monkeypatch.setattr(
        mod.post_to_kiosk, "main",
        lambda: sys.exit("error: message API returned HTTP 500"))
    sent = []
    monkeypatch.setattr(mod, "send_chat", lambda *a, **k: sent.append(a))
    with pytest.raises(SystemExit):
        mod.main()
    assert sent == []


def test_dry_run_prints_a_redacted_envelope_and_sends_nothing(
    tmp_path, monkeypatch, capsys
):
    mod = load(tmp_path, text="secret meeting title")
    for name, value in CHAT_ENV.items():
        monkeypatch.setenv(name, value)

    def refuse_network(*_a, **_k):
        raise AssertionError("dry run must not open a connection")

    monkeypatch.setattr(mod.post_to_kiosk, "_no_redirect_opener", refuse_network)
    mod.send_chat("secret meeting title", dry_run=True)
    out = capsys.readouterr().out
    envelope = json.loads(out)
    assert envelope["url"] == "http://gateway.test/v1/chats/cht_test/messages"
    assert "secret meeting title" not in out
    assert "tok_test" not in out
