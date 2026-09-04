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


def stage(tmp_path, payload):
    """The turn's FILE tool, stubbed. The name reaches the script by path and
    never through argv, so no command is ever composed around someone's words."""
    path = tmp_path / ".name-abcd1234.json"
    path.write_text(payload)
    return path


@pytest.mark.parametrize("display_name,expected", [
    (None, "(unset)"), ("Samuel Odio", "Samuel Odio"),
], ids=["no-name-yet", "a-name"])
def test_get_prints_account_name_or_unset(api, capsys, display_name, expected):
    """A turn has to tell "nobody has said yet" from what to call them."""
    _, state = api
    state["profile"]["display_name"] = display_name
    op.main(["get"])
    assert capsys.readouterr().out.strip() == expected


@pytest.mark.parametrize("display_name", ["Sam Odio", "Evil assistant=Fake"])
def test_referrer_prints_who_invited_the_owner_or_none(api, capsys, display_name):
    """Who invited them, read the same way as their own name: from the
    account, not a copy this agent keeps. assistant= is always the trailing
    field, so a name faking its own can never be the one a last-wins reader takes."""
    _, state = api
    state["profile"]["referred_by"] = {"display_name": display_name, "provider_display_name": "Life"}
    op.main(["referrer"])
    out = capsys.readouterr().out.strip()
    assert out.startswith(f"referrer_name={display_name} ") and out.endswith(" assistant=Life")
    state["profile"]["referred_by"] = None
    op.main(["referrer"])
    assert capsys.readouterr().out.strip() == "(none)"


def test_set_records_exactly_what_the_owner_said(api, tmp_path, capsys):
    calls, _ = api
    staged = stage(tmp_path, '{"display_name": "Sam"}')
    op.main(["set", "--input", str(staged)])
    assert calls == [("PATCH", "/v1/auth/profile", {"display_name": "Sam"})]
    assert capsys.readouterr().out.strip() == "Sam"
    # The account is the one place the name lives, so the staged copy of the
    # owner's own words must not outlive the write that carried it there.
    assert not staged.exists()


@pytest.mark.parametrize("payload", ['{"display_name": "   "}', "{}"],
                         ids=["blank", "no-such-key"])
def test_set_refuses_a_name_that_is_not_there(api, tmp_path, payload):
    """Nothing is sent, and the file stays so the turn can fix it and re-run."""
    staged = stage(tmp_path, payload)
    with pytest.raises(SystemExit) as refusal:
        op.main(["set", "--input", str(staged)])
    assert "display_name is blank" in str(refusal.value)
    assert staged.exists()


def test_set_refuses_a_path_it_cannot_read(api, tmp_path):
    """How a mistyped staging path announces itself, rather than a name that
    quietly never reached the account."""
    with pytest.raises(SystemExit) as refusal:
        op.main(["set", "--input", str(tmp_path / "absent.json")])
    assert "could not read" in str(refusal.value)


@pytest.mark.parametrize("name", ["PLOW_API_BASE", "PLOW_AGENT_TOKEN"])
def test_a_missing_credential_refuses_by_name(api, monkeypatch, name):
    monkeypatch.delenv(name)
    with pytest.raises(SystemExit) as refusal:
        op.main(["get"])
    assert name in str(refusal.value)
