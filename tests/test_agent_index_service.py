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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "image/s6-overlay/s6-rc.d/agent-index"


def run_service(tmp_path, environment: dict[str, str], seconds: float = 2.0) -> str:
    """Start the real run script against a fake container environment.

    It is a supervised loop, so it never exits on its own: it is killed after a
    moment and judged on what it did. /command and /opt are not there, so the
    client invocation fails -- which is the point, it proves the script reached
    the invocation with the values it was given.
    """
    env_dir = tmp_path / "run/s6/container_environment"
    env_dir.mkdir(parents=True)
    for name, value in environment.items():
        (env_dir / name).write_text(value)

    script = (SERVICE / "run").read_text().replace(
        "/run/s6/container_environment", str(env_dir))
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
