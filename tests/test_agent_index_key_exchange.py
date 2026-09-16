"""The pinned client owns assertion exchange and report-key storage."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "image/s6-overlay/s6-rc.d/agent-index/run"


def commands(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


def test_image_has_no_bespoke_key_exchanger():
    assert not (ROOT / "image/s6-overlay/scripts/agent-index-key.py").exists()
    assert "agent-index-key.py" not in (ROOT / "Dockerfile").read_text()


def test_bootstrap_invokes_the_pinned_client_with_the_plow_token():
    run = commands(RUN.read_text())
    bootstrap = run.split("\n    3)\n", 1)[1].split("\n      ;;", 1)[0]
    # The script runs under with-contenv, so the bearer is in its environment
    # and this child inherits it. What must not appear is an unset.
    assert RUN.read_text().startswith("#!/command/with-contenv sh")
    assert "-u PLOW_AGENT_TOKEN" not in bootstrap
    assert "agent-index-client.py" in bootstrap
    assert "--register" in bootstrap
    assert "--name" not in bootstrap, "boot must never own publisher page content"


def test_every_report_receives_only_the_stored_key():
    run = commands(RUN.read_text())
    reporter = run.split("\n  esac\n", 1)[1].split("/bin/sleep 300", 1)[0]
    assert "agent-index-client.py" in reporter
    # Unset rather than merely not passed: with-contenv put it in this script's
    # environment, so a child that says nothing about it inherits it.
    assert "env -u PLOW_AGENT_TOKEN" in reporter

