#!/usr/bin/env python3
"""Tests for post_to_kiosk.py — the shared POST helper every ld- producer uses.

The helper reads the message text from one of two fixed sources (stdin, or a
caller-set MESSAGE_FILE) and the endpoint URL + bearer token file-first-then-env.
The body shape (CARD + BODY_TYPE, plus an optional TITLE) is set by each
producer's thin wrapper before calling main(). These tests import the module and
rebind those constants to scratch files / env — a seam reachable only by an
importer, never by the CLI a scheduled agent invokes.

Both transports are exercised so this one canonical helper is proven to serve
the Plow agent seed (file secrets + stdin) AND the Hermes agent seed (env
secrets + MESSAGE_FILE handoff). Producer wrappers are NOT tested here — they
live in the consuming seed repos; each seed tests its own wrappers against this
helper after pulling it as ld-shared.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import post_to_kiosk  # noqa: E402

TOKEN = "test-token-abc"
passed = failed = 0


def check(label, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"PASS - {label}")
    else:
        failed += 1
        print(f"FAIL - {label}")


def run(*args, stdin_text=""):
    """Invoke post_to_kiosk.main() with the given CLI args and stdin.

    Returns (exit_code, stdout_text). stdin is only consumed when MESSAGE_FILE
    is unset (the stdin transport).
    """
    out = io.StringIO()
    code = 0
    saved_argv, saved_stdin = sys.argv, sys.stdin
    sys.argv = ["post_to_kiosk.py", *args]
    sys.stdin = io.StringIO(stdin_text)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            post_to_kiosk.main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    finally:
        sys.argv, sys.stdin = saved_argv, saved_stdin
    return code, out.getvalue()


def reset_module():
    """Restore module constants to defaults so a test cannot leak into another."""
    post_to_kiosk.CARD = None
    post_to_kiosk.BODY_TYPE = None
    post_to_kiosk.TITLE = None
    post_to_kiosk.MESSAGE_FILE = None
    post_to_kiosk.ENDPOINT_FILE = "/config/secrets/dashboard-endpoint-url"
    post_to_kiosk.TOKEN_FILE = "/config/secrets/dashboard-token"
    os.environ.pop(post_to_kiosk.ENDPOINT_ENV, None)
    os.environ.pop(post_to_kiosk.TOKEN_ENV, None)


def use_file_secrets(tmp: Path, endpoint="https://x.test/api/message", card="1", body_type="alert"):
    """File transport: write the two secret files and rebind the file paths.

    Points the env-var names at a directory with no env set, so file-first is
    what resolves. Returns (endpoint_file, token_file).
    """
    reset_module()
    endpoint_file = tmp / "dashboard-endpoint-url"
    token_file = tmp / "dashboard-token"
    endpoint_file.write_text(endpoint)
    token_file.write_text(TOKEN)
    post_to_kiosk.CARD = card
    post_to_kiosk.BODY_TYPE = body_type
    post_to_kiosk.ENDPOINT_FILE = str(endpoint_file)
    post_to_kiosk.TOKEN_FILE = str(token_file)
    return endpoint_file, token_file


def use_env_secrets(tmp: Path, endpoint="https://x.test/api/message", card="1", body_type="alert"):
    """Env transport: point the file paths at nonexistent files and set env vars."""
    reset_module()
    post_to_kiosk.CARD = card
    post_to_kiosk.BODY_TYPE = body_type
    post_to_kiosk.ENDPOINT_FILE = str(tmp / "nonexistent-endpoint")
    post_to_kiosk.TOKEN_FILE = str(tmp / "nonexistent-token")
    os.environ[post_to_kiosk.ENDPOINT_ENV] = endpoint
    os.environ[post_to_kiosk.TOKEN_ENV] = TOKEN


class _CapturingHandler(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        type(self).received.append(
            {
                "path": self.path,
                "auth": self.headers.get("Authorization", ""),
                "content_type": self.headers.get("Content-Type", ""),
                "body": json.loads(body),
            }
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *_args):
        pass


def _start_server(handler_cls=_CapturingHandler):
    """Start a throwaway loopback server with the given handler; return (server, base_url).

    Resets _CapturingHandler.received so the capturing tests see only their own
    POST. The non-capturing handlers (500, redirect) ignore it.
    """
    _CapturingHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


# ────────────────────────── tests ──────────────────────────


def test_file_secrets_stdin_message_posts_correct_payload():
    """Plow transport: file secrets + message on stdin. http:// accepted (LAN)."""
    server, base = _start_server()
    try:
        with tempfile.TemporaryDirectory() as d:
            use_file_secrets(Path(d), endpoint=f"{base}/api/message", body_type="alert")
            code, _ = run(stdin_text="follow up with Stephanie")
    finally:
        server.shutdown()
        reset_module()
    check("file+stdin POST exit zero", code == 0)
    check("server received exactly one POST", len(_CapturingHandler.received) == 1)
    if _CapturingHandler.received:
        r = _CapturingHandler.received[0]
        check("path is /api/message", r["path"] == "/api/message")
        check("auth header is bearer + token", r["auth"] == f"Bearer {TOKEN}")
        check("content-type is application/json", r["content_type"] == "application/json")
        check("body card matches CARD", r["body"]["card"] == "1")
        check("body type matches BODY_TYPE", r["body"]["type"] == "alert")
        check("body text matches the stdin message", r["body"]["text"] == "follow up with Stephanie")
        check(
            "body carries only card + type + text (no title when TITLE unset)",
            set(r["body"]) == {"card", "type", "text"},
        )


def test_env_secrets_message_file_posts_and_consumes_file():
    """Hermes transport: env secrets + MESSAGE_FILE handoff, consumed on success."""
    server, base = _start_server()
    try:
        with tempfile.TemporaryDirectory() as d:
            use_env_secrets(Path(d), endpoint=f"{base}/api/message", card="3", body_type="weather")
            msg = Path(d) / "ld-weather-text"
            msg.write_text("<div class='weather'>72°</div>")
            post_to_kiosk.MESSAGE_FILE = str(msg)
            code, _ = run()
            file_gone = not msg.exists()
    finally:
        server.shutdown()
        reset_module()
    check("env+file POST exit zero", code == 0)
    check("MESSAGE_FILE consumed after a successful send", file_gone)
    if _CapturingHandler.received:
        r = _CapturingHandler.received[0]
        check("auth header uses the env token", r["auth"] == f"Bearer {TOKEN}")
        check("body card/type from wrapper", (r["body"]["card"], r["body"]["type"]) == ("3", "weather"))
        check("body text is the MESSAGE_FILE contents", r["body"]["text"] == "<div class='weather'>72°</div>")


def test_message_file_preserved_on_send_failure():
    """A failed send must leave MESSAGE_FILE intact so a retry can resend."""
    server, base = _start_server(_Failing500Handler)
    try:
        with tempfile.TemporaryDirectory() as d:
            use_env_secrets(Path(d), endpoint=f"{base}/api/message")
            msg = Path(d) / "ld-alert-text"
            msg.write_text("the alert")
            post_to_kiosk.MESSAGE_FILE = str(msg)
            code, _ = run()
            file_still_there = msg.exists()
    finally:
        server.shutdown()
        reset_module()
    check("failed send exits non-zero", code != 0)
    check("MESSAGE_FILE preserved on failure (retry can resend)", file_still_there)


def test_optional_title_is_posted_when_set():
    """A producer can set TITLE to control the eyebrow: '' hides it. Absent (None)
    leaves `title` off the body — the default the live-post test covers."""
    server, base = _start_server()
    try:
        with tempfile.TemporaryDirectory() as d:
            use_file_secrets(Path(d), endpoint=f"{base}/api/message", body_type="affirmation")
            post_to_kiosk.TITLE = ""
            code, _ = run(stdin_text="x")
    finally:
        server.shutdown()
        reset_module()
    check("title post exit zero", code == 0)
    if _CapturingHandler.received:
        check(
            "body carries an empty title to hide the eyebrow",
            _CapturingHandler.received[-1]["body"].get("title") == "",
        )


def test_dry_run_redacts_body_and_token():
    """--dry-run always redacts body.text and bearer from stdout, on both transports."""
    distinctive = "Stephanie asked about the proposal yesterday"
    with tempfile.TemporaryDirectory() as d:
        use_file_secrets(Path(d), body_type="alert")
        code, out = run("--dry-run", stdin_text=distinctive)
        printed = json.loads(out)
    reset_module()
    check("dry-run exit zero", code == 0)
    check("method is POST", printed["method"] == "POST")
    check("authorization is redacted", printed["authorization"] == "Bearer <redacted>")
    check("live token never appears in dry-run stdout", TOKEN not in out)
    check("body card matches CARD", printed["body"]["card"] == "1")
    check(
        "body text is redacted with length",
        printed["body"]["text"] == f"<redacted, {len(distinctive)} chars>",
    )
    check("live message text never appears in dry-run stdout", distinctive not in out)


def test_dry_run_does_not_consume_message_file():
    """--dry-run on the MESSAGE_FILE transport must (a) not delete the file the
    real run needs and (b) redact the body text + token in stdout, the same as
    the stdin transport — a MESSAGE_FILE may hold paraphrased private text."""
    distinctive = "Stephanie asked about the proposal yesterday"
    with tempfile.TemporaryDirectory() as d:
        use_env_secrets(Path(d), body_type="weather")
        msg = Path(d) / "ld-weather-text"
        msg.write_text(distinctive)
        post_to_kiosk.MESSAGE_FILE = str(msg)
        code, out = run("--dry-run")
        still_there = msg.exists()
        printed = json.loads(out)
    reset_module()
    check("dry-run exit zero", code == 0)
    check("dry-run leaves MESSAGE_FILE intact", still_there)
    check("authorization is redacted", printed["authorization"] == "Bearer <redacted>")
    check(
        "MESSAGE_FILE body text is redacted with length",
        printed["body"]["text"] == f"<redacted, {len(distinctive)} chars>",
    )
    check("MESSAGE_FILE text never appears in dry-run stdout", distinctive not in out)
    check("env token never appears in dry-run stdout", TOKEN not in out)


class _Failing500Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(500)
        self.end_headers()

    def log_message(self, *_args):
        pass


def test_non_200_exits_non_zero():
    server, base = _start_server(_Failing500Handler)
    try:
        with tempfile.TemporaryDirectory() as d:
            use_file_secrets(Path(d), endpoint=f"{base}/api/message")
            code, _ = run(stdin_text="the alert")
    finally:
        server.shutdown()
        reset_module()
    check("non-200 exits non-zero", code != 0)


def test_missing_or_empty_inputs_fail_fast():
    """Each input fails loudly when missing or empty — no defaults, no fallbacks
    beyond file→env. A secret absent from BOTH file and env exits non-zero; empty
    stdin surfaces 'no <type> text on stdin'."""
    for label, mutate in (
        ("endpoint file not readable", lambda p: p["endpoint"].unlink()),
        ("token file not readable", lambda p: p["token"].unlink()),
        ("endpoint file is empty", lambda p: p["endpoint"].write_text("")),
        ("token file is empty", lambda p: p["token"].write_text("")),
    ):
        with tempfile.TemporaryDirectory() as d:
            ep, tok = use_file_secrets(Path(d))
            mutate({"endpoint": ep, "token": tok})
            code, _ = run("--dry-run", stdin_text="the alert")
        check(f"--dry-run exits non-zero when {label}", code != 0)
    reset_module()

    # A secret missing from BOTH file and env must fail fast.
    with tempfile.TemporaryDirectory() as d:
        use_env_secrets(Path(d))
        os.environ.pop(post_to_kiosk.ENDPOINT_ENV, None)  # drop env too → neither source
        code, _ = run("--dry-run", stdin_text="the alert")
    reset_module()
    check("--dry-run exits non-zero when endpoint absent from file AND env", code != 0)

    # Empty/whitespace-only stdin (no message text) must also fail fast.
    with tempfile.TemporaryDirectory() as d:
        use_file_secrets(Path(d))
        code, _ = run("--dry-run", stdin_text="   \n")
    reset_module()
    check("--dry-run exits non-zero when stdin message text is empty", code != 0)


def test_unset_wrapper_constants_fail_fast():
    """CARD and BODY_TYPE must be set before main() — a forgetful wrapper crashes
    loudly rather than posting to the wrong slot or with an unset type."""
    for constant in ("CARD", "BODY_TYPE"):
        with tempfile.TemporaryDirectory() as d:
            use_file_secrets(Path(d))
            setattr(post_to_kiosk, constant, None)
            code, _ = run("--dry-run", stdin_text="x")
        check(f"unset {constant} exits non-zero", code != 0)
    reset_module()


def test_non_http_schemes_rejected_with_no_token_leak():
    """ftp:// and garbage schemes fail fast — only http(s):// is allowed — and
    never echo the bearer. Guards a tampered endpoint pointing to an unsupported scheme."""
    for scheme_url in ("ftp://attacker.test/api/message", "notaurl"):
        with tempfile.TemporaryDirectory() as d:
            use_file_secrets(Path(d), endpoint=scheme_url)
            code, out = run("--dry-run", stdin_text="x")
        check(f"non-http(s) endpoint {scheme_url!r} exits non-zero", code != 0)
        check(f"bearer token not echoed for {scheme_url!r}", TOKEN not in out)
    reset_module()


class _RedirectHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(302)
        self.send_header("Location", "https://attacker.test/api/message")
        self.end_headers()

    def log_message(self, *_args):
        pass


def test_redirect_not_followed():
    """A 3xx must not be followed: the no-redirect opener turns it into an
    HTTPError → non-zero exit, so urllib never re-issues the POST (with the
    Authorization header) to the redirect target."""
    server, base = _start_server(_RedirectHandler)
    try:
        with tempfile.TemporaryDirectory() as d:
            use_file_secrets(Path(d), endpoint=f"{base}/api/message")
            code, _ = run(stdin_text="x")
    finally:
        server.shutdown()
        reset_module()
    check("redirect 302 causes non-zero exit", code != 0)


def main():
    test_file_secrets_stdin_message_posts_correct_payload()
    test_env_secrets_message_file_posts_and_consumes_file()
    test_message_file_preserved_on_send_failure()
    test_optional_title_is_posted_when_set()
    test_dry_run_redacts_body_and_token()
    test_dry_run_does_not_consume_message_file()
    test_non_200_exits_non_zero()
    test_missing_or_empty_inputs_fail_fast()
    test_unset_wrapper_constants_fail_fast()
    test_non_http_schemes_rejected_with_no_token_leak()
    test_redirect_not_followed()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
