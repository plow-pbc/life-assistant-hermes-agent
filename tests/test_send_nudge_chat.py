"""tests/test_send_nudge_chat.py — behavior tests for the nudge's chat leg.

The script carries the PLOW_CHAT_TOKEN bearer, so its contract mirrors
post_to_kiosk's: fixed non-argv inputs, env-then-dotenv credentials, loud
failure on any non-2xx, redirects refused, and consume-on-success as the
LAST leg of the two-leg handoff. These tests import the module and rebind
its path constants — a seam reachable only by an importer, never by the CLI
the sheet invokes.
"""
import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "ld-calendar-nudge" / "scripts" / "send_nudge_chat.py"

spec = importlib.util.spec_from_file_location("send_nudge_chat", SCRIPT)
snc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snc)
REAL_OPENER = snc._no_redirect_opener  # kept so the redirect test can restore it


class FakeOpener:
    """Records the request; optionally raises the way urllib would."""

    def __init__(self, status=200, error=None):
        self.status, self.error, self.requests = status, error, []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if self.error:
            raise self.error
        opener = self

        class _Resp:
            status = opener.status
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        return _Resp()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A handoff + dotenv on disk, env cleared, opener faked."""
    handoff = tmp_path / "calendar-nudge-text"
    handoff.write_text('Heads up: "Standup" at 12:20pm (20m).')
    dotenv = tmp_path / ".env"
    dotenv.write_text("PLOW_CHAT_BASE_URL=https://dotenv.test\n"
                      "PLOW_CHAT_CHAT_UID=cht_dotenv\n"
                      "PLOW_CHAT_TOKEN=tok_dotenv\n")
    monkeypatch.setattr(snc, "HANDOFF", str(handoff))
    monkeypatch.setattr(snc, "DOTENV", str(dotenv))
    for name in ("PLOW_CHAT_BASE_URL", "PLOW_CHAT_CHAT_UID", "PLOW_CHAT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    opener = FakeOpener()
    monkeypatch.setattr(snc, "_no_redirect_opener", lambda: opener)
    return handoff, dotenv, opener, monkeypatch


def test_success_posts_the_handoff_body_and_consumes_it(rig, capsys):
    handoff, _, opener, _ = rig
    text = handoff.read_text().strip()
    snc.main()
    (req,) = opener.requests
    assert req.full_url == "https://dotenv.test/v1/chats/cht_dotenv/messages"
    assert json.loads(req.data) == {"body": text}
    assert req.get_header("Authorization") == "Bearer tok_dotenv"
    assert not handoff.exists(), "the last leg owns consume-on-success"


def test_env_wins_over_dotenv(rig):
    handoff, _, opener, monkeypatch = rig
    monkeypatch.setenv("PLOW_CHAT_CHAT_UID", "cht_env")
    snc.main()
    (req,) = opener.requests
    assert "/v1/chats/cht_env/messages" in req.full_url


@pytest.mark.parametrize("missing", [
    "PLOW_CHAT_BASE_URL", "PLOW_CHAT_CHAT_UID", "PLOW_CHAT_TOKEN"])
def test_a_missing_credential_refuses_by_name(rig, missing):
    handoff, dotenv, opener, _ = rig
    dotenv.write_text("".join(
        f"{n}=value\n" for n in
        ("PLOW_CHAT_BASE_URL", "PLOW_CHAT_CHAT_UID", "PLOW_CHAT_TOKEN")
        if n != missing))
    with pytest.raises(SystemExit) as excinfo:
        snc.main()
    assert missing in str(excinfo.value)
    assert not opener.requests, "no request may carry a half-built target"
    assert handoff.exists(), "a failed run must leave the handoff for a retry"


@pytest.mark.parametrize("error", [
    urllib.error.HTTPError("u", 500, "boom", None, io.BytesIO()),
    urllib.error.URLError("unreachable"),
], ids=["http-500", "transport"])
def test_a_failed_send_exits_loud_and_preserves_the_handoff(rig, error):
    handoff, _, opener, _ = rig
    opener.error = error
    with pytest.raises(SystemExit) as excinfo:
        snc.main()
    assert "Plow Chat send failed" in str(excinfo.value)
    assert handoff.exists()


def test_an_empty_handoff_sends_nothing(rig):
    handoff, _, opener, _ = rig
    handoff.write_text("   ")
    with pytest.raises(SystemExit):
        snc.main()
    assert not opener.requests


def test_a_real_redirect_fails_loud_and_is_never_followed(rig):
    """A 3xx must surface as an error, never a re-POST of the bearer to the
    redirect target — the same threat post_to_kiosk's opener guards, proven
    against a real 302 through the REAL opener (the fake is bypassed)."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading

    followed = []

    class _Redirect(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path.startswith("/stolen"):
                followed.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/stolen")
            self.end_headers()

        def log_message(self, *_):
            pass

    handoff, dotenv, _, monkeypatch = rig
    server = HTTPServer(("127.0.0.1", 0), _Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        dotenv.write_text(
            f"PLOW_CHAT_BASE_URL=http://127.0.0.1:{server.server_port}\n"
            "PLOW_CHAT_CHAT_UID=cht_x\nPLOW_CHAT_TOKEN=tok_x\n")
        monkeypatch.setattr(snc, "_no_redirect_opener", REAL_OPENER)
        with pytest.raises(SystemExit) as excinfo:
            snc.main()
    finally:
        server.shutdown()
    assert "Plow Chat send failed" in str(excinfo.value)
    assert not followed, "the bearer must never chase the redirect target"
    assert handoff.exists()
