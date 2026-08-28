"""The nudge poster's own leg: ordering and the silent-drop refusals.

The kiosk leg is post_to_kiosk's and tested there. What this wrapper adds --
and what nothing else covers -- is the chat leg and its ordering: a missing
chat env must refuse BEFORE the kiosk leg posts and consumes the handoff (a
half-configured install would otherwise post the card and message nobody,
every tick, with nothing left to retry), the chat leg must not run at all
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
def test_a_missing_chat_variable_refuses_before_the_kiosk_posts(
    tmp_path, monkeypatch, missing
):
    """The refusal must land before the kiosk leg runs: post_to_kiosk consumes
    the handoff on success, so a late refusal posts the card, burns the file,
    and silently drops the chat reminder every tick."""
    mod = load(tmp_path)
    for name, value in CHAT_ENV.items():
        monkeypatch.setenv(name, "" if name == missing else value)
    kiosk_posts = []
    monkeypatch.setattr(mod.post_to_kiosk, "main", lambda: kiosk_posts.append(1))
    with pytest.raises(SystemExit) as excinfo:
        mod.main()
    assert missing in str(excinfo.value)
    assert kiosk_posts == []
    assert Path(mod.post_to_kiosk.MESSAGE_FILE).exists()


def test_a_failed_kiosk_post_never_reaches_chat(tmp_path, monkeypatch):
    """The sheet's ordering rule, as code: kiosk first, chat only on success."""
    mod = load(tmp_path)
    for name, value in CHAT_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        mod.post_to_kiosk, "main",
        lambda consume=True: sys.exit("error: message API returned HTTP 500"))
    sent = []
    monkeypatch.setattr(mod, "send_chat", lambda *a, **k: sent.append(a))
    with pytest.raises(SystemExit):
        mod.main()
    assert sent == []


def test_the_handoff_survives_a_chat_failure_and_is_consumed_on_success(
    tmp_path, monkeypatch
):
    """The :20 run of a :30 meeting gets exactly one shot at composing; if the
    chat leg fails transiently after the kiosk leg, the handoff must stay on
    disk so a retry resends it -- the :50 recompose has already moved past the
    event. Only a fully successful run consumes it."""
    mod = load(tmp_path)
    for name, value in CHAT_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(mod.post_to_kiosk, "main", lambda consume=True: None)

    monkeypatch.setattr(
        mod.post_to_kiosk, "post_bearer_json",
        lambda *a, **k: sys.exit("error: Plow Chat returned HTTP 502"))
    with pytest.raises(SystemExit):
        mod.main()
    assert Path(mod.post_to_kiosk.MESSAGE_FILE).exists(), "retry needs the file"

    monkeypatch.setattr(mod.post_to_kiosk, "post_bearer_json", lambda *a, **k: None)
    mod.main()
    assert not Path(mod.post_to_kiosk.MESSAGE_FILE).exists()


def test_dry_run_prints_a_redacted_envelope_and_sends_nothing(
    tmp_path, monkeypatch, capsys
):
    mod = load(tmp_path, text="secret meeting title")

    def refuse_network(*_a, **_k):
        raise AssertionError("dry run must not POST")

    monkeypatch.setattr(mod.post_to_kiosk, "post_bearer_json", refuse_network)
    mod.send_chat(
        "secret meeting title", True, "http://gateway.test", "cht_test", "tok_test"
    )
    out = capsys.readouterr().out
    envelope = json.loads(out)
    assert envelope["url"] == "http://gateway.test/v1/chats/cht_test/messages"
    assert "secret meeting title" not in out
    assert "tok_test" not in out
