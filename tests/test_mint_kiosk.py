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
        self._send({"uid": "kio_test", "pairing_code": "ABC123", "expires_at": "2026-08-28T20:00:00Z"})

    def do_GET(self):
        type(self).calls.append(("GET", self.path, self.headers.get("Authorization"), None))
        paired = type(self).paired_at
        self._send({"uid": "kio_test", "name": "Life dashboard", "paired_at": paired,
                    "status": {"sha": "abc1234" if paired else None, "deployed_at": paired,
                               "last_result": "ok" if paired else None, "reported_at": paired}})

    def log_message(self, *_args):
        pass


@pytest.fixture
def plow(tmp_path):
    _Plow.calls, _Plow.paired_at = [], None
    server = HTTPServer(("127.0.0.1", 0), _Plow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
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
    assert "pi_line_1=sudo apt install -y nodejs npm git chromium fonts-noto-color-emoji" in out
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


@pytest.mark.parametrize("paired_at,rc", [(None, 1), ("2026-08-28T19:00:00Z", 0)],
                         ids=["not-yet", "paired-and-deployed"])
def test_status_reports_pairing_and_the_deployed_sha(plow, capsys, paired_at, rc):
    mk.main([], dotenv_path=str(plow.dotenv))
    plow.handler.paired_at = paired_at
    assert mk.main(["--status"], dotenv_path=str(plow.dotenv)) == rc
    out = capsys.readouterr().out
    assert f"paired_at={paired_at}" in out and ("sha=abc1234" in out) == bool(paired_at)
