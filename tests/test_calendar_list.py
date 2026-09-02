"""The calendar listing, normalised deterministically instead of by eye.

Every case here is a shape gog actually returns, and each one has a silent
wrong answer if a model parses it in prose: the preamble makes the output
invalid JSON, a large result arrives as an envelope naming a file, and the
account is the primary entry rather than dataOwner -- which varies across
calendars shared into an account.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cl = load("calendar_list", "ld-setup/scripts/calendar_list.py")
# The script's own GatherError, not a second import of the module: loading
# gather_result again here would make a DIFFERENT class object, and every
# pytest.raises below would miss the exception it was written for.
GatherError = cl.GatherError

PREAMBLE = "Note: Using direct access token (expires in ~1 hour; no auto-refresh)\n"
LISTING = [
    {"id": "mary@example.test", "summary": "mary@example.test", "primary": True,
     "accessRole": "owner", "dataOwner": "mary@example.test"},
    {"id": "fam@group.calendar.google.test", "summary": "Family",
     "summaryOverride": "Ours", "accessRole": "reader", "dataOwner": "someone@else.test"},
]


def gather(tmp_path, text):
    path = tmp_path / "gather.txt"
    path.write_text(text)
    return str(path)


def test_the_preamble_is_skipped(tmp_path):
    """The output is not JSON. A parse of the whole string fails on a WORKING
    call, and a turn that treats that as "no calendars" tells the owner the
    wrong thing."""
    result = cl.normalize(cl.extract_array(cl.read_gather(
        gather(tmp_path, PREAMBLE + json.dumps(LISTING)))))
    assert result["account"] == "mary@example.test"
    assert [c["id"] for c in result["calendars"]] == [
        "mary@example.test", "fam@group.calendar.google.test"]


def test_a_persisted_envelope_is_unwrapped(tmp_path):
    """A large result comes back wrapped rather than inline."""
    envelope = json.dumps({"result": json.dumps(
        {"exit_code": 0, "output": PREAMBLE + json.dumps(LISTING)})})
    result = cl.normalize(cl.extract_array(cl.read_gather(gather(tmp_path, envelope))))
    assert result["account"] == "mary@example.test"


def test_a_nonzero_exit_refuses(tmp_path):
    """An error envelope is not an empty listing."""
    envelope = json.dumps({"result": json.dumps({"exit_code": 1, "output": ""})})
    with pytest.raises(GatherError):
        cl.read_gather(gather(tmp_path, envelope))


def test_the_owners_rename_wins_over_the_summary():
    """summaryOverride is what they see in Google Calendar, so it is what they
    will recognise being read back."""
    result = cl.normalize(LISTING)
    assert [c["display"] for c in result["calendars"]] == ["mary@example.test", "Ours"]


def test_the_account_is_primary_never_dataowner():
    """Shares keep their own owner -- a real listing had three distinct
    dataOwner values across nine calendars -- so deriving the account from it
    picks whichever calendar was read last."""
    result = cl.normalize(LISTING)
    assert result["account"] == "mary@example.test"
    assert "dataOwner" not in json.dumps(result)


def test_no_primary_refuses_rather_than_guessing():
    """Guessing the first row would put a SHARED calendar's address into
    calendar.account, and every producer would authenticate as someone else."""
    with pytest.raises(GatherError, match="primary"):
        cl.normalize([{"id": "shared@example.test", "summary": "Shared"}])


def test_two_primaries_refuse():
    """Not a shape gog should produce; if it does, one of them is wrong and
    picking either silently is worse than saying so."""
    with pytest.raises(GatherError, match="primary"):
        cl.normalize([{"id": "a@b.test", "primary": True},
                      {"id": "c@d.test", "primary": True}])


@pytest.mark.parametrize("entries", [
    [],                                    # nothing to choose from
    [{"summary": "No id"}],                # a calendar with no id
    [{"id": "   ", "primary": True}],      # a blank one
    "not a list",
])
def test_a_shape_that_cannot_be_used_refuses(entries):
    with pytest.raises(GatherError):
        cl.normalize(entries)


def test_a_listing_with_no_array_refuses():
    with pytest.raises(GatherError, match="no JSON array"):
        cl.extract_array("Note: something went sideways")


def test_a_display_name_is_carried_but_never_becomes_an_id():
    """The display string is attacker-controlled: it may be shown to the owner
    and must never be persisted or reach a shell."""
    hostile = '"; rm -rf ~; echo "'
    result = cl.normalize([{"id": "a@b.test", "summary": hostile, "primary": True}])
    assert result["calendars"][0]["display"] == hostile
    assert result["account"] == "a@b.test"
    assert result["calendars"][0]["id"] == "a@b.test"
