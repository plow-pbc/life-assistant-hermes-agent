"""The pinned client owns assertion exchange and report-key storage."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "image/s6-overlay/s6-rc.d/agent-index/run"


def test_image_has_no_bespoke_key_exchanger():
    assert not (ROOT / "image/s6-overlay/scripts/agent-index-key.py").exists()
    assert "agent-index-key.py" not in (ROOT / "Dockerfile").read_text()


def test_the_service_takes_the_container_environment_from_s6():
    """Which invocation is handed the bearer is asserted by running the script
    (tests/test_agent_index_service.py), not by reading it. This is the one
    fact that cannot be: the shebang is what puts the environment there."""
    assert RUN.read_text().startswith("#!/command/with-contenv sh")

