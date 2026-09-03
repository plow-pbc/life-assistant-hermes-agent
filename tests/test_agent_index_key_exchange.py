"""The Plow token reaches the index once, and never again.

That token authorises chats, the Latch relay and inference as well as usage, so
an index that holds it holds the agent. It is traded once for a key the index
scopes to reporting; these run the real exchange against a fake index that
records what it was shown.
"""
import http.server
import json
import os
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCHANGE = ROOT / "image/s6-overlay/scripts/agent-index-key.py"
PLOW_TOKEN = "plow_HxK3nQ7pR2vT8wYz-A4bC6dE9fG1hJ0k"   # pragma: allowlist secret
MINTED = "aik_" + "k" * 43                              # pragma: allowlist secret


class FakeIndex(http.server.BaseHTTPRequestHandler):
    seen: list[str] = []

    def do_POST(self):
        FakeIndex.seen.append(self.headers.get("authorization", ""))
        body = json.dumps({"ok": True, "key": MINTED, "scope": "usage,stories"}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def index_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), FakeIndex)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def exchange(tmp_path, server, token=PLOW_TOKEN):
    return subprocess.run(
        ["python3", str(EXCHANGE)], capture_output=True, text=True,
        env={**os.environ,
             "AGENT_INDEX_API": f"http://127.0.0.1:{server.server_port}",
             "AGENT_INDEX_KEY_PATH": str(tmp_path / ".agent-index/token"),
             "AGENT_ID": "life", "PLOW_AGENT_TOKEN": token})


def test_the_plow_token_is_spent_once_and_the_key_is_used_after(tmp_path):
    FakeIndex.seen = []
    server = index_server()
    try:
        first = exchange(tmp_path, server)
        assert first.returncode == 0, first.stderr
        assert first.stdout.strip() == MINTED, "it hands the reporter the index key"
        assert FakeIndex.seen == [f"Bearer {PLOW_TOKEN}"], "the exchange itself uses the Plow token"

        # Every later hour: no call at all, because the key is already held.
        for _ in range(3):
            again = exchange(tmp_path, server)
            assert again.returncode == 0
            assert again.stdout.strip() == MINTED
        assert len(FakeIndex.seen) == 1, "the Plow token must cross to the index exactly once"
    finally:
        server.shutdown()


def test_the_stored_key_is_the_agents_to_read(tmp_path):
    """Written as whoever runs the exchange -- the agent -- and 0600: the
    reporter has to be able to read it, and nobody else should."""
    server = index_server()
    try:
        exchange(tmp_path, server)
    finally:
        server.shutdown()
    stored = tmp_path / ".agent-index/token"
    assert stored.read_text() == MINTED
    assert oct(stored.stat().st_mode & 0o777) == "0o600"


def test_it_refuses_to_fall_back_to_the_plow_token(tmp_path):
    """An unreachable index is not a reason to report with the bearer this
    exists to withhold: the next hour tries again."""
    result = subprocess.run(
        ["python3", str(EXCHANGE)], capture_output=True, text=True,
        env={**os.environ, "AGENT_INDEX_API": "http://127.0.0.1:1",
             "AGENT_INDEX_KEY_PATH": str(tmp_path / ".agent-index/token"),
             "PLOW_AGENT_TOKEN": PLOW_TOKEN})
    assert result.returncode != 0
    assert PLOW_TOKEN not in result.stdout, "and it never prints the token onward"
    assert not (tmp_path / ".agent-index/token").exists()


def test_it_will_not_hand_on_something_that_is_not_our_key(tmp_path):
    """A file holding anything else is not a credential this reporter can use,
    and passing it on would send a stranger's value to the index as auth."""
    stored = tmp_path / ".agent-index/token"
    stored.parent.mkdir(parents=True)
    stored.write_text("gho_aleftoverfromtheoldsignin")     # pragma: allowlist secret
    FakeIndex.seen = []
    server = index_server()
    try:
        result = exchange(tmp_path, server)
    finally:
        server.shutdown()
    assert result.stdout.strip() == MINTED, "it mints a real one instead"
    assert "gho_" not in result.stdout


def test_the_reporter_is_started_without_the_plow_token():
    """The service hands PLOW_AGENT_TOKEN to the exchange and to nothing else."""
    run = (ROOT / "image/s6-overlay/s6-rc.d/agent-index/run").read_text()
    loop = run[run.index("while :; do"):]
    reporter = loop.split("agent-index-key.py")[1].split("agent-index-client.py")[0]
    # Comments in that stretch SAY "PLOW_AGENT_TOKEN" to explain its absence,
    # so judge the commands, not the prose around them.
    commands = "\n".join(line for line in reporter.splitlines()
                         if not line.strip().startswith("#"))
    assert "PLOW_AGENT_TOKEN" not in commands, \
        "the reporter's environment must not carry the Plow bearer"
