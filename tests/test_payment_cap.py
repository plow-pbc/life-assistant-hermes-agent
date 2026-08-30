"""tests/test_payment_cap.py — behavior tests for the advisory daily guideline.

The assertion is the advisory verdict a caller reads (WITHIN / EXCEEDS) and the
exit code, driven through the same CLI the SKILL.md tells the agent to run — so
a change to the cap value or the boundary rule fails here rather than as a
mis-decided payment.
"""
import contextlib
import io
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ld-payments" / "scripts" / "payment_cap.py"
sys.path.insert(0, str(SCRIPT.parent))
import payment_cap  # noqa: E402


def run(spent, amount):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            code = payment_cap.main(["--spent-today", str(spent), "--amount", str(amount)])
        except SystemExit as exc:
            code = int(exc.code)
    return subprocess.CompletedProcess([], code, stdout.getvalue(), stderr.getvalue())


@pytest.fixture(autouse=True)
def cap_server(monkeypatch, tmp_path):
    state = {
        "status": 200,
        "redirect_to": None,
        "redirect_followed": False,
        "payload": {"daily_payment_cap_usd": "200.000000",
                    "memory_notifications_enabled": False},
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/capture":
                state["redirect_followed"] = True
                body = json.dumps(state["payload"]).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            assert self.path == "/v1/api-keys/current/preferences"
            assert self.headers["Authorization"] == "Bearer not-a-secret"
            body = json.dumps(state["payload"]).encode()
            self.send_response(state["status"])
            if state["redirect_to"]:
                self.send_header("Location", state["redirect_to"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runtime_env = tmp_path / ".env"
    runtime_env.write_text("DOMO_MCP_TOKEN=not-a-secret\n", encoding="utf-8")
    monkeypatch.setattr(payment_cap, "PLOW_API_ORIGIN", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("PLOW_RUNTIME_ENV_FILE", str(runtime_env))
    monkeypatch.delenv("DOMO_MCP_TOKEN", raising=False)

    def configure(payload=None, *, status=200, redirect=False):
        state["status"] = status
        state["redirect_to"] = (
            f"http://127.0.0.1:{server.server_port}/capture" if redirect else None
        )
        if payload is not None:
            state["payload"] = payload
        return state

    yield configure
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.parametrize(("cap", "amount", "message"), [
    ("500.000000", 234.55, "$500.00"),
    (None, 10000, "no daily cap configured"),
])
def test_dashboard_cap_outcomes(cap_server, cap, amount, message):
    cap_server({
        "daily_payment_cap_usd": cap,
        "memory_notifications_enabled": False,
    })
    result = run(200, amount)

    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[0] == "WITHIN"
    assert message in result.stdout


def test_preference_read_failure_stops_loudly(cap_server):
    cap_server(status=503)
    result = run(0, 1)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "could not read the current daily payment cap" in result.stderr


def test_preference_redirect_is_refused_without_forwarding_the_bearer(cap_server):
    state = cap_server(status=302, redirect=True)

    result = run(0, 1)

    assert result.returncode == 2
    assert state["redirect_followed"] is False


@pytest.mark.parametrize(("spent", "amount", "verdict"), [
    (0, 50, "WITHIN"),
    (150, 50, "WITHIN"),     # lands exactly on the cap — at-or-under is within
    (150, 50.01, "EXCEEDS"),  # a cent over the cap
    (0, 200, "WITHIN"),
    (0, 200.01, "EXCEEDS"),
    (200, 0.01, "EXCEEDS"),   # the day is already at the cap
    (0, 0, "WITHIN"),
], ids=["fresh", "exactly-at-cap", "cent-over", "single-at-cap",
        "single-over", "already-maxed", "zero"])
def test_verdict_is_the_first_token_and_the_boundary_is_inclusive(spent, amount, verdict):
    r = run(spent, amount)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[0] == verdict


@pytest.mark.parametrize(("spent", "amount"), [
    (-1, 50),
    (50, -1),
    ("nan", 50),
    (50, "inf"),
    ("-inf", 50),
], ids=["negative-spent", "negative-amount", "nan-spent", "inf-amount",
        "neg-inf-spent"])
def test_malformed_inputs_fail_loudly(spent, amount):
    # A negative or non-finite amount is upstream drift, not a payment — exit
    # loud (a controlled exit 2, never a raw traceback), don't silently treat a
    # negative as a credit that frees up budget or let nan/inf crash the check.
    r = run(spent, amount)
    assert r.returncode == 2
    assert r.stdout == ""


def test_missing_arguments_fail_loudly():
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--amount", "50"],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
