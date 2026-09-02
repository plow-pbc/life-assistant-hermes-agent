#!/usr/bin/env python3
"""Tests for post_to_kiosk.py — the shared POST helper every ld- producer uses.

The helper reads the message text from one of two fixed sources (stdin, or a
caller-set MESSAGE_FILE) and the endpoint URL + bearer token from the first
populated of three: the secret file, the process env, then the shared dotenv.
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
    # Not the real /opt/data/.env: a machine that happens to have one would
    # otherwise satisfy the third source and silence the both-absent refusals.
    post_to_kiosk.DOTENV = "/nonexistent/dotenv-for-tests/.env"
    post_to_kiosk.OUTBOX_DIR = "/opt/data/ld/outbox"
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


def use_dotenv_secrets(tmp: Path, endpoint="https://x.test/api/message", card="1", body_type="alert"):
    """Dotenv transport: no secret file, no env var — both keys in DOTENV alone.

    The live case ld-setup creates: mint_wall_token.py appends its lines after
    the gateway loaded /opt/data/.env, so a cron-spawned producer sees them
    only by reading the file itself.
    """
    reset_module()
    post_to_kiosk.CARD = card
    post_to_kiosk.BODY_TYPE = body_type
    post_to_kiosk.ENDPOINT_FILE = str(tmp / "nonexistent-endpoint")
    post_to_kiosk.TOKEN_FILE = str(tmp / "nonexistent-token")
    dotenv = tmp / ".env"
    dotenv.write_text(
        f"PLOW_AGENT_TOKEN={TOKEN}\n{post_to_kiosk.ENDPOINT_ENV}={endpoint}\n"
        f"{post_to_kiosk.TOKEN_ENV}={TOKEN}\n"
    )
    post_to_kiosk.DOTENV = str(dotenv)


def use_env_secrets(tmp: Path, endpoint="https://x.test/api/message", card="1", body_type="alert"):
    """Env transport: point the file paths at nonexistent files and set env vars."""
    reset_module()
    post_to_kiosk.CARD = card
    post_to_kiosk.BODY_TYPE = body_type
    post_to_kiosk.ENDPOINT_FILE = str(tmp / "nonexistent-endpoint")
    post_to_kiosk.TOKEN_FILE = str(tmp / "nonexistent-token")
    os.environ[post_to_kiosk.ENDPOINT_ENV] = endpoint
    os.environ[post_to_kiosk.TOKEN_ENV] = TOKEN


def use_latch_delivery(tmp: Path, endpoint, card="3", body_type="weather"):
    """Latch transport: endpoint from the env (so a loopback server can stand
    where the Pi would and prove nothing reached it), DASHBOARD_DELIVERY=latch
    from the dotenv, the outbox rebound under tmp. No token anywhere: latch
    mode must not need one -- the Mac holds it."""
    reset_module()
    post_to_kiosk.CARD = card
    post_to_kiosk.BODY_TYPE = body_type
    post_to_kiosk.ENDPOINT_FILE = str(tmp / "nonexistent-endpoint")
    post_to_kiosk.TOKEN_FILE = str(tmp / "nonexistent-token")
    os.environ[post_to_kiosk.ENDPOINT_ENV] = endpoint
    dotenv = tmp / ".env"
    dotenv.write_text("DASHBOARD_DELIVERY=latch\n")
    post_to_kiosk.DOTENV = str(dotenv)
    post_to_kiosk.OUTBOX_DIR = str(tmp / "outbox")


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


def test_dotenv_secrets_are_the_third_source():
    """No secret file and no env var: both values come from the dotenv, and a
    Pi-shaped endpoint there is accepted. --dry-run, so no server is needed
    and the URL can carry the real port 5174 rather than a loopback's.

    Without this source, every line mint_wall_token.py appends after `up`
    stays invisible to the producers until the gateway restarts."""
    with tempfile.TemporaryDirectory() as d:
        use_dotenv_secrets(Path(d), endpoint="http://raspberrypi.local:5174/api/message",
                           card="3", body_type="weather")
        code, out = run("--dry-run", stdin_text="72 and clear")
    reset_module()
    check("dotenv-only dry-run exit zero", code == 0)
    check(
        "the dotenv's endpoint is the one that would be posted to",
        code == 0 and json.loads(out)["url"] == "http://raspberrypi.local:5174/api/message",
    )
    check("dotenv token never appears on stdout", TOKEN not in out)


def test_dotenv_endpoint_that_is_not_the_pi_is_refused():
    """/opt/data/.env is agent-writable at runtime; an injected endpoint line
    there must not steer the bearer anywhere but http://<host>:5174/api/message.
    A live loopback server on another port proves nothing was sent."""
    server, base = _start_server()
    try:
        with tempfile.TemporaryDirectory() as d:
            use_dotenv_secrets(Path(d), endpoint=f"{base}/api/message", card="3", body_type="weather")
            code, out = run(stdin_text="x")
    finally:
        server.shutdown()
        reset_module()
    check("dotenv endpoint off the Pi shape exits non-zero", code != 0)
    check("no request reached the server (refused before any send)", len(_CapturingHandler.received) == 0)
    check("bearer token not echoed on refusal", TOKEN not in out)
    # Right shape, wrong reach: a public hostname passes the regex but must
    # refuse before the bearer can walk off the household network.
    with tempfile.TemporaryDirectory() as d:
        try:
            use_dotenv_secrets(Path(d), endpoint="http://collector.example:5174/api/message",
                               card="3", body_type="weather")
            code, out = run(stdin_text="x")
        finally:
            reset_module()
    check("dotenv endpoint on a public host is refused (right shape, wrong reach)", code != 0)


def test_latch_delivery_writes_the_outbox_and_prints_the_hand_off_without_a_request():
    """DASHBOARD_DELIVERY=latch: the exact wire body lands in the outbox (mode
    600), the fixed NOT DELIVERED block names both Latch calls, no request is
    made, no token is read or printed, and the handoff is consumed because the
    outbox now holds the body."""
    server, base = _start_server()
    try:
        with tempfile.TemporaryDirectory() as d:
            use_latch_delivery(Path(d), endpoint=f"{base}/api/message")
            msg = Path(d) / "ld-weather-text"
            msg.write_text("<div class='weather'>72°</div>")
            post_to_kiosk.MESSAGE_FILE = str(msg)
            post_to_kiosk.TITLE = ""
            code, out = run()
            outbox = Path(d) / "outbox" / "card-3.json"
            written = outbox.read_text() if outbox.exists() else None
            mode = oct(outbox.stat().st_mode & 0o777) if outbox.exists() else None
            file_gone = not msg.exists()
    finally:
        server.shutdown()
        reset_module()
    wire = json.dumps({"card": "3", "type": "weather", "text": "<div class='weather'>72°</div>", "title": ""})
    check("latch exit zero", code == 0)
    check("no request reached the server", len(_CapturingHandler.received) == 0)
    check("outbox holds the exact wire body", written == wire)
    check("outbox file is mode 600", mode == "0o600")
    check("MESSAGE_FILE consumed once the outbox holds the body", file_gone)
    check(
        "stdout is the fixed NOT DELIVERED block with both Latch calls and the JSON",
        out == (
            "NOT DELIVERED — ship it through Latch, then paste both outputs:\n"
            "1. mcp__latch__plow_write_file  path=~/Plow/ld/card-3.json  content=<the JSON below>\n"
            "2. mcp__latch__plow_run_command argv=[\"sh\",\"-c\",\"curl -fsS -H @$HOME/Plow/ld/dashboard.hdr "
            "-H 'Content-Type: application/json' --data-binary @$HOME/Plow/ld/card-3.json "
            f"{base}/api/message\"] network=true\n"
            f"{wire}\n"
        ),
    )
    check("no token on stdout (latch mode never reads one)", TOKEN not in out)


def test_latch_endpoint_precedence_env_yields_to_dotenv_but_a_file_does_not():
    """ld-setup re-points the endpoint in the dotenv; the startup env is that
    file's stale boot-time copy, so in latch mode the dotenv line wins over
    env -- and ONLY over env: a secrets-mount file keeps its documented
    precedence even when the dotenv also names the key."""
    cases = [  # label, secrets-file host (None = absent), env host, dotenv host, winner, loser
        # Hosts are private IPs: a dotenv-sourced endpoint is household-gated.
        ("latch env endpoint yields to the re-pointed dotenv line",
         None, "192.168.1.9", "192.168.1.50", "192.168.1.50", "192.168.1.9"),
        ("latch secrets-file endpoint keeps precedence over the dotenv",
         "10.0.0.7", "192.168.1.9", "192.168.1.50", "10.0.0.7", "192.168.1.50"),
    ]
    for label, file_host, env_host, dotenv_host, winner, loser in cases:
        with tempfile.TemporaryDirectory() as d:
            try:
                use_latch_delivery(Path(d), endpoint=f"http://{env_host}:5174/api/message")
                if file_host:
                    endpoint_file = Path(d) / "endpoint-file"
                    endpoint_file.write_text(f"http://{file_host}:5174/api/message")
                    post_to_kiosk.ENDPOINT_FILE = str(endpoint_file)
                (Path(d) / ".env").write_text(
                    "DASHBOARD_DELIVERY=latch\n"
                    f"DASHBOARD_ENDPOINT_URL=http://{dotenv_host}:5174/api/message\n")
                code, out = run(stdin_text="hi")
            finally:
                reset_module()
        check(label, code == 0 and f"http://{winner}:5174/api/message" in out and loser not in out)


def test_a_delivery_that_is_not_latch_still_posts():
    """DASHBOARD_DELIVERY unset, blank, or anything but `latch` is today's
    direct POST -- the dotenv's presence alone changes nothing."""
    for delivery in ("", "direct"):
        server, base = _start_server()
        try:
            with tempfile.TemporaryDirectory() as d:
                use_env_secrets(Path(d), endpoint=f"{base}/api/message")
                dotenv = Path(d) / ".env"
                dotenv.write_text(f"DASHBOARD_DELIVERY={delivery}\n")
                post_to_kiosk.DOTENV = str(dotenv)
                code, _ = run(stdin_text="x")
        finally:
            server.shutdown()
            reset_module()
        check(f"DASHBOARD_DELIVERY={delivery!r} exits zero", code == 0)
        check(f"DASHBOARD_DELIVERY={delivery!r} still POSTs to the endpoint", len(_CapturingHandler.received) == 1)


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
    test_dotenv_secrets_are_the_third_source()
    test_dotenv_endpoint_that_is_not_the_pi_is_refused()
    test_latch_delivery_writes_the_outbox_and_prints_the_hand_off_without_a_request()
    test_latch_endpoint_precedence_env_yields_to_dotenv_but_a_file_does_not()
    test_a_delivery_that_is_not_latch_still_posts()
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
