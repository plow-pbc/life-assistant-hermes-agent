"""The owner's name lives on their Plow account. These are the two things a
turn does with it: read what to call them, and record what they said."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "owner_profile", ROOT / "ld-shared" / "scripts" / "owner_profile.py")
op = importlib.util.module_from_spec(spec)
spec.loader.exec_module(op)


@pytest.fixture
def api(monkeypatch):
    """Stand in for the API: records every call, answers from `profile`."""
    calls = []
    state = {"profile": {"display_name": None, "photo_url": None}}

    def fake(method, base, path, token, label, body=None):
        calls.append((method, path, body))
        if method == "PATCH":
            state["profile"]["display_name"] = body["display_name"]
        return dict(state["profile"])

    monkeypatch.setattr(op, "request_json", fake)
    monkeypatch.setenv("PLOW_API_BASE", "https://api.test")
    monkeypatch.setenv("PLOW_AGENT_TOKEN", "tok")  # pragma: allowlist secret
    return calls, state


def test_get_says_unset_in_words_when_the_account_has_no_name(api, capsys):
    op.main(["get"])
    assert capsys.readouterr().out.strip() == "(unset)"


def test_get_prints_the_name(api, capsys):
    _, state = api
    state["profile"]["display_name"] = "Samuel Odio"
    op.main(["get"])
    assert capsys.readouterr().out.strip() == "Samuel Odio"


def test_set_records_exactly_what_the_owner_said(api, capsys):
    calls, _ = api
    op.main(["set", "Sam"])
    assert calls == [("PATCH", "/v1/auth/profile", {"display_name": "Sam"})]
    assert capsys.readouterr().out.strip() == "Sam"


def test_set_refuses_a_blank_name(api):
    with pytest.raises(SystemExit) as refusal:
        op.main(["set", "   "])
    assert "blank" in str(refusal.value)


@pytest.mark.parametrize("name", ["PLOW_API_BASE", "PLOW_AGENT_TOKEN"])
def test_a_missing_credential_refuses_by_name(api, monkeypatch, name):
    monkeypatch.delenv(name)
    with pytest.raises(SystemExit) as refusal:
        op.main(["get"])
    assert name in str(refusal.value)
