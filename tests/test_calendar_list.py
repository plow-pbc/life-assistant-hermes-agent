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


@pytest.mark.parametrize("label,payload", [
    # The output is not JSON: a parse of the whole string fails on a WORKING
    # call, and a turn that reads that as "no calendars" tells the owner the
    # wrong thing.
    ("inline, with gog's preamble", PREAMBLE + json.dumps(LISTING)),
    # A large result comes back wrapped, naming a file rather than the text.
    ("a persisted envelope", json.dumps({"result": json.dumps(
        {"exit_code": 0, "output": PREAMBLE + json.dumps(LISTING)})})),
])
def test_both_delivery_shapes_reach_the_same_answer(tmp_path, label, payload):
    result = cl.normalize(cl.extract_array(cl.read_gather(gather(tmp_path, payload))))
    assert result["account"] == "mary@example.test"
    assert [c["id"] for c in result["calendars"]] == [
        "mary@example.test", "fam@group.calendar.google.test"]


def test_a_nonzero_exit_refuses(tmp_path):
    """An error envelope is not an empty listing."""
    envelope = json.dumps({"result": json.dumps({"exit_code": 1, "output": ""})})
    with pytest.raises(GatherError):
        cl.read_gather(gather(tmp_path, envelope))


def test_the_display_is_the_owners_rename():
    """summaryOverride is what they see in Google Calendar, so it is what they
    will recognise read back."""
    result = cl.normalize(LISTING)
    assert [c["display"] for c in result["calendars"]] == ["mary@example.test", "Ours"]


@pytest.mark.parametrize("label,flagged,owned_by,expected", [
    # 1. The clearest signal, and the only one seen live.
    ("one flagged primary", ["mary@example.test"], {"mary@example.test"}, "mary@example.test"),
    ("flag wins over a disagreeing owner set",
     ["mary@example.test"], {"mary@example.test", "other@example.test"}, "mary@example.test"),
    # 2. No flag, but every owner-role calendar names the same owner.
    ("no flag, one owner", [], {"mary@example.test"}, "mary@example.test"),
    # 3. Nothing decides it -- ask, never guess.
    ("no flag, several owners", [], {"a@example.test", "b@example.test"}, None),
    ("no flag, no owner rows", [], set(), None),
    ("two different primaries", ["a@example.test", "b@example.test"], {"a@example.test"}, None),
])
def test_the_account_is_derived_or_left_for_the_owner(label, flagged, owned_by, expected):
    """An account guessed wrong is not a visible failure: it is written to
    calendar.account, every producer authenticates as that identity, and the
    reads come back thin for reasons nobody traces back here. `primary: true`
    was seen on ONE Mac, so its absence is a case, not an error."""
    assert cl.derive_account(flagged, owned_by) == expected


def test_a_shared_calendar_never_votes_for_the_account():
    """A calendar shared into the account carries THEIR address in dataOwner.
    Counting it would make a stranger a candidate for this owner's config."""
    result = cl.normalize([
        {"id": "mary@example.test", "summary": "Mine", "accessRole": "owner",
         "dataOwner": "mary@example.test"},
        {"id": "team@group.calendar.google.test", "summary": "Team",
         "accessRole": "reader", "dataOwner": "someone@else.test"},
    ])
    assert result["account"] == "mary@example.test"


def test_no_signal_at_all_leaves_the_account_null():
    """The listing is still returned -- the owner can be asked which account is
    theirs, which needs the calendars in hand."""
    result = cl.normalize([{"id": "a@example.test", "summary": "A", "accessRole": "reader"},
                           {"id": "b@example.test", "summary": "B", "accessRole": "reader"}])
    assert result["account"] is None
    assert [c["id"] for c in result["calendars"]] == ["a@example.test", "b@example.test"]


@pytest.mark.parametrize("label,entries", [
    ("nothing to choose from", []),
    ("a calendar with no id", [{"summary": "No id"}]),
    ("a blank id", [{"id": "   ", "primary": True}]),
    ("not a list", "not a list"),
])
def test_a_listing_that_cannot_be_used_refuses(label, entries):
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
