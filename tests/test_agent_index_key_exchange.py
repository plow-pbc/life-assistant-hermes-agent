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
    assert "PLOW_AGENT_TOKEN" in bootstrap
    assert "agent-index-client.py" in bootstrap
    assert "--register" in bootstrap
    assert "--name" not in bootstrap, "boot must never own publisher page content"


def test_hourly_report_receives_only_the_stored_key():
    run = commands(RUN.read_text())
    reporter = run.split("\n  esac\n", 1)[1].split("/bin/sleep 3600", 1)[0]
    assert "agent-index-client.py" in reporter
    assert "PLOW_AGENT_TOKEN" not in reporter


def test_the_registration_gate_keeps_no_path_of_its_own():
    """Where this install's state lives is the client's to know. The gate that
    named a path here named one the client had stopped writing, and re-registered
    every tenant on the hour; it asks now, and asking has nothing to go stale."""
    run = commands(RUN.read_text())
    assert ".agent-index/token" not in run and ".agent-index.json" not in run
    assert "load_state" in run, "it asks the client whether this install is registered"
