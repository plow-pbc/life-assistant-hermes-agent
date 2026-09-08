"""The usage reporter, as the image ships it.

There is no switch: the service is in the image or it is not, and that IS the
decision -- an owner who does not want their usage on the index builds without
it.

These RUN the service script in a sandbox rather than reading it. A test that
greps for a string passes on a script that would not start, which is the one
thing worth knowing about a boot service.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "image/s6-overlay/s6-rc.d/agent-index"


# A stand-in for the pinned client, in the two halves the run script uses it
# in: `load_state`, which answers whether this install is registered, and a
# __main__ that records the argv it was invoked with. What the script DID is
# then a file, not a guess from the output of a program that is not there.
STUB_CLIENT = """
def load_state():
    return {state}


if __name__ == "__main__":
    import sys
    with open({record!r}, "a") as record:
        record.write(" ".join(sys.argv[1:]) + "\\n")
"""


def run_service(tmp_path, environment: dict[str, str], seconds: float = 2.0,
                state: str | None = None) -> str:
    """Start the real run script against a fake container environment.

    It is a supervised loop, so it never exits on its own: it is killed after a
    moment and judged on what it did. /command and /opt are not there, so the
    client invocation fails -- which is the point, it proves the script reached
    the invocation with the values it was given.

    Given `state` -- the expression the stub client's load_state returns -- the
    three absolute paths this image guarantees are pointed at the sandbox
    instead, so the script's OWN branching runs against a client that answers.
    That is the only way to see what it does on the second hour, which is where
    the registration gate is either right or minting a key an hour forever.
    """
    env_dir = tmp_path / "run/s6/container_environment"
    env_dir.mkdir(parents=True)
    for name, value in environment.items():
        (env_dir / name).write_text(value)

    script = (SERVICE / "run").read_text().replace(
        "/run/s6/container_environment", str(env_dir))
    if state is not None:
        home = tmp_path / "hermes"
        home.mkdir()
        client = tmp_path / "client.py"
        client.write_text(STUB_CLIENT.format(state=state, record=str(tmp_path / "invoked")))
        script = (script
                  .replace("/var/lib/hermes", str(home))
                  .replace("/command/s6-setuidgid hermes", "")
                  .replace("/opt/hermes/.venv/bin/python3", sys.executable)
                  .replace("/opt/plow/agent-index-client.py", str(client)))
    sandbox = tmp_path / "run.sh"
    sandbox.write_text(script)
    sandbox.chmod(0o755)

    try:
        done = subprocess.run(["sh", str(sandbox)], capture_output=True, text=True,
                              timeout=seconds, env={"PATH": os.environ["PATH"]})
        return done.stdout + done.stderr
    except subprocess.TimeoutExpired as expired:
        out = (expired.stdout or b"") + (expired.stderr or b"")
        return out.decode() if isinstance(out, bytes) else str(out)


def test_it_stands_down_when_nothing_says_which_agent_this_is(tmp_path):
    """There is nothing to report FOR without an AGENT_ID, and guessing files
    this container's usage under somebody else's agent."""
    said = run_service(tmp_path, {"PLOW_AGENT_TOKEN": "plow_atokenshapedthing"})
    assert "standing down" in said
    assert "AGENT_ID" in said, "and it says which value is missing"


def test_given_an_id_and_a_credential_it_proceeds(tmp_path):
    """One run, two things worth knowing about it.

    It reaches the work -- as the agent, with the home this image uses -- and it
    does NOT stand down, which is the no-switch rule stated as behaviour: with a
    credential and an id and nothing else set, a switch would have stopped it
    here for want of a flag."""
    said = run_service(tmp_path, {"PLOW_AGENT_TOKEN": "plow_atokenshapedthing",
                                  "AGENT_ID": "life"})
    assert "standing down" not in said, "it had everything it needed"
    # /command/s6-setuidgid does not exist out here, and that failure is the
    # evidence: it got as far as dropping privilege to do the work.
    assert "s6-setuidgid" in said or "not found" in said


def test_the_service_is_wired_the_way_s6_starts_one():
    """A service s6 does not know about is a file nobody runs -- and this one
    cannot start before the boot that exports its credential."""
    assert (SERVICE / "type").read_text().strip() == "longrun"
    assert (SERVICE / "dependencies.d/plow-init").exists()
    assert (ROOT / "image/s6-overlay/s6-rc.d/user/contents.d/agent-index").exists()


def test_the_client_is_pinned_and_the_build_verifies_it():
    """A moving reference would substitute unreviewed code inside an agent that
    holds a live credential; a sha alone trusts whoever serves it. The build
    does the checking -- this asserts the build was told to."""
    import re
    pin = (ROOT / "vendor/client.pin").read_text()
    assert re.search(r"^sha=[0-9a-f]{40}$", pin, re.M), "a full commit sha, never a branch"
    assert re.search(r"^sha256=[0-9a-f]{64}$", pin, re.M)
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "vendor/client.pin" in dockerfile and "sha256sum" in dockerfile


def test_neither_deleted_layout_comes_back():
    """A bind mount whose source is missing does not fail: the runtime creates a
    DIRECTORY at the target and the reporter starts against it, reporting
    nothing. The image carries the client instead."""
    assert not (ROOT / "compose.override.yml").exists()
    assert not (ROOT / "docker/s6-rc.d").exists()


def invocations(tmp_path) -> list[str]:
    """Every way the client was invoked in that run, in order."""
    record = tmp_path / "invoked"
    return record.read_text().splitlines() if record.exists() else []


def test_a_registered_install_reports_without_registering_again(tmp_path):
    """The bug this gate had: it tested a path the client only ever DELETES, so
    it was true on every pass and every tenant minted a fresh key on the hour.

    An install whose state holds a key registers ZERO times and reports once."""
    said = run_service(tmp_path, {"PLOW_AGENT_TOKEN": "plow_atokenshapedthing",
                                  "AGENT_ID": "life"},
                       state='{"install_id": "i" * 32, "key": "aik_" + "k" * 43}')
    assert invocations(tmp_path) == [""], f"one bare report, nothing else: {said}"


def test_an_unregistered_install_registers_once_then_reports(tmp_path):
    """The other half: a gate that never registers is a tenant with no page."""
    run_service(tmp_path, {"PLOW_AGENT_TOKEN": "plow_atokenshapedthing",
                           "AGENT_ID": "life"},
                state="{}")
    assert invocations(tmp_path) == ["--register --agent life", ""]


def test_a_client_that_cannot_classify_the_state_registers_nothing(tmp_path):
    """State the client could not READ is not state to register over: minting
    against a new install strands every row the old one published. So the hour
    is skipped entirely -- no registration, no report -- and it says so."""
    said = run_service(tmp_path, {"PLOW_AGENT_TOKEN": "plow_atokenshapedthing",
                                  "AGENT_ID": "life"},
                       state='__import__("sys").exit("  state could not be READ")')
    assert invocations(tmp_path) == [], "it touched the Index on a state it cannot read"
    assert "could not say whether this install is registered" in said
