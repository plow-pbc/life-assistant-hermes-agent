"""The agent-index reporter's s6 slot.

These assert the two things that were actually wrong when this was built, not
the shape of the files: a reporter that is never registered never runs, and a
reporter that inherits HOME writes its token somewhere it cannot.
"""
import pathlib
import re
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SVC = ROOT / "docker" / "s6-rc.d" / "agent-index"


def test_registered_in_the_user_bundle():
    # s6 starts the services named in the bundle, not every directory present.
    # An unregistered slot is silently inert -- the container comes up healthy
    # and simply never reports.
    assert (ROOT / "docker" / "s6-rc.d" / "user" / "contents.d" / "agent-index").exists()


def test_is_a_longrun():
    assert SVC.joinpath("type").read_text().strip() == "longrun"


def test_run_is_executable():
    assert SVC.joinpath("run").stat().st_mode & 0o111, "s6 skips a non-executable run"


@pytest.mark.parametrize("enabled", ["1", "true", "yes"])
def test_enabled_values_are_accepted(enabled):
    assert enabled in SVC.joinpath("run").read_text()


def test_finish_does_not_permanently_stop_an_enabled_reporter():
    # 125 is s6's "do not restart" marker. The dashboard returns it
    # unconditionally, which is right for a UI whose absence you can see and
    # wrong for a background job whose absence is silent.
    finish = SVC.joinpath("finish").read_text()
    assert "exit 0" in finish and "exit 125" in finish
    assert finish.index("exit 0") < finish.index("exit 125"), \
        "the enabled branch must come first and must not return 125"


def test_home_and_hermes_home_are_both_on_the_invocation():
    """Two paths, named on the invocation, for two failures that are both silent.

    HOME: s6-setuidgid changes uid/gid but leaves HOME as root's, and the client
    resolves its token path with expanduser(), which reads $HOME before
    /etc/passwd. Without it the login dies writing /root/.agent-index -- and
    nothing notices, because an agent with no usage exits 0 long before it
    needs a token.

    HERMES_HOME: the store is at /opt/data/state.db, not the client's own
    $HOME/.hermes/state.db fallback. A store that is not found is not an error;
    the reporter posts a zero and the agent reads as idle rather than
    misconfigured.

    Both currently match what the base image exports, so neither assertion
    changes behaviour today. That is the point: without them, correctness rests
    on env vars owned by an image we pin by digest and bump, and nobody bumping
    it would know to check either.
    """
    run = SVC.joinpath("run").read_text()
    exec_line = [l for l in run.splitlines() if l.startswith("exec ")][0]
    # Matched with a boundary, not `in`: "HERMES_HOME=/opt/data" CONTAINS
    # "HOME=/opt/data", so a substring check passes with HOME deleted. Caught
    # by reverting each one separately -- reverting only HERMES_HOME reddened
    # and reverting HOME did not, which is the test lying about the half it
    # was written to protect.
    assert re.search(r"(?<![A-Z_])HOME=/opt/data", exec_line), \
        "HOME must be named on the exec line, not merely mentioned in a comment"
    assert "HERMES_HOME=/opt/data" in exec_line, \
        "HERMES_HOME must be named on the exec line"
    assert "HERMES_HOME=/opt/data/.hermes" not in run, \
        "the store is at /opt/data/state.db, not under a .hermes subdirectory"


def test_the_reporter_is_not_baked_into_the_dockerfile():
    """Delivered one way, not two.

    A COPY here would put a second, staler copy in the standalone image while
    the mount supplies the fleet -- two sources for one file, drifting apart
    silently because only one of them is ever exercised.
    """
    assert "agent-index-client" not in (ROOT / "Dockerfile").read_text()


def test_identity_is_not_validated_twice():
    """The client reads AGENT_ID itself; the shell neither rechecks nor forwards it."""
    run = SVC.joinpath("run").read_text()
    assert "--agent" not in run
    assert "AGENT_ID unset" not in run
