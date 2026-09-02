"""The agent-index reporter's s6 slot.

These assert the two things that were actually wrong when this was built, not
the shape of the files: a reporter that is never registered never runs, and a
reporter that inherits HOME writes its token somewhere it cannot.
"""
import pathlib
import re

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


def test_the_switch_is_read_from_the_agents_own_dotenv():
    """Not from the environment, and that is the whole point.

    compose.override.yml merges after agent-mgr's template and wins -- measured
    -- and that file is shared by every instance registered against the
    checkout, so an AGENT_INDEX there opts in siblings who never asked. The
    agent's home is already mounted at /opt/data, so the switch is read from
    the one file that is per-person. Nothing in Compose can forge a value
    Compose never carries.

    grep, never `.`: that file holds the agent's credentials, and sourcing it
    would put every one of them into the reporter's environment.
    """
    run = SVC.joinpath("run").read_text()
    # The assignment itself, not the file's prose -- the comment above it
    # names the grep pipeline this replaced, so a blanket search for that word
    # matches the explanation and passes whatever the code does.
    #
    # Continuations joined first: the command wraps, so reading one line finds
    # the assignment without the command it runs.
    joined = run.replace("\\\n", " ")
    assignment = [l for l in joined.splitlines() if l.startswith("AGENT_INDEX=")][0]
    assert "runtime_env.py" in assignment, \
        "read it through the repo's one dotenv parser, not a second spelling"
    assert "|" not in assignment, \
        "a private pipeline disagrees with the shared parser about duplicates and quotes"
    assert re.search(r"^\s*\.\s+/opt/data/\.env", run, re.M) is None, "must not source the dotenv"


def test_a_disabled_slot_stays_down_and_a_died_one_comes_back():
    """finish reads run's exit status rather than re-deriving the switch.

    run exits 0 without exec'ing when reporting is off, so 0 means deliberate
    and earns 125 (s6's do-not-restart marker). Anything else means the loop
    died, which should come back -- the dashboard returns 125 unconditionally,
    right for a UI you can see is missing and wrong for a silent background job.
    """
    finish = SVC.joinpath("finish").read_text()
    assert "125" in finish and "$1" in finish
    assert "AGENT_INDEX" not in finish, "the switch is not in this script's environment to read"


def test_the_client_is_pinned_to_a_revision_and_a_checksum():
    """Fetched, not carried -- and not from a moving reference.

    plow-pbc/agent-index-client owns that file and is public, so this repo
    names a revision instead of holding a copy that drifts. A sha rather than
    `main` for the reason the Dockerfile pins its base by digest: a moving
    reference substitutes unreviewed code under an agent holding a live
    credential.

    The checksum is the second half. A sha in a URL is only as good as the host
    serving it, and this file is executed inside the agent.
    """
    pin = dict(
        line.split("=", 1)
        for line in (ROOT / "vendor" / "client.pin").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    assert re.fullmatch(r"[0-9a-f]{40}", pin["sha"]), "pin a full commit sha, never a branch"
    assert re.fullmatch(r"[0-9a-f]{64}", pin["sha256"])
    assert pin["path"] == "standalone/agent_index_client.py", \
        "the repo root is a different reporter; the stdlib client lives under standalone/"


def test_the_fetched_client_is_not_committed():
    """A committed copy is the drift this replaced; keeping both brings it back."""
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "vendor/agent_index_client.py"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.strip()
    assert tracked == "", "the client is fetched by `just client`, not tracked"


def test_the_service_refuses_a_client_that_is_not_a_file():
    """The bind-mount hazard, which fails in the least visible way available.

    A bind whose SOURCE is missing does not error: the runtime creates a
    DIRECTORY at the target. Measured -- a container started without the fetch
    has a directory at /usr/local/bin/agent-index-client. Without this check
    python raises IsADirectoryError once an hour into a log nobody reads, and
    the agent reports nothing while every slot looks healthy.
    """
    run = SVC.joinpath("run").read_text()
    assert "-f /usr/local/bin/agent-index-client" in run
    assert "just client" in run, "the message has to name the fix, not just the symptom"
