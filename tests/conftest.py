"""Fixtures shared across this agent's contract tests."""
import threading

import pytest


@pytest.fixture
def run_concurrently():
    """Run callables from a common barrier; return whatever they raised.

    The barrier is the test, not decoration. Threads started one after another
    usually finish one after another, so a race only shows under real overlap --
    and a race that does not show is one a suite passes every time while the bug
    ships. Both writers here are read-modify-write, and the losses they produce
    are clean files that are quietly wrong: a config missing an answer the owner
    gave, or a Pi and a Mac holding different bearers.

    Failures are collected rather than raised in a worker thread, where pytest
    would never see them; the caller asserts on the returned list.
    """
    def run(*calls):
        start = threading.Barrier(len(calls))
        errors = []

        def wrap(call):
            def body():
                try:
                    start.wait(timeout=5)
                    call()
                except BaseException as exc:    # noqa: BLE001 - returned to the caller
                    errors.append(exc)
            return threading.Thread(target=body)

        threads = [wrap(call) for call in calls]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
            assert not thread.is_alive(), "a writer never finished -- the lock deadlocked"
        return errors

    return run


FAKE_LATCH = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
state = Path(os.environ["FAKE_LATCH_DIR"])
argv = sys.argv[1:]
call = {"argv": argv}
if argv[:2] == ["file", "put"]:
    call["content"] = Path(argv[-2]).read_text()
with open(state / "calls.jsonl", "a") as log:
    log.write(json.dumps(call) + "\\n")
answers = json.loads((state / "answers.json").read_text())
queue = answers.get(argv[0], [])
answer = queue.pop(0) if len(queue) > 1 else (queue[0] if queue else {"stdout": {}})
(state / "answers.json").write_text(json.dumps(answers))
if "error" in answer:
    print(json.dumps({"error": answer["error"]}), file=sys.stderr)
    sys.exit(answer.get("exit", 1))
print(json.dumps(answer["stdout"]))
'''


class FakeLatch:
    """A `latch` on PATH that answers by subcommand from a queue and logs every
    call, so a script's real latch calls -- argv, flags, the file it put -- are
    what a test asserts on."""

    def __init__(self, root):
        self.root = root
        self.answers = {}
        self._write()

    def _write(self):
        import json
        (self.root / "answers.json").write_text(json.dumps(self.answers))

    def answer(self, command, *answers):
        """Queue answers for `latch COMMAND`: a dict is stdout, an
        ("error", code, exit) tuple is latch's error line."""
        self.answers[command] = [
            {"error": {"code": a[1], "message": a[1].replace("_", " ")}, "exit": a[2]}
            if isinstance(a, tuple) else {"stdout": a} for a in answers]
        self._write()

    def hosts(self, *hosts):
        """`latch status` with these (name, online, plugins...) hosts."""
        self.answer("status", {"hosts": [
            {"name": name, "online": online,
             "plugins": [{"name": p, "state": "enabled"} for p in plugins]}
            for name, online, *plugins in hosts]})

    def calls(self, command=None):
        import json
        log = self.root / "calls.jsonl"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return [c for c in calls if command is None or c["argv"][0] == command]


@pytest.fixture
def fake_latch(tmp_path, monkeypatch):
    import os
    root = tmp_path / "fake-latch"
    (root / "bin").mkdir(parents=True)
    script = root / "bin" / "latch"
    script.write_text(FAKE_LATCH)
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_LATCH_DIR", str(root))
    monkeypatch.setenv("PATH", f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PLOW_HOME_CHANNEL", "cht_home")
    return FakeLatch(root)
