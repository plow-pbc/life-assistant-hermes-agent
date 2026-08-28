"""The kiosk is minted once, and the dotenv gains exactly two lines, once.

A loopback HTTP server stands in for Plow (the same shape
ld-shared/scripts/test_post_to_kiosk.py uses for the kiosk), so the bearer,
the path and the body are asserted as they arrive on the wire. The dotenv
deliberately ends WITHOUT a newline: the leading-newline append is the
contract, and a file that already ended in one would pass with or without it.
"""
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("mint_kiosk", ROOT / "ld-setup" / "scripts" / "mint_kiosk.py")
mk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mk)

TOKEN = "tok_test"


class _Plow(BaseHTTPRequestHandler):
    paired_at = None
    cards = {}
    mint_uid = "kio_test"
    pairing_code = "ABC123"
    calls = []

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        type(self).calls.append(("POST", self.path, self.headers.get("Authorization"),
                                 json.loads(self.rfile.read(n) or b"{}")))
        self._send({"uid": type(self).mint_uid, "pairing_code": type(self).pairing_code,
                    "expires_at": "2026-08-28T20:00:00Z"})

    def do_GET(self):
        type(self).calls.append(("GET", self.path, self.headers.get("Authorization"), None))
        paired = type(self).paired_at
        self._send({"uid": "kio_test", "name": "Life dashboard", "paired_at": paired,
                    "cards": type(self).cards,
                    "status": {"sha": "abc1234" if paired else None, "deployed_at": paired,
                               "last_result": "ok" if paired else None, "reported_at": paired}})

    def log_message(self, *_args):
        pass


@pytest.fixture
def plow(tmp_path, monkeypatch):
    _Plow.calls, _Plow.paired_at, _Plow.cards = [], None, {}
    _Plow.mint_uid, _Plow.pairing_code = "kio_test", "ABC123"
    server = HTTPServer(("127.0.0.1", 0), _Plow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    # The trusted base comes from the environment now (mint_kiosk.resolve_base),
    # never the dotenv — see test_a_dotenv_plow_api_base_is_ignored.
    monkeypatch.setenv("PLOW_API_BASE", base)
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"PLOW_AGENT_TOKEN={TOKEN}\nPLOW_API_BASE={base}")  # no trailing newline, on purpose
    yield SimpleNamespace(base=base, dotenv=dotenv, calls=_Plow.calls, handler=_Plow)
    server.shutdown()


def test_a_first_run_mints_appends_two_lines_and_prints_the_owner_lines(plow, capsys):
    before = plow.dotenv.read_text()
    assert mk.main([], dotenv_path=str(plow.dotenv)) == 0
    assert plow.dotenv.read_text() == (
        before + f"\nDASHBOARD_ENDPOINT_URL={plow.base}/v1/kiosks/kio_test/cards\nDASHBOARD_TOKEN={TOKEN}\n"
    )
    assert plow.calls == [("POST", "/v1/kiosks", f"Bearer {TOKEN}", {"name": "Life dashboard"})]
    out = capsys.readouterr().out
    # Bare `key=value` lines, one per line, nothing shell-wrapped around the
    # value: the agent lifts the value straight into an ssh argv element
    # (["ssh", "<user>@<pi>", "<line>"]) with no further parsing.
    # apt-get (not apt, whose "WARNING: ... stable CLI interface" the skill reads
    # as a failed phase), and `sudo env` so env_reset cannot drop the frontend.
    assert ("pi_line_1=sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y"
            " nodejs npm git chromium fonts-noto-color-emoji") in out
    assert ("pi_line_2=curl -fsSL https://raw.githubusercontent.com/plow-pbc/life-dashboard/main/updater/bootstrap.sh"
            " | sh -s -- --pair ABC123") in out


@pytest.mark.parametrize("paired_at,posts_after", [
    (None, 1),                          # unpaired: the code is re-minted, the file is not
    ("2026-08-28T19:00:00Z", 0),        # paired: nothing to mint
], ids=["unpaired-re-mints-the-code", "paired-leaves-everything"])
def test_a_second_run_never_appends_again(plow, capsys, paired_at, posts_after):
    mk.main([], dotenv_path=str(plow.dotenv))
    after_first = plow.dotenv.read_text()
    capsys.readouterr()  # discard the first (minting) run's own pi_line_* output
    plow.calls.clear()
    plow.handler.paired_at = paired_at
    assert mk.main([], dotenv_path=str(plow.dotenv)) == 0
    assert plow.dotenv.read_text() == after_first
    assert plow.calls[0] == ("GET", "/v1/kiosks/kio_test", f"Bearer {TOKEN}", None)
    assert sum(c[0] == "POST" for c in plow.calls) == posts_after
    assert ("--pair ABC123" in capsys.readouterr().out) == bool(posts_after)


@pytest.mark.parametrize("dotenv_text", ["PLOW_AGENT_TOKEN=\n", "PLOW_AGENT_TOKEN=   \n", ""],
                         ids=["empty", "blank", "absent"])
def test_a_blank_token_refuses_before_any_request(plow, dotenv_text):
    plow.dotenv.write_text(dotenv_text)
    with pytest.raises(SystemExit) as e:
        mk.main([], dotenv_path=str(plow.dotenv))
    assert "PLOW_AGENT_TOKEN" in str(e.value)
    assert plow.calls == [] and plow.dotenv.read_text() == dotenv_text


@pytest.mark.parametrize("paired_at,cards,rc", [
    (None, {}, 1),
    ("2026-08-28T19:00:00Z", {"3": {"type": "weather"}}, 0),
], ids=["not-yet", "paired-deployed-and-carrying-a-card"])
def test_status_reports_pairing_the_deployed_sha_and_the_cards(plow, capsys, paired_at, cards, rc):
    """The cards are the point: Phase 3's proof is card 3 on the kiosk, and the
    status route returns them, so the agent never has to take the wall on faith."""
    mk.main([], dotenv_path=str(plow.dotenv))
    plow.handler.paired_at, plow.handler.cards = paired_at, cards
    assert mk.main(["--status"], dotenv_path=str(plow.dotenv)) == rc
    out = capsys.readouterr().out
    assert f"paired_at={paired_at}" in out and ("sha=abc1234" in out) == bool(paired_at)
    assert f"cards={sorted(cards)}" in out


def test_a_re_mint_that_returns_a_different_kiosk_refuses_and_leaves_the_dotenv(plow):
    """The re-POST on an unpaired kiosk must come back as the SAME kiosk. A
    different uid means the dotenv now points at a kiosk nobody will pair."""
    mk.main([], dotenv_path=str(plow.dotenv))
    after_first = plow.dotenv.read_text()
    plow.handler.mint_uid = "kio_other"
    with pytest.raises(SystemExit) as e:
        mk.main([], dotenv_path=str(plow.dotenv))
    assert "kio_other" in str(e.value) and "kio_test" in str(e.value)
    assert plow.dotenv.read_text() == after_first


def test_a_dotenv_plow_api_base_is_ignored(plow, monkeypatch):
    """/opt/data/.env is agent-writable at runtime; an injected line there must
    not redirect where this instance's own bearer gets minted. Rewrite the
    dotenv's PLOW_API_BASE to a different host and confirm the mint still
    lands on the trusted (env-sourced) base, not the tampered dotenv value."""
    tampered = plow.dotenv.read_text().replace(plow.base, "https://attacker.test")
    plow.dotenv.write_text(tampered)
    assert mk.main([], dotenv_path=str(plow.dotenv)) == 0
    assert plow.calls == [("POST", "/v1/kiosks", f"Bearer {TOKEN}", {"name": "Life dashboard"})]
    monkeypatch.delenv("PLOW_API_BASE", raising=False)
    assert mk.resolve_base() == mk.DEFAULT_BASE


def test_a_pairing_code_with_shell_metacharacters_refuses_before_printing(plow, capsys):
    """pi_line_2 lands verbatim in an ssh argv element -- an unvalidated code
    from the server is a remote command injection sink, not just a typo risk."""
    plow.handler.pairing_code = "ABC; rm -rf /"
    with pytest.raises(SystemExit) as e:
        mk.main([], dotenv_path=str(plow.dotenv))
    assert "pairing_code" in str(e.value)
    assert "pi_line_2" not in capsys.readouterr().out
