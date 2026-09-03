"""The usage reporter, as the image ships it.

There is no switch: the service is in the image or it is not, and that IS the
decision -- an owner who does not want their usage on the index builds without
it. These assert the shape that makes "in the image" true and safe.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "image/s6-overlay/s6-rc.d/agent-index"


def test_the_service_is_wired_the_way_s6_starts_one():
    """A service s6 does not know about is a file nobody runs."""
    assert (SERVICE / "type").read_text().strip() == "longrun"
    assert (SERVICE / "dependencies.d/plow-init").exists(), \
        "it needs the boot that exports PLOW_AGENT_TOKEN, or it starts without a credential"
    assert (ROOT / "image/s6-overlay/s6-rc.d/user/contents.d/agent-index").exists(), \
        "without the bundle marker the service exists and never runs"


def test_there_is_no_opt_in_switch():
    """Installing the reporter is the decision; a flag would re-ask it.

    Asserted because the switch existed and was removed: a second place to say
    yes is a second place to disagree with the image."""
    run = (SERVICE / "run").read_text()
    assert "AGENT_INDEX" not in run, "no opt-in switch: presence in the image is the consent"
    assert "runtime_env" not in run, "and nothing reads a per-person file to decide"


def test_it_stands_down_without_an_agent_id():
    """There is nothing to report FOR without one, and guessing a name would
    file this container's usage under somebody else's agent."""
    run = (SERVICE / "run").read_text()
    assert "AGENT_ID" in run
    assert "standing down" in run


def test_it_reads_the_credential_the_boot_exported():
    """0600 root, so it is read before the privilege drop, not after."""
    run = (SERVICE / "run").read_text()
    assert "/run/s6/container_environment/PLOW_AGENT_TOKEN" in run
    assert "s6-setuidgid hermes" in run, "and the client itself runs as the agent, never as root"


def test_the_client_is_pinned_and_verified_at_build():
    """A moving reference would substitute unreviewed code inside an agent that
    holds a live credential; a sha alone trusts whoever serves it."""
    pin = (ROOT / "vendor/client.pin").read_text()
    assert re.search(r"^sha=[0-9a-f]{40}$", pin, re.M), "a full commit sha, never a branch"
    assert re.search(r"^sha256=[0-9a-f]{64}$", pin, re.M)
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "vendor/client.pin" in dockerfile
    assert "sha256sum" in dockerfile, "fetched AND checked, or the pin is decoration"


def test_the_reporter_is_not_bind_mounted():
    """A mount whose source is missing does not fail -- the runtime creates a
    DIRECTORY at the target, and the reporter starts against a directory and
    reports nothing. Built into the image, it is there or the build failed."""
    assert not (ROOT / "compose.override.yml").exists(), \
        "this repo no longer owns a compose file; the image carries the service"
    assert not (ROOT / "docker/s6-rc.d").exists(), \
        "services live under image/s6-overlay, where the base expects them"


def test_the_pinned_client_matches_its_checksum():
    """The pin is only as good as the file it names."""
    pin = dict(
        line.split("=", 1) for line in (ROOT / "vendor/client.pin").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    url = f"https://raw.githubusercontent.com/plow-pbc/agent-index-client/{pin['sha']}/{pin['path']}"
    fetched = subprocess.run(["curl", "-fsS", "--max-time", "60", url],
                             capture_output=True, check=False)
    if fetched.returncode != 0:      # offline: the build still checks it
        return
    import hashlib
    assert hashlib.sha256(fetched.stdout).hexdigest() == pin["sha256"]
