"""The interview's answers become the config every producer trusts.

Behavior, not shape: each row is judged by the SHARED gate
(ld-shared/scripts/ld_config_gate.py, imported), because that gate is the
single definition of "installed" and a config it refuses is a 06:00 failure
in front of nobody. The timezone check lives here too -- the owner is the
one answering, and the container's zone is fixed at boot, so the refusal has
to name it for the owner to relay.
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wc = load("write_config", "ld-setup/scripts/write_config.py")
gate = load("ld_config_gate", "ld-shared/scripts/ld_config_gate.py").gate

TZ = "America/Chicago"
ENV = {"TZ": TZ}
FULL = {
    "owner_name": "Rowan", "owner_email": "rowan@example.test",
    "owner_imessage": "+15550001111", "city": "Chicago", "timezone": TZ,
    "has_mac": True, "mac_username": "rowan",
    "extra_calendar_ids": ["fam@group.calendar.google.com"],
    "people": ["Mary"], "teams": [{"abbr": "chc", "sport": "baseball", "league": "mlb"}],
    "digest_length": "short",
}
MINIMAL = {"owner_name": "Rowan", "owner_email": "rowan@example.test",
           "city": "Chicago", "timezone": TZ, "has_mac": False}


def fake_geocode(city):
    """Open-Meteo, stubbed. No test in this suite touches the network: a live
    lookup makes the suite fail on a train, and it made this one flaky."""
    assert city == "Chicago"
    return 41.85, -87.65, "Chicago, Illinois, United States"









def live_config():
    # Built here rather than through a whole-config mode: that mode was a form,
    # and it is gone. This is the shape --patch expects to find on disk.
    return {
        "family": {"owner": {"name": FULL["owner_name"], "imessage": FULL["owner_imessage"]},
                   "people": list(FULL["people"]), "timezone": TZ},
        "calendar": {"account": FULL["owner_email"],
                     "sources": [{"calendar_id": FULL["owner_email"], "name": "Personal"},
                                 {"calendar_id": FULL["extra_calendar_ids"][0],
                                  "name": FULL["extra_calendar_ids"][0]}]},
        "weekly_digest": {"length": FULL["digest_length"], "long_lead": []},
        "morning_triage": {"chat_db_path": f"/Users/{FULL['mac_username']}/Library/Messages/chat.db",
                           "ranking_instructions": "", "exclude": {"imessage_handles": []}},
        "calendar_nudge": {"lookahead_virtual_minutes": 30, "lookahead_in_person_minutes": 60,
                           "owner_identities": [FULL["owner_email"]]},
        "weather": {"location": FULL["city"], "lat": 41.85, "lon": -87.65},
        "sports": {"followed": list(FULL["teams"])},
    }


def test_a_write_reports_the_place_it_matched_never_the_coordinates(tmp_path, monkeypatch, capsys):
    """The output that lets a caller check the geocode without reading the
    config back -- a second tool call, and the gap between two of them is where
    "Coordinates check out fine (37.38, -122.08)" reached a real owner.

    The region is what separates the Mountain View in California from the one
    in Arkansas, which is the only question a caller has. A lat/lon answers it
    by writing the owner's home to five decimal places into a log that outlives
    the turn (CodeQL py/clear-text-logging-sensitive-data, high).
    """
    monkeypatch.setattr(wc, "geocode", fake_geocode)
    target = tmp_path / "ld" / "config.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(live_config()))
    capsys.readouterr()
    wc.main(["--patch"], stdin=io.StringIO('{"weather": {"location": "Chicago"}}'),
            env=ENV, config_path=str(target))
    out = capsys.readouterr().out
    assert "geocoded: matched Chicago, Illinois, United States" in out
    for leaked in ("lat=", "lon=", "41.85", "-87.65"):
        assert leaked not in out, f"the write logged {leaked!r}"
    # Written to the config, where the producers need them; just not printed.
    assert json.loads(target.read_text())["weather"]["lat"] == 41.85


def test_a_write_that_did_not_geocode_says_nothing_about_it(tmp_path, capsys):
    """Only the call that geocoded reports a place -- a line on every write
    would be noise the model learns to repeat back to the owner."""
    target = tmp_path / "ld" / "config.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(live_config()))
    capsys.readouterr()
    wc.main(["--patch"], stdin=io.StringIO('{"family": {"owner": {"name": "Ro"}}}'),
            env=ENV, config_path=str(target))
    assert "geocoded:" not in capsys.readouterr().out


def test_a_patch_changes_one_setting_and_leaves_the_rest_alone():
    current = live_config()
    merged = wc.apply_patch({"family": {"owner": {"name": "Ro"}}}, current, ENV)[0]
    assert merged["family"]["owner"]["name"] == "Ro"
    # Everything the owner did not restate: the sibling key inside the object
    # that was patched, and every other section.
    assert merged["family"]["owner"]["imessage"] == FULL["owner_imessage"]
    assert merged["calendar"] == current["calendar"]
    assert merged["weather"] == current["weather"]
    assert gate(merged) == ""


def test_a_patched_list_replaces_rather_than_grows():
    """followed teams and calendar sources are sets the owner states in full;
    a patch that could only append would have no way to drop one."""
    merged = wc.apply_patch(
        {"sports": {"followed": [{"abbr": "bos", "sport": "baseball", "league": "mlb"}]}},
        live_config(), ENV)[0]
    assert [t["abbr"] for t in merged["sports"]["followed"]] == ["bos"]


def test_a_new_city_takes_its_coordinates_with_it():
    """The one patch that fails silently: the card's title becomes the new city
    and the forecast stays the old one's."""
    merged = wc.apply_patch({"weather": {"location": "Denver"}}, live_config(), ENV,
                            geocoder=lambda city: (39.74, -104.99, "Denver, Colorado, United States"))[0]
    assert merged["weather"] == {"location": "Denver", "lat": 39.74, "lon": -104.99}


# A patch is composed by a model from a sentence, so a misspelling merges in
# BESIDE the real key rather than failing -- the gate then passes on the old
# value and the owner is told a change landed that never did. The nested and
# list-item rows are the half a top-level section check cannot see.
@pytest.mark.parametrize("patch,expected", [
    ({"wether": {"location": "Denver"}}, "'wether'"),
    ({"family": {"owner": {"nme": "Ro"}}}, "'family.owner.nme'"),
    ({"sports": {"followed": [{"abbr": "bos", "leage": "mlb"}]}},
     "'sports.followed[0].leage'"),
], ids=["section", "nested", "list-item"])
def test_an_invalid_patch_refuses_and_names_what_is_wrong(patch, expected):
    with pytest.raises(SystemExit) as e:
        wc.apply_patch(patch, live_config(), ENV)
    assert expected in str(e.value)


def test_the_restart_notice_reaches_the_caller_with_the_write(tmp_path, monkeypatch, capsys):
    """Through main(), the way a turn actually sees it -- and only after the
    write landed, so a refusal never prints a note about a zone that is not in
    the file."""
    monkeypatch.setattr(wc, "geocode", fake_geocode)
    target = tmp_path / "ld" / "config.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(live_config()))
    capsys.readouterr()
    wc.main(["--patch"], stdin=io.StringIO('{"family": {"timezone": "America/Los_Angeles"}}'),
            env={"TZ": "UTC"}, config_path=str(target))
    out = capsys.readouterr().out
    assert f"wrote {target}" in out
    assert "restart" in out and "America/Los_Angeles" in out
    assert json.loads(target.read_text())["family"]["timezone"] == "America/Los_Angeles"


def test_a_patch_the_gate_would_refuse_never_reaches_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(wc, "geocode", fake_geocode)
    target = tmp_path / "ld" / "config.json"
    target.parent.mkdir(parents=True, exist_ok=True); target.write_text(json.dumps(live_config()))
    before = target.read_text()

    with pytest.raises(SystemExit) as e:
        wc.main(["--patch"], env=ENV, config_path=str(target),
                stdin=io.StringIO(json.dumps({"calendar_nudge": {"owner_identities": []}})))
    assert "the gate says" in str(e.value)
    assert target.read_text() == before


def test_the_installed_command_line_actually_reaches_the_patch_path(tmp_path):
    """Through __main__, not through main() -- the seam every other test here
    skips, and the one the documented invocation actually uses.

    `main()` defaults its argv to [] on purpose, so a library caller never picks
    up pytest's own argv; the entry point has to pass sys.argv[1:] itself. When
    it passed [] instead, `--patch` was parsed as no flags and the partial config
    went through the first-run path, which refuses it as missing answers.

    Run against a config path in a writable temp dir -- a verbatim copy of the
    script with only its CONFIG constant repointed, so the entry point under
    test is the real one. Pointing it at /var/lib/hermes instead made the LOCK refuse
    first (it is taken before the mode branch and fails closed), and a lock
    error proves nothing about how --patch was parsed.

    The refusal it reaches is the proof: "could not read ... config.json" is the
    patch path -- a patch onto a config that does not exist yet -- while
    "missing required answer(s)" is the first-run path.
    """
    source = (ROOT / "ld-setup" / "scripts" / "write_config.py").read_text()
    target = tmp_path / "ld" / "config.json"
    assert f'CONFIG = "{wc.CONFIG}"' in source, "the constant moved; repoint this copy"
    script = tmp_path / "write_config.py"
    script.write_text(source.replace(f'CONFIG = "{wc.CONFIG}"',
                                     f'CONFIG = {str(target)!r}'))
    proc = subprocess.run(
        [sys.executable, str(script), "--patch"],
        input=json.dumps({"family": {"owner": {"name": "Ro"}}}),
        capture_output=True, text=True,
        # The copy lives outside the repo, so the shared gate and lock helper
        # it imports by relative path have to be reachable some other way.
        env={**os.environ, "TZ": TZ,
             "PYTHONPATH": str(ROOT / "ld-shared" / "scripts")})

    combined = proc.stdout + proc.stderr
    assert "missing required answer(s)" not in combined, (
        "--patch was dropped and the partial config went through build()")
    assert "refusing to patch" in combined, combined


def test_a_write_that_fails_leaves_the_previous_config_intact(tmp_path, monkeypatch):
    """The config is the only copy of every preference the owner is NOT
    restating, which is the whole point of --patch. A truncate-then-write
    destroys it before the replacement exists, so a failure in that window
    leaves an empty file -- and an unreadable config stands every producer
    down at once, silently."""
    monkeypatch.setattr(wc, "geocode", fake_geocode)
    target = tmp_path / "ld" / "config.json"
    target.parent.mkdir(parents=True, exist_ok=True); target.write_text(json.dumps(live_config()))
    before = target.read_bytes()

    def boom(*_args):
        raise OSError("no space left on device")

    monkeypatch.setattr(wc.os, "replace", boom)
    with pytest.raises(OSError):
        wc.main(["--patch"], env=ENV, config_path=str(target),
                stdin=io.StringIO(json.dumps({"family": {"owner": {"name": "Ro"}}})))

    assert target.read_bytes() == before
    # And nothing half-written left beside it for the next run to trip over.
    # The lock file is a permanent sibling, not debris: it is what every writer
    # takes before its read so two turns cannot merge over each other.
    assert sorted(p.name for p in target.parent.iterdir()) == [
        "config.json", "config.json.lock"]


@pytest.mark.parametrize("token", ["Infinity", "-Infinity", "NaN"], ids=["inf", "-inf", "nan"])
def test_a_patch_carrying_a_non_standard_json_constant_is_refused(
    tmp_path, monkeypatch, token
):
    """Python parses NaN/Infinity and writes them back, and the shared gate
    passes them (`float("inf") > 0`). Written out, ld_config_gate's own reader
    -- which already refuses those tokens -- calls the live config "not valid
    JSON" and stands every producer down. Refuse at the door instead."""
    monkeypatch.setattr(wc, "geocode", fake_geocode)
    target = tmp_path / "ld" / "config.json"
    target.parent.mkdir(parents=True, exist_ok=True); target.write_text(json.dumps(live_config()))
    before = target.read_bytes()

    with pytest.raises(SystemExit) as e:
        wc.main(["--patch"], env=ENV, config_path=str(target),
                stdin=io.StringIO(
                    '{"calendar_nudge": {"lookahead_virtual_minutes": %s}}' % token))

    assert token in str(e.value)
    assert target.read_bytes() == before



def test_two_concurrent_patches_both_survive(tmp_path, monkeypatch, run_concurrently):
    """The loss a lock exists to prevent, and the reason it is not theoretical.

    Both modes here are read-modify-write. Two turns can run at once -- an owner
    answering while a cron producer patches, or two answers arriving back to
    back -- and unlocked, the second read happens before the first rename lands,
    so the second write publishes a merge that never saw the first answer.

    Not a corrupt file. A clean, valid config missing a reply the owner already
    gave, which nothing downstream can notice and nobody traces back.
    """
    monkeypatch.setattr(wc, "geocode", fake_geocode)
    target = tmp_path / "ld" / "config.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(live_config()))

    def patch(payload):
        return lambda: wc.main(["--patch"], stdin=io.StringIO(json.dumps(payload)),
                               env=ENV, config_path=str(target))

    assert not run_concurrently(patch({"family": {"owner": {"name": "Ro"}}}),
                                patch({"weekly_digest": {"length": "long"}}))

    written = json.loads(target.read_text())
    assert written["family"]["owner"]["name"] == "Ro", "the name write was lost"
    assert written["weekly_digest"]["length"] == "long", "the digest write was lost"
    # And the rest is intact, so neither writer replaced the file with its own
    # partial view of it.
    assert written["calendar"] == live_config()["calendar"]


def test_two_first_drafts_race_before_the_directory_exists(tmp_path, run_concurrently):
    """The case fail-open got wrong, and the reason the lock creates the dir.

    On a FIRST draft there is no `ld/` yet. An earlier version took the lock
    inside that directory and ran the work anyway when it could not -- so
    neither writer held a lock, both created the directory, both read nothing,
    and both wrote: an answer lost every time, five runs out of five.
    """
    target = tmp_path / "ld" / "config.json"
    assert not target.parent.exists(), "the point of this test is the missing dir"

    def draft(payload):
        return lambda: wc.main(["--draft"], stdin=io.StringIO(json.dumps(payload)),
                               env=ENV, config_path=str(target))

    assert not run_concurrently(draft({"family": {"owner": {"name": "Ro"}}}),
                                draft({"sports": {"followed": []}}))

    written = json.loads(target.read_text())
    assert written["family"]["owner"]["name"] == "Ro", "the name draft was lost"
    assert "followed" in written.get("sports", {}), "the teams draft was lost"
    assert oct(target.parent.stat().st_mode)[-3:] == "700", (
        "the directory the lock created holds a person's data")


def test_a_staged_input_is_removed_after_a_write_and_kept_after_a_refusal(tmp_path):
    """The staged file is the owner's own words -- a name, a city, calendar ids.

    Left beside the config it is a second copy with no reader, and a later turn
    that finds a stale one can act on an answer nobody just gave. Removed only
    on success: a refusal keeps it, because the turn's next move is to fix what
    it named and run again, and deleting the evidence would make that guesswork.
    """
    target = tmp_path / "ld" / "config.json"
    staged = tmp_path / ".draft-abcd1234.json"

    staged.write_text('{"family": {"owner": {"name": "Ro"}}}')
    wc.main(["--draft", "--input", str(staged)], env=ENV, config_path=str(target))
    assert not staged.exists(), "the staged answers outlived the write"
    assert json.loads(target.read_text())["family"]["owner"]["name"] == "Ro"

    staged.write_text('{"wether": {"location": "Denver"}}')
    with pytest.raises(SystemExit):
        wc.main(["--draft", "--input", str(staged)], env=ENV, config_path=str(target))
    assert staged.exists(), "a refusal deleted the file the turn has to fix"


def test_an_input_that_is_the_config_is_refused(tmp_path):
    """The staged input is deleted once written through, so an input that IS the
    config would delete the household's config on a write that reported PASS --
    every answer they ever gave, gone, with a success line above it.

    It is also never what a caller means: --input carries a PARTIAL config to
    merge, and merging a file onto itself changes nothing.
    """
    target = tmp_path / "ld" / "config.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(live_config()))

    for path in (str(target), str(target.parent / "." / "config.json")):
        with pytest.raises(SystemExit) as refusal:
            wc.main(["--patch", "--input", path], env=ENV, config_path=str(target))
        assert "--input is the config itself" in str(refusal.value)
    assert json.loads(target.read_text()) == live_config(), "the config was touched"


def test_a_staged_input_that_cannot_be_removed_is_reported(tmp_path, monkeypatch):
    """Not swallowed. The file holds the owner's own words, and one left behind
    is a second copy of their data that nothing will read. The write succeeded,
    so the refusal says both -- what landed and what did not -- and exits
    non-zero so a caller cannot read the run as clean."""
    target = tmp_path / "ld" / "config.json"
    staged = tmp_path / ".draft-abcd1234.json"
    staged.write_text('{"family": {"owner": {"name": "Ro"}}}')

    def refuse_remove(path):
        raise OSError(13, "Permission denied")
    monkeypatch.setattr(wc.os, "remove", refuse_remove)

    with pytest.raises(SystemExit) as refusal:
        wc.main(["--draft", "--input", str(staged)], env=ENV, config_path=str(target))
    message = str(refusal.value)
    assert "could not remove the staged answers" in message
    assert "wrote" in message, "the caller is not told the write landed"
    # The write really did land -- this is a partial success, reported as one.
    assert json.loads(target.read_text())["family"]["owner"]["name"] == "Ro"
