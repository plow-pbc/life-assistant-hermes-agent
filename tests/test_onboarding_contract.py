"""Onboarding is prompt-shaped, so most of it cannot be tested here.

What CAN be asserted is the wiring: the write mode's refusals, which files ship
as assets, when the skill is invoked at all, and the two or three sentences that
carry a real invariant (a token that must not cross a group chat, a claim about
where credentials live). Those are here.

What is deliberately NOT here is the conversation's wording. Whether the opener
reads well, whether the model narrates its own bookkeeping, whether the intro is
short -- none of that is decidable from the text of a prompt, and pinning
paragraphs against it produced tests that broke on every edit while catching
nothing. That evidence comes from transcripts of the agent actually running.
"""
import importlib.util
import io
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL = (ROOT / "ld-setup" / "SKILL.md").read_text()
SOUL = (ROOT / "runtime" / "SOUL.md").read_text()
DOCKERFILE = (ROOT / "Dockerfile").read_text()
ONBOARDING = SKILL[SKILL.index("## Onboarding"):SKILL.index("## The wall (optional)")]
TRIGGER = " ".join(SOUL[SOUL.index("# First run"):SOUL.index("# The wall")].split())

WALL_MARKER = "/opt/data/ld/setup-complete"
# The one call Latch permits, byte for byte as the skill must emit it.
DISCOVERY_ARGV = 'argv=["gog", "calendar", "calendars", "--json", "--results-only"]'


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wc = load("write_config", "ld-setup/scripts/write_config.py")
gate = load("ld_config_gate", "ld-shared/scripts/ld_config_gate.py").gate
ENV = {"TZ": "America/Los_Angeles"}


def draft(config, payload):
    wc.main(["--draft"], env=ENV, config_path=str(config), stdin=io.StringIO(payload))


# --------------------------------------------------------------------------
# --draft: what it may excuse, and what it may not
# --------------------------------------------------------------------------

def test_a_draft_records_an_answer_the_gate_would_refuse(tmp_path):
    """The whole reason the mode exists.

    The gate wants a calendar account and its sources; onboarding never asks
    for either, because they arrive later through Latch. Under --patch every
    answer the owner gave would be refused for a question they had not been
    asked, and the name would reach nothing.
    """
    config = tmp_path / "config.json"
    for answer in ('{"family": {"owner": {"name": "Mary"}}}',
                   '{"family": {"timezone": "America/Los_Angeles"}}',
                   '{"weather": {"location": "Mountain View, California", "lat": 37.4, "lon": -122.1}}',
                   '{"sports": {"followed": []}}'):
        draft(config, answer)
    written = json.loads(config.read_text())
    assert written["family"]["owner"]["name"] == "Mary"
    assert "calendar.account is blank" in gate(written), (
        "if the gate stops refusing this, --draft has no reason to exist")

    with pytest.raises(SystemExit) as refusal:
        wc.main(["--patch"], env=ENV, config_path=str(config),
                stdin=io.StringIO('{"family": {"owner": {"name": "Mary"}}}'))
    assert "the gate says" in str(refusal.value)


@pytest.mark.parametrize("payload,complaint", [
    ('{"family": {"owner": {"name": "   "}}}', "family.owner.name is blank"),
    ('{"family": {"owner": {"name": "[OWNER_NAME]"}}}', "placeholder"),
    ('{"family": {"owner": {"name": 5}}}', "not valid JSON"),
    ('{"calendar": {"account": "a@b.test", "sources": [{"calendar_id": "", "name": "A"}]}}',
     "calendar.sources[].calendar_id is blank"),
    ('{"calendar": {"account": "a@b.test", "sources": ['
     '{"calendar_id": "x@y.test", "name": "A"}, {"calendar_id": "x@y.test", "name": "B"}]}}',
     "not unique"),
    ('{"calendar_nudge": {"owner_identities": []}}', "non-empty list"),
    ('{"calendar_nudge": {"lookahead_virtual_minutes": -5}}', "positive number"),
    ('{"family": {"timezone": "America/New_York"}}', "AGENT_TZ"),
    ('{"wether": {"location": "Denver"}}', "unknown config key"),
])
def test_a_draft_refuses_a_value_that_was_actually_supplied(tmp_path, payload, complaint):
    """The exemption is for questions not yet asked, and nothing else.

    A draft is the record of progress, so a bad value written here is worse
    than a refusal: the next turn reads it as answered and moves on, and the
    owner is never asked again.
    """
    config = tmp_path / "config.json"
    with pytest.raises(SystemExit) as refusal:
        draft(config, payload)
    assert complaint in str(refusal.value)
    assert not config.exists(), "a refused draft must leave nothing behind"


def test_the_stand_ins_only_fill_what_is_absent():
    """fill_unasked() is the whole boundary between the two behaviours: if it
    overwrote a key that was present, a supplied blank name would be replaced
    by a valid stand-in and sail through."""
    supplied = {"family": {"owner": {"name": "   "}},
                "calendar_nudge": {"lookahead_virtual_minutes": -5}}
    original = json.loads(json.dumps(supplied))
    filled = wc.fill_unasked(supplied)
    assert filled["family"]["owner"]["name"] == "   "
    assert filled["calendar_nudge"]["lookahead_virtual_minutes"] == -5
    assert filled["calendar"]["account"]
    assert filled["calendar_nudge"]["lookahead_in_person_minutes"] > 0
    assert supplied == original, "fill mutated its input"


def test_a_draft_starts_from_nothing_but_a_patch_does_not(tmp_path):
    """The first answer arrives before any config exists. --patch must keep
    refusing that -- it is how a mistyped path announces itself instead of
    silently starting a new config."""
    config = tmp_path / "config.json"
    draft(config, '{"family": {"owner": {"name": "Mary"}}}')
    assert config.is_file()
    with pytest.raises(SystemExit) as refusal:
        wc.main(["--patch"], env=ENV, config_path=str(tmp_path / "absent.json"),
                stdin=io.StringIO('{"family": {"owner": {"name": "Mary"}}}'))
    assert "could not read" in str(refusal.value)


def test_draft_and_patch_are_not_both_accepted():
    """Two merge modes with different verdicts; silently preferring one makes
    the strict one unreachable from a caller that thought it asked."""
    with pytest.raises(SystemExit) as refusal:
        wc.main(["--patch", "--draft"], env=ENV,
                stdin=io.StringIO("{}"), config_path="/nonexistent/config.json")
    assert "not both" in str(refusal.value)


def test_discovery_is_what_makes_the_config_installable(tmp_path):
    """End to end, and the reason the lookaheads are written with the calendars.

    Onboarding's own answers can never pass the gate. These writes are what turn
    the draft into a config the producers will run on -- and if any one of
    account, sources, owner_identities or the two lookaheads is missing, it
    stays refused.
    """
    config = tmp_path / "config.json"
    for answer in ('{"family": {"owner": {"name": "Mary"}}}',
                   '{"family": {"timezone": "America/Los_Angeles"}}',
                   '{"weather": {"location": "Mountain View, California", "lat": 37.4, "lon": -122.1}}',
                   '{"sports": {"followed": []}}'):
        draft(config, answer)
    assert gate(json.loads(config.read_text())), "should still be short of installed"

    account = "mary@example.test"
    draft(config, json.dumps({
        "calendar": {"account": account,
                     "sources": [{"calendar_id": account, "name": "Personal"},
                                 {"calendar_id": "fam@group.calendar.google.test", "name": "Family"}]},
        "calendar_nudge": {"owner_identities": [account],
                           "lookahead_virtual_minutes": 30,
                           "lookahead_in_person_minutes": 60}}))
    written = json.loads(config.read_text())
    assert gate(written) == "", f"still refused: {gate(written)}"
    assert [s["calendar_id"] for s in written["calendar"]["sources"]] == \
        [account, "fam@group.calendar.google.test"]
    assert written["family"]["owner"]["name"] == "Mary", "discovery clobbered an earlier answer"


def test_the_lookaheads_alone_do_not_make_it_installable(tmp_path):
    """The inverse: writing the defaults is necessary, not sufficient."""
    config = tmp_path / "config.json"
    draft(config, json.dumps({
        "family": {"owner": {"name": "Mary"}, "timezone": "America/Los_Angeles"},
        "calendar_nudge": {"lookahead_virtual_minutes": 30,
                           "lookahead_in_person_minutes": 60}}))
    assert "calendar.account is blank" in gate(json.loads(config.read_text()))


def test_the_lookahead_defaults_match_the_template_they_come_from():
    """Two literals in prose that must equal the schema's own, or the wall
    starts with a nudge window nobody chose."""
    example = json.loads((ROOT / "ld-shared/references/config.example.json").read_text())
    section = ONBOARDING[ONBOARDING.index("### 5 ·"):]
    assert f'"lookahead_virtual_minutes": {example["calendar_nudge"]["lookahead_virtual_minutes"]}' in section
    assert f'"lookahead_in_person_minutes": {example["calendar_nudge"]["lookahead_in_person_minutes"]}' in section


def test_no_mode_of_write_config_touches_the_crons():
    """The script writes one file. Its docstring once claimed a patch
    re-registered the crons -- through three readings, a merged PR description
    and a review note, while no version of the code ever did it."""
    source = (ROOT / "ld-setup/scripts/write_config.py").read_text()
    assert "subprocess" not in source
    assert "register_crons" not in source.split('"""', 2)[2]
    assert "It does **not** touch the crons" in SKILL


# --------------------------------------------------------------------------
# Assets: what ships in the image
# --------------------------------------------------------------------------

def test_only_the_gif_ships_as_an_asset():
    """Four screenshots shipped on 2026-09-02 carrying faces, a date of birth,
    lab results and diagnoses, an order with a home address and a named $30K
    transfer -- into a public image, sent to every owner who onboards.

    The slot must not quietly refill: an asset baked here reaches everyone.
    """
    assets = ROOT / "docs/onboarding-v2/assets"
    assert sorted(f.name for f in assets.iterdir()) == ["quick-q.gif"]
    baked = [l for l in DOCKERFILE.splitlines() if l.startswith("COPY") and "/srv/plow-assets/" in l]
    assert baked == ["COPY docs/onboarding-v2/assets/quick-q.gif /srv/plow-assets/quick-q.gif"]


def test_nothing_promises_pictures_that_do_not_exist():
    """The lead-in question goes with the images: asked with nothing behind it,
    it is worse than not asking."""
    live, in_comment = [], False
    for line in ONBOARDING.splitlines():
        if "<!--" in line:
            in_comment = True
        if not in_comment:
            live.append(line)
        if "-->" in line:
            in_comment = False
    live = "\n".join(live)
    assert "work-" not in live
    assert "Want to see the kind of thing I mean?" not in live


def test_the_baked_asset_path_is_one_the_media_layer_will_deliver():
    """Hermes drops a model-emitted MEDIA: path under its denylist without an
    error the owner or the agent can see, and this image's whole HERMES_HOME is
    /var/lib/hermes -- so an asset beside the skills is silently undeliverable.
    """
    denied = ("/etc", "/proc", "/sys", "/dev", "/root", "/boot",
              "/var/log", "/var/lib", "/var/run")
    gif = "/srv/plow-assets/quick-q.gif"
    assert f"MEDIA:{gif}" in SKILL
    assert not any(gif.startswith(f"{d}/") for d in denied)


# --------------------------------------------------------------------------
# When onboarding runs at all
# --------------------------------------------------------------------------


@pytest.mark.parametrize("where,text", [
    ("SOUL.md", TRIGGER),
    # The frontmatter decides whether the sheet is loaded at all.
    ("the frontmatter", " ".join(SKILL.split("---", 2)[1].split())),
    # The section's opening is what is read once it has been.
    ("the section's entry condition",
     " ".join(SKILL[SKILL.index("## Onboarding"):SKILL.index("### 1 · Opener")].split())),
])
def test_every_entry_point_states_the_same_three_part_gate(where, text):
    """Three statements of one rule that can disagree is worse than one: a turn
    that finds the loosest of them has its permission. They were three
    different rules -- a solo owner DM, "any inbound message", and "the first
    inbound message from an owner" with nothing about where.
    """
    assert "any inbound message" not in text, f"{where} still admits any inbound"
    assert "owner" in text and "DM" in text, f"{where} does not name the gate"
    assert "roster is just the two of you" in text, f"{where} omits the solo condition"


def test_a_group_gets_no_questions_and_no_writes():
    """Owner-only without solo-DM still runs the interview in a group the owner
    is in; solo-DM without owner-only lets a member's answers reach the owner's
    config. The negative case has to name the writes, not just the questions."""
    assert "onboarding does not exist" in TRIGGER
    assert "no `--draft`, no config, no marker" in TRIGGER


# --------------------------------------------------------------------------
# The marker, and the wall's separate one
# --------------------------------------------------------------------------



def test_the_config_alone_says_whether_to_onboard():
    """A second progress source fails both ways.

    Backwards: every owner already running predates a marker, so a populated
    config plus no marker re-opens the interview. Forwards: a marker written
    before the calendars exist stops the skill being invoked at all, and a
    Latch installed next week is never picked up.
    """
    for field in ("`family.owner.name`", "`weather.location`",
                  "`sports.followed`", "`calendar.sources`"):
        assert field in TRIGGER, f"{field} is not part of the condition"
    assert "present and empty counts as answered" in TRIGGER
    assert "stays missing until Latch is connected" in TRIGGER
    assert "onboarding-complete" not in SOUL and "onboarding-complete" not in SKILL, \
        "the marker is back as a second authority"


def test_the_wall_marker_stays_the_walls_own():
    """They mean different things: an owner with no Pi finishes onboarding and
    never gets a setup-complete. Collapsing them either strands a wall-less
    owner mid-conversation or reports a blank wall as done."""
    assert f"> {WALL_MARKER}" not in ONBOARDING
    assert len(re.findall(rf"^\s*date -u \+%FT%TZ > {re.escape(WALL_MARKER)}\s*$",
                          SKILL, re.MULTILINE)) == 1



def test_discovery_is_one_argv_and_never_auth_list():
    """Latch allows Gmail and Calendar subcommands and nothing else, measured
    against a real relay: `gog auth list` is refused under every binary name,
    so a flow that starts by enumerating accounts dead-ends on a Mac that is
    working correctly, and reads as "no calendars" rather than "wrong command".
    """
    # Twice, byte-identical: the turn-top probe and the section that reads it.
    # The probe IS the listing, so a connected turn already holds what it needs.
    assert ONBOARDING.count(DISCOVERY_ARGV) == 2
    assert not re.search(r'argv=\[[^]]*"auth"', SKILL)


def test_no_display_name_is_persisted_or_shelled():
    """A calendar's display name is written by whoever owns it.

    The pick is composed into a shell heredoc, so a calendar named
    `"; rm -rf ~; echo "` is a command if it reaches one. Producers read
    calendar_id and nothing else, and the gate accepts a source without a name.
    """
    section = " ".join(ONBOARDING[ONBOARDING.index("### 5 ·"):].split())
    assert "Ids only — no `name` key" in section
    assert '"sources": [{"calendar_id": "<id from the script>"}' in ONBOARDING
    assert '"name": "<display name>"' not in ONBOARDING


def test_a_source_without_a_name_still_passes_the_gate():
    """The claim the ids-only write rests on."""
    config = {"family": {"owner": {"name": "M"}, "timezone": "UTC"},
              "calendar": {"account": "a@b.test", "sources": [{"calendar_id": "a@b.test"}]},
              "calendar_nudge": {"owner_identities": ["a@b.test"],
                                 "lookahead_virtual_minutes": 30,
                                 "lookahead_in_person_minutes": 60},
              "weather": {"location": "X", "lat": 1, "lon": 2},
              "sports": {"followed": []}}
    assert gate(config) == ""





def test_the_account_is_taken_from_primary_not_dataowner():
    """dataOwner varies across the list -- the real listing had three distinct
    values across nine calendars, because shares keep their own owner -- so
    deriving the account from it picks whichever calendar was read last."""
    section = " ".join(ONBOARDING[ONBOARDING.index("### 5 ·"):].split())
    assert "the `primary` entry's id rather than `dataOwner`" in section


def test_the_listing_is_normalised_by_a_script_not_by_eye():
    """gog prints a note line before the array, a large result arrives as a
    persisted envelope, and the account is the primary entry rather than
    dataOwner. Each is a silent wrong answer if a model does it by hand."""
    section = ONBOARDING[ONBOARDING.index("### 5 ·"):]
    assert "calendar_list.py" in section
    assert "Do not parse that output yourself" in section
    assert (ROOT / "ld-setup/scripts/calendar_list.py").is_file()


def test_calendar_names_are_named_as_untrusted():
    """They come off someone else's calendar and are read by a model."""
    assert "untrusted data" in ONBOARDING[ONBOARDING.index("### 5 ·"):]


# --------------------------------------------------------------------------
# Two sentences that carry an invariant
# --------------------------------------------------------------------------

def test_the_sheet_names_no_tool_that_does_not_exist():
    """Naming an unavailable tool sends the model hunting for it.

    The sheet described `send_message` -- to say it was NOT callable -- and the
    turn went terminal, skill_view, tool_search, tool_search, tool_search, then
    `clarify` with the question "placeholder". That was the entire first thing
    the agent said to a new owner: "❓ placeholder".
    """
    assert "send_message" not in SKILL


def test_the_turn_makes_as_few_tool_calls_as_it_can():
    """Every gap between two tool calls is somewhere a sentence escapes.

    Both remaining leaks came from gaps that did not need to exist: a config
    read-back after a write that already knew the answer, and a re-probe. The
    fix is fewer calls, not another rule about what not to say in between.
    """
    text = " ".join(ONBOARDING.split())
    assert "Make as few tool calls as the step needs" in text
    assert "Do not read a file back to confirm a write that already reported its result" in text
    # And the city step no longer asks for the read-back at all.
    city = " ".join(ONBOARDING[ONBOARDING.index("### 3 ·"):ONBOARDING.index("### 4 ·")].split())
    assert "Do not read the config back to check" in city


def test_the_draft_reports_what_it_geocoded(tmp_path, capsys):
    """The output that makes the read-back unnecessary."""
    config = tmp_path / "config.json"
    draft(config, '{"family": {"owner": {"name": "M"}}}')
    capsys.readouterr()
    wc.main(["--draft"], env=ENV, config_path=str(config),
            stdin=io.StringIO('{"weather": {"location": "Chicago"}}'))
    out = capsys.readouterr().out
    assert "geocoded: 'Chicago' -> lat=" in out and "lon=" in out


def test_a_draft_that_did_not_geocode_says_nothing_about_it(tmp_path, capsys):
    """Only the call that geocoded reports coordinates -- a line on every write
    would be noise the model learns to repeat."""
    config = tmp_path / "config.json"
    draft(config, '{"family": {"owner": {"name": "M"}}}')
    assert "geocoded:" not in capsys.readouterr().out


def test_the_write_comes_first_and_the_message_is_the_last_thing():
    """The trailing note was the ordering, not the wording.

    Sending the message first leaves the turn needing something to end on, and
    whatever it ends on is delivered -- "Name's drafted, waiting on her next
    reply", "Onboarding's now complete on my end". Six rules about what not to
    say could not fix a turn that structurally had to say something. With the
    write first the message IS the ending.
    """
    text = " ".join(ONBOARDING.split())
    assert "the draft comes first and the message goes last" in text
    assert "the message is the last thing in the turn, and nothing follows it" in text
    # The cost is named here rather than discovered in a transcript later.
    assert "The accepted cost" in text
    assert "the introduction and the Latch link are never sent" in text

    intro = " ".join(ONBOARDING[ONBOARDING.index("### 2 ·"):ONBOARDING.index("### 3 ·")].split())
    assert "Draft the name first, then introduce yourself" in intro
    teams = " ".join(ONBOARDING[ONBOARDING.index("### 3 ·"):ONBOARDING.index("### 4 ·")].split())
    assert "Draft it before writing §4's close" in teams
    close = " ".join(ONBOARDING[ONBOARDING.index("### 4 ·"):ONBOARDING.index("### 5 ·")].split())
    assert "This message is the LAST thing in its turn" in close

    # And "first" must not be read as "before there is anything to write".
    # Observed on a first turn: a fabricated name drafted into the config,
    # then retracted to the owner in two messages.
    assert '"Draft first" means the answer you were just given' in text
    assert "invents no name" in text
    assert "Never compose a value the owner did not say" in text


def test_the_reply_is_the_step_not_a_report_of_it():
    """Deferring all prose to the end made the model summarise instead of speak.

    Observed: turn two's whole reply was "Good, name is drafted. Now waiting on
    Mary's next reply" -- so the introduction, the privacy line and the Latch
    link were never sent, and nothing downstream notices a step that produced a
    sentence instead of a message.
    """
    text = " ".join(ONBOARDING.split())
    assert "write the step's own message" in text
    assert "Not a report of what you just did" in text
    assert "has skipped its own step" in text


def test_clarify_is_forbidden_during_onboarding():
    """The ❓ rows are a tool, and it blocks the turn until someone picks.

    Three times it reached a real owner: a menu asking them to name the
    assistant, a menu asking how messages should be sent, and `❓ placeholder`.
    Banning numbered menus in prose did not cover the tool that renders them.
    """
    text = " ".join(ONBOARDING.split())
    assert "Never call `clarify`" in text
    assert "blocks the turn until the owner picks something" in text
    assert "through the `clarify` tool" in text, "the prose ban must name the tool too"



def test_the_framework_name_is_not_the_agents_name():
    """Observed: "I'm Hermes." That is the software it runs on, the way a
    person is not called Android -- and it was said on a turn where no name
    existed to give."""
    opener = " ".join(SKILL[SKILL.index("### 1 · Opener"):SKILL.index("### 2 ·")].split())
    assert '"Hermes" is not your name' in opener
    assert "do not borrow the framework's" in opener


def test_the_privacy_line_does_not_claim_local_execution():
    """The agent runs in a cloud VM; Latch is what is on the Mac, and the vault
    is Latch's. The earlier wording invited "I run on your machine, not someone
    else's server" -- a false claim about where someone's credentials live,
    made at the moment they are deciding whether to trust it."""
    intro = ONBOARDING[ONBOARDING.index("### 2 ·"):ONBOARDING.index("### 3 ·")]
    assert "The app on your Mac is where your accounts live" in " ".join(intro.split())
    assert "not** in your own words" in intro
    # The counter-example is quoted on purpose, behind a NOT: marker.
    said = "\n".join(l for l in intro.splitlines() if "NOT:" not in l)
    for false_claim in ("run on your own machine", "run on their machine",
                        "nothing ever leaves", "not someone else's server"):
        assert false_claim not in said, f"the intro still invites {false_claim!r}"


def test_the_wall_token_handoff_is_dm_only():
    """The one place a bearer token crosses chat. In a group every participant
    keeps it in their history forever, and there is nothing to rotate short of
    re-minting and re-shipping the Pi."""
    wall = SKILL[SKILL.index("**No Mac (or no Latch):**"):]
    handoff = " ".join(wall[:wall.index("date -u +%FT%TZ > /opt/data/ld/pi-brought-up")].split())
    assert "in the owner's own one-to-one thread and nowhere else*, and never in a group" in handoff
    assert handoff.index("one-to-one thread and nowhere else") < handoff.index("text the owner, verbatim")
