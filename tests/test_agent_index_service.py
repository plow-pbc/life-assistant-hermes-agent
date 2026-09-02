"""The agent-index reporter's s6 slot.

These assert the two things that were actually wrong when this was built, not
the shape of the files: a reporter that is never registered never runs, and a
reporter that inherits HOME writes its token somewhere it cannot.
"""
import pathlib
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


def test_client_invocation_sets_home_explicitly():
    """The bug this was born with.

    s6-setuidgid changes uid/gid but leaves HOME as root's, and the client
    resolves its token path with expanduser(), which reads $HOME before
    /etc/passwd. Without an explicit HOME the login dies writing
    /root/.agent-index and the token never reaches the bind-mounted home.

    It fails silently in the reporting path: an agent with no usage prints
    "nothing to report" and exits 0 long before it ever needs a token, so the
    slot looks healthy right up until the day it has something to send.
    """
    run = SVC.joinpath("run").read_text()
    assert "HOME=/opt/data" in run
    home = run.index("HOME=/opt/data")
    client = run.index("agent-index-client")
    assert home < client, "HOME must be set on the invocation, not after it"


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
