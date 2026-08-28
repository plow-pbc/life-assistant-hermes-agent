#!/usr/bin/env python3
"""Tests for verify_deploy.py — the deploy-verification poller of ld-viewer-dev.

The script's contract: `verify_deploy.py <sha> [--timeout N]` polls
GET <base>/api/version — base URL derived from DASHBOARD_ENDPOINT_URL by
stripping its `/api/message` suffix — until the kiosk reports the given SHA.
Exit 0 on a live match, exit 1 on timeout (printing the last response so a
rollback is visible), exit 2 on missing/malformed env. These tests import the
module and rebind POLL_INTERVAL / the env var — the same importer-only seam the
ld-shared suite uses.

Like every vendored ld- suite, outcomes go through check() and the exit code is
the verdict; tests/test_vendored_suites.py runs this file as a subprocess.
"""
import contextlib
import io
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_deploy  # noqa: E402

passed = failed = 0


def check(label, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"PASS - {label}")
    else:
        failed += 1
        print(f"FAIL - {label}")


def run(*args):
    """Invoke verify_deploy.main() with the given CLI args.

    Returns (exit_code, combined stdout+stderr text). A clean return from
    main() is exit 0, matching the CLI.
    """
    out, err = io.StringIO(), io.StringIO()
    code = 0
    saved_argv = sys.argv
    sys.argv = ["verify_deploy.py", *args]
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            verify_deploy.main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    finally:
        sys.argv = saved_argv
    return code, out.getvalue() + err.getvalue()


class _VersionHandler(BaseHTTPRequestHandler):
    """Serves GET /api/version with a class-set payload; records every path."""

    payload = {"sha": None, "deployedAt": None}
    requests = []

    def do_GET(self):
        type(self).requests.append(self.path)
        body = json.dumps(type(self).payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class _RedirectHandler(BaseHTTPRequestHandler):
    """302s /api/version to /elsewhere, which would report the wanted SHA."""

    requests = []

    def do_GET(self):
        type(self).requests.append(self.path)
        if self.path == "/elsewhere":
            body = json.dumps({"sha": "abc123", "deployedAt": "x"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(302)
        self.send_header("Location", "/elsewhere")
        self.end_headers()

    def log_message(self, *_args):
        pass


def _start_server(handler_cls=_VersionHandler):
    handler_cls.requests = []
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _use_endpoint(base):
    """Point the env at a kiosk and make polling test-fast."""
    os.environ[verify_deploy.ENDPOINT_ENV] = f"{base}/api/message"
    verify_deploy.POLL_INTERVAL = 0.05


def _reset():
    os.environ.pop(verify_deploy.ENDPOINT_ENV, None)
    verify_deploy.POLL_INTERVAL = 5.0


# ────────────────────────── tests ──────────────────────────


def test_success_on_live_sha_match_and_base_url_derivation():
    """Exit 0 when /api/version reports the SHA — and the polled path proves the
    base URL came from DASHBOARD_ENDPOINT_URL minus its /api/message suffix."""
    server, base = _start_server()
    try:
        _use_endpoint(base)
        _VersionHandler.payload = {"sha": "abc123", "deployedAt": "2026-08-28T00:00:00Z"}
        code, out = run("abc123", "--timeout", "5")
    finally:
        server.shutdown()
        _reset()
    check("live SHA match exits zero", code == 0)
    check("polled path is exactly /api/version (suffix stripped, not appended)",
          _VersionHandler.requests and set(_VersionHandler.requests) == {"/api/version"})
    check("success output names the live sha", "abc123" in out)


def test_timeout_on_mismatch_prints_last_response():
    """A kiosk stuck on another SHA (a rollback) → exit 1, last response shown
    verbatim so the report can carry what the kiosk actually said."""
    server, base = _start_server()
    try:
        _use_endpoint(base)
        _VersionHandler.payload = {"sha": "old111", "deployedAt": "2026-08-27T00:00:00Z"}
        code, out = run("new222", "--timeout", "0.2")
    finally:
        server.shutdown()
        _reset()
    check("mismatch exits 1 on timeout", code == 1)
    check("timeout output carries the last response verbatim", "old111" in out)


def test_exit_2_on_missing_or_malformed_env():
    """No env, empty env, a URL without the /api/message suffix, and a
    non-http(s) scheme all fail fast with exit 2 — before any polling."""
    _reset()
    for label, value in (
        ("unset env", None),
        ("empty env", "   "),
        ("no /api/message suffix", "http://kiosk.test/api/other"),
        ("non-http scheme", "ftp://kiosk.test/api/message"),
    ):
        if value is None:
            os.environ.pop(verify_deploy.ENDPOINT_ENV, None)
        else:
            os.environ[verify_deploy.ENDPOINT_ENV] = value
        code, _ = run("abc123", "--timeout", "1")
        check(f"{label} exits 2", code == 2)
    _reset()


def test_redirect_refused():
    """A 302 must not be followed to a body that would report the SHA — the
    poller treats it as a failed probe and times out instead of trusting it."""
    server, base = _start_server(_RedirectHandler)
    try:
        _use_endpoint(base)
        code, _ = run("abc123", "--timeout", "0.2")
    finally:
        server.shutdown()
        _reset()
    check("redirected probe never claims success", code == 1)
    check("redirect target never fetched", "/elsewhere" not in _RedirectHandler.requests)


def main():
    test_success_on_live_sha_match_and_base_url_derivation()
    test_timeout_on_mismatch_prints_last_response()
    test_exit_2_on_missing_or_malformed_env()
    test_redirect_refused()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
