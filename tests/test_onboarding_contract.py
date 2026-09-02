"""Onboarding is prompt-shaped, so its invariants are about text and paths.

There is no onboarding *program* to test: the conversation lives in
ld-setup/SKILL.md and runtime/SOUL.md, and the model runs it. What can be
asserted is the wiring underneath -- that the two documents name the same
marker, that the marker is written exactly once and nowhere earlier, that the
GIF the opener sends is baked at a path Hermes will actually deliver, and that
the draft mode the conversation depends on records an answer the shared gate
would refuse. Each of these fails quietly in production: a marker mismatch is
an owner re-onboarded on every message, a bad asset path is a missing picture
with no error anywhere, and a gated draft is an answer the owner gave and the
agent silently dropped.
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
# The one call Latch actually permits, byte for byte as the skill must emit it.
DISCOVERY_ARGV = 'argv=["gog", "calendar", "calendars", "--json", "--results-only"]'

MARKER = "/opt/data/ld/onboarding-complete"
WALL_MARKER = "/opt/data/ld/setup-complete"
GIF = "/srv/plow-assets/quick-q.gif"

# Hermes drops a model-emitted MEDIA: path under any of these without an error
# the owner or the agent can see (gateway/platforms/base.py's media denylist).
MEDIA_DENIED = ("/etc", "/proc", "/sys", "/dev", "/root", "/boot",
                "/var/log", "/var/lib", "/var/run")


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wc = load("write_config", "ld-setup/scripts/write_config.py")


def test_both_documents_name_the_same_completion_marker():
    """SOUL.md decides when to run onboarding; SKILL.md decides when it is done.

    They are separate files edited at separate times, and a marker that drifts
    between them has no failing surface: the skill writes one path, the soul
    checks another, and the owner is re-onboarded from the top on every single
    message they ever send.
    """
    assert MARKER in SOUL
    assert MARKER in SKILL


def test_the_marker_is_written_once_and_only_at_the_close():
    """One writer, and it is the last thing onboarding does.

    A marker written before the questions are asked ends the conversation while
    the config is still empty -- and because nothing re-checks, the owner never
    gets asked again.
    """
    writes = re.findall(rf"^\s*date -u \+%FT%TZ > {re.escape(MARKER)}\s*$",
                        SKILL, re.MULTILINE)
    assert len(writes) == 1, f"expected exactly one writer of {MARKER}, found {len(writes)}"
    close = SKILL.index("### 4 · Close")
    assert SKILL.index(f"> {MARKER}") > close, "the marker is written before the close"


def test_the_wall_marker_stays_the_walls_own():
    """Onboarding must not write, or be gated on, the wall's marker.

    They mean different things -- an owner with no Pi finishes onboarding and
    never gets a setup-complete -- and collapsing them either strands a
    wall-less owner mid-conversation forever or reports a blank wall as done.
    """
    onboarding = SKILL[SKILL.index("## Onboarding"):SKILL.index("## The wall (optional)")]
    assert f"> {WALL_MARKER}" not in onboarding
    writes = re.findall(rf"^\s*date -u \+%FT%TZ > {re.escape(WALL_MARKER)}\s*$",
                        SKILL, re.MULTILINE)
    assert len(writes) == 1


def test_the_opener_gif_is_baked_where_hermes_will_deliver_it():
    """The path in the skill, the path in the image, and one Hermes accepts.

    A GIF under the home is silently dropped, so this asserts the actual
    prefix rather than merely that the two files agree: they could agree on a
    denied path and the opener would still arrive with no picture.
    """
    assert f"MEDIA:{GIF}" in SKILL
    assert f"COPY docs/onboarding-v2/assets/quick-q.gif {GIF}" in DOCKERFILE
    assert (ROOT / "docs/onboarding-v2/assets/quick-q.gif").is_file()
    assert not any(GIF.startswith(f"{denied}/") for denied in MEDIA_DENIED)


def test_onboarding_never_asks_for_what_latch_supplies():
    """Email, calendar ids and the Mac username arrive through the Mac.

    Asking for them is not merely redundant -- it is a question the owner
    cannot answer usefully at that point, in the middle of the one conversation
    that decides whether they stay. Calendars are now DISCOVERED rather than
    absent, which is the same guarantee reached the other way round: the owner
    still never types one.
    """
    for asked in ("owner_email", "extra_calendar_ids", "mac_username"):
        assert asked not in ONBOARDING, f"onboarding asks for {asked}"
    assert "never types a calendar address" in ONBOARDING


def test_calendar_discovery_is_one_argv_and_never_auth_list():
    """Latch allows Gmail and Calendar subcommands and nothing else.

    `gog auth list` is refused under every binary name -- measured against a
    real Latch, not guessed -- so a discovery flow that starts by enumerating
    accounts dead-ends on a Mac that is working correctly, and the failure
    reads as "no calendars" rather than "wrong command".
    """
    # Twice, and byte-identical both times: once as the turn-top probe that
    # decides whether Latch is up, once in the section that reads the answer.
    # They are the same call on purpose -- the probe IS the listing, so a turn
    # that finds Latch connected already holds what it needs and does not go
    # back for it. Two spellings would be two calls, and the second would be
    # the one nobody checked against a real relay.
    assert ONBOARDING.count(DISCOVERY_ARGV) == 2, (
        "the probe and the discovery call must be the same argv, written once each")
    # No `auth` subcommand reaches an argv anywhere in this sheet. The section
    # names it in prose, deliberately, to say why it is not used -- so the
    # check is on the argv shape, not on the word.
    assert not re.search(r'argv=\[[^]]*"auth"', SKILL)


MECHANICS = SKILL[SKILL.index("## How a turn actually sends things"):SKILL.index("### 1 · Opener")]


def test_the_sheet_states_the_one_message_per_turn_limit():
    """A turn delivers exactly one message, and nothing can add a second.

    send_message is defined in the image but registered with no toolset, so it
    is not callable from a chat turn. Instructions written as "send two
    messages" produced, in order: one message with two paragraphs, then a
    message asking the OWNER whether to proceed without the missing tool. An
    instruction the build cannot satisfy is worse than the limitation.
    """
    text = " ".join(MECHANICS.split())
    assert "You cannot split a message on purpose, but you can leak one by accident" in text
    assert "there is no split marker" in text
    assert "send_message" in text, "the reason has to be recorded, or it gets re-attempted"
    # The real mechanism behind every leak: interim text is delivered.
    assert "Text you emit BETWEEN tool calls is delivered as its own message" in text
    assert "call your tools in silence, and speak once, at the end" in text
    # All three leaks that reached a real owner, quoted so the rule keeps its
    # evidence -- and behind NOT: so the scan can tell them from instructions.
    assert MECHANICS.count("    NOT: ") == 3


def test_the_owner_is_never_told_about_the_machinery():
    """Sent to a real owner: "this build doesn't have a working send_message
    tool ... want me to proceed with single combined messages?" -- as a
    numbered question. They are here to meet an assistant."""
    text = " ".join(MECHANICS.split())
    assert "Never tell the owner about your own machinery" in text
    assert "do the best available thing silently" in text


def test_media_tags_are_required_flush_left_and_never_fenced():
    """The gateway blanks fenced code blocks before scanning for MEDIA tags.

    Always, regardless of whether the path is real -- so a tag inside triple
    backticks is dropped with no attachment and no error, while the sentence
    introducing it still arrives. That is exactly how four screenshots went
    missing from a message that read correctly.
    """
    text = " ".join(MECHANICS.split())
    assert "plain text on its own line" in text
    assert "blanks out fenced code blocks first" in text
    assert "silently dropped" in text
    # And the sheet warns that its OWN examples are indented for the document.
    assert "indented HERE because this sheet is a document" in text


def test_the_photo_stack_is_four_tags_on_one_message():
    """Four attachments on one message, not four messages.

    Several MEDIA tags in one message deliver as several attachments in the
    order written, so the stack is one send_message call -- which is also what
    keeps them arriving together rather than trickling in.
    """
    intro = ONBOARDING[ONBOARDING.index("### 2 ·"):ONBOARDING.index("### 3 ·")]
    flat = " ".join(intro.split())
    assert "Four attachments on the one message, not four messages" in flat
    assert "never inside a code fence" in flat
    # All four tags, each flush left on its own line, in argument order.
    tags = [l for l in intro.splitlines() if l.strip().startswith("MEDIA:/srv/plow-assets/work-")]
    assert len(tags) == 4, f"expected four MEDIA lines, found {len(tags)}"
    assert [t.strip().rsplit("/", 1)[1] for t in tags] == [
        "work-1-vault-login.png", "work-2-instacart-grocery.png",
        "work-3-amazon-shopping.png", "work-4-medical-discovery.png"]


def test_bookkeeping_never_becomes_the_final_message():
    """Observed, delivered to the owner: "Written. Now waiting for Mary's
    reply before continuing to city/teams." The last thing written in a turn is
    the message, so a note-to-self left there is sent."""
    text = " ".join(MECHANICS.split())
    assert "call your tools in silence, and speak once, at the end" in text
    assert "If you catch yourself narrating a step as you take it, that sentence is already sent" in text


def test_nothing_follows_the_close():
    """Observed, its own message, after the wall offer: "Onboarding is
    complete. I'll stay quiet unless Mary follows up."

    Interim text around the marker write, delivered like every other leak of
    its kind. The owner was told they are set one message earlier; announcing
    that you will now be quiet is not being quiet.
    """
    close = " ".join(ONBOARDING[ONBOARDING.index("### 4 ·"):ONBOARDING.index("### 5 ·")].split())
    assert "the marker is written in silence, like every other tool call" in close
    assert "NOT: Onboarding is complete" in close
    assert "Saying you will now be quiet is not being quiet" in close


def test_the_geocode_read_back_stays_internal():
    """Observed in the owner's chat: "Good -- those coordinates match Mountain
    View, CA, so that's correct."

    They do not know a geocoder ran and cannot act on a lat/lon. The check is
    real and stays; narrating it is the leak.
    """
    city = " ".join(ONBOARDING[ONBOARDING.index("### 3 ·"):ONBOARDING.index("### 4 ·")].split())
    assert "That check is yours alone, and it happens between tool calls — so it happens in silence" in city
    assert "Not one word about coordinates, checking, verifying, matching or being correct" in city
    # Two counter-examples, both actually sent to an owner, both behind NOT:.
    assert city.count("NOT: Good —") == 2


def test_the_introduction_is_one_short_message():
    """One message is the only option, so brevity is the only lever."""
    intro = " ".join(ONBOARDING[ONBOARDING.index("### 2 ·"):ONBOARDING.index("### 3 ·")].split())
    assert "**One message, and short.**" in intro
    assert "the way a person texts, not three paragraphs of prose" in intro


def test_latch_state_is_probed_every_turn_not_asked():
    """"Have you installed it yet?" is a question with an answer already on file.

    Asking it puts the owner in the position of reporting on homework, and
    worse, it is the ONLY thing standing between them and the calendar pick --
    so an owner who installs Latch and says nothing waits forever for an
    assistant that could have looked.
    """
    text = " ".join(ONBOARDING.split())
    assert "you find out where Latch stands by looking, not by asking" in text
    assert 'Never ask "have you installed it yet?"' in text
    # The probe is the same call the discovery section makes.
    assert DISCOVERY_ARGV in ONBOARDING[:ONBOARDING.index("### 1 · Opener")]


@pytest.mark.parametrize("outcome", [
    "no such tool",       # no plow server at all
    "a relay error",      # Latch not running
    "a calendar listing",  # up
])
def test_every_probe_outcome_has_a_stated_behaviour(outcome):
    """Three outcomes, three behaviours, none of them left to inference.

    The absent-tool case is the one that gets forgotten, and it is the one that
    matters most: a deployment with no Mac at all must look exactly like a Mac
    that has not been set up yet, or every owner without one gets an error
    report about a feature they were never offered.
    """
    table = " ".join(ONBOARDING[:ONBOARDING.index("### 1 · Opener")].split())
    assert outcome in table


def test_a_failed_probe_never_reaches_the_owner():
    """A 503 and a stack trace are the same sentence to a person who did not
    ask for either, and the sentence is not about them."""
    text = " ".join(ONBOARDING.split())
    assert "never say a word about it" in text
    assert "never put the failure itself in front of them" in text.replace("Nor do you ever put", "never put")


def test_the_link_is_nudged_at_most_once_more():
    """Between silence and a reminder every turn, the failure mode is the
    reminder: the owner has the link, and repeating it is the assistant
    nagging about the one thing it cannot do itself."""
    text = " ".join(ONBOARDING.split())
    assert "mention it again at most once more" in text
    assert "Never every turn" in text
    assert "If you cannot tell whether you have already nudged, you have" in text


def test_a_connected_latch_suppresses_the_download_paragraph():
    """Sending an install link to someone who has installed it reads as an
    assistant that has not noticed them -- and it is the first impression."""
    intro = " ".join(ONBOARDING[ONBOARDING.index("### 2 ·"):ONBOARDING.index("### 3 ·")].split())
    assert "only if the probe said Latch is not up" in intro
    assert "If the probe returned a listing, none of that paragraph is sent" in intro
    assert "not the link" in intro
    assert "go straight to §5 in this same turn" in intro


def test_the_calendar_pick_no_longer_waits_to_be_told():
    """The probe is the trigger, so an owner who installs Latch and says
    nothing still gets asked which calendars to track."""
    text = " ".join(ONBOARDING.split())
    assert "the trigger already fired" in text
    section = " ".join(ONBOARDING[ONBOARDING.index("### 5 ·"):].split())
    assert "you do not wait to be told, and you do not ask" in section


def test_the_account_is_taken_from_primary_not_dataowner():
    """dataOwner varies across the list; calendar.account is one identity.

    The real listing carried three distinct dataOwner values across nine
    calendars -- shares keep their own owner -- so deriving the account from it
    picks whichever calendar happened to be read last.
    """
    section = ONBOARDING[ONBOARDING.index("### 5 ·"):]
    assert "`primary` is true" in section
    assert "dataOwner" in section, "the trap has to be named to be avoided"
    assert "Take it from `primary`" in section


def test_the_listing_is_parsed_past_its_preamble():
    """gog prints a note line before the JSON array, so the output is not JSON.

    A consumer that json.loads() the whole string fails on a working call, and
    the flow reports the calendar as unavailable when it is right there.
    """
    section = ONBOARDING[ONBOARDING.index("### 5 ·"):]
    assert "skip to the first `[`" in section


def test_calendar_names_are_named_as_untrusted():
    """They come off someone else's calendar and are shown to a model."""
    section = ONBOARDING[ONBOARDING.index("### 5 ·"):]
    assert "untrusted data" in section


def test_the_nudge_lookaheads_are_written_with_the_calendars():
    """Without them a config with calendars still fails the gate.

    Nothing asks the owner for a lookahead, the gate requires both to be
    positive numbers, and no other step writes them -- so a run that discovered
    calendars and stopped there leaves a config that looks complete in chat and
    can never start the wall.
    """
    section = ONBOARDING[ONBOARDING.index("### 5 ·"):]
    assert '"lookahead_virtual_minutes": 30' in section
    assert '"lookahead_in_person_minutes": 60' in section
    example = json.loads((ROOT / "ld-shared/references/config.example.json").read_text())
    assert example["calendar_nudge"]["lookahead_virtual_minutes"] == 30
    assert example["calendar_nudge"]["lookahead_in_person_minutes"] == 60


def test_the_wall_routes_a_missing_calendar_to_discovery():
    """The wall used to stop dead on calendar keys, which was right when
    nothing could fill them and is wrong now that discovery exists."""
    wall = SKILL[SKILL.index("## The wall (optional)"):]
    assert "run §5" in wall
    assert "asking them to type an email" not in wall


def test_a_discovered_calendar_makes_the_config_installable(tmp_path):
    """The point of the whole chunk, end to end.

    Onboarding's own answers can never pass the shared gate. These writes are
    what turn that draft into a config the producers will run on -- and if any
    one of account, sources, owner_identities or the two lookaheads is left
    out, it stays refused.
    """
    config = tmp_path / "config.json"
    env = {"TZ": "America/Los_Angeles"}
    for answer in ('{"family": {"owner": {"name": "Mary"}}}',
                   '{"family": {"timezone": "America/Los_Angeles"}}',
                   '{"weather": {"location": "Mountain View, California", "lat": 37.4, "lon": -122.1}}',
                   '{"sports": {"followed": []}}'):
        wc.main(["--draft"], env=env, config_path=str(config), stdin=io.StringIO(answer))
    gate = load("ld_config_gate", "ld-shared/scripts/ld_config_gate.py").gate
    assert gate(json.loads(config.read_text())), "should still be short of installed"

    account = "mary@example.test"
    wc.main(["--draft"], env=env, config_path=str(config), stdin=io.StringIO(json.dumps({
        "calendar": {"account": account,
                     "sources": [{"calendar_id": account, "name": "Personal"},
                                 {"calendar_id": "fam@group.calendar.google.test", "name": "Family"}]},
        "calendar_nudge": {"owner_identities": [account],
                           "lookahead_virtual_minutes": 30,
                           "lookahead_in_person_minutes": 60}})))
    written = json.loads(config.read_text())
    assert gate(written) == "", f"still refused: {gate(written)}"
    assert [s["calendar_id"] for s in written["calendar"]["sources"]] == \
        [account, "fam@group.calendar.google.test"]
    assert written["family"]["owner"]["name"] == "Mary", "discovery clobbered an earlier answer"


def test_the_lookaheads_alone_do_not_make_it_installable(tmp_path):
    """Guards the inverse: writing the defaults is necessary, not sufficient."""
    config = tmp_path / "config.json"
    env = {"TZ": "America/Los_Angeles"}
    wc.main(["--draft"], env=env, config_path=str(config), stdin=io.StringIO(json.dumps({
        "family": {"owner": {"name": "Mary"}, "timezone": "America/Los_Angeles"},
        "calendar_nudge": {"lookahead_virtual_minutes": 30,
                           "lookahead_in_person_minutes": 60}})))
    gate = load("ld_config_gate", "ld-shared/scripts/ld_config_gate.py").gate
    assert "calendar.account is blank" in gate(json.loads(config.read_text()))


def test_a_draft_records_an_answer_the_gate_would_refuse(tmp_path):
    """The whole reason --draft exists.

    The shared gate wants a calendar account and its sources; onboarding never
    asks for either. Under --patch every answer the owner gave would be refused
    for something they have not been asked yet, so the name and the city would
    reach nothing.
    """
    config = tmp_path / "config.json"
    env = {"TZ": "America/Los_Angeles"}
    # Everything onboarding actually collects: the name, the zone, the city and
    # the teams. No calendar, because nobody was asked for one.
    for answer in ('{"family": {"owner": {"name": "Mary"}}}',
                   '{"family": {"timezone": "America/Los_Angeles"}}',
                   '{"weather": {"location": "Mountain View", "lat": 37.4, "lon": -122.1}}',
                   '{"sports": {"followed": []}}'):
        wc.main(["--draft"], env=env, config_path=str(config), stdin=io.StringIO(answer))
    written = json.loads(config.read_text())
    assert written["family"]["owner"]["name"] == "Mary"
    assert written["weather"]["location"] == "Mountain View"

    gate = load("ld_config_gate", "ld-shared/scripts/ld_config_gate.py").gate
    verdict = gate(written)
    assert "calendar.account is blank" in verdict, (
        "the gate should still refuse a config with no calendar -- if it stops "
        "doing so, --draft has no reason to exist")

    # The same answer through --patch is refused outright, and that refusal is
    # about the calendar nobody asked for, not about anything Mary said.
    with pytest.raises(SystemExit) as refusal:
        wc.main(["--patch"], env=env, config_path=str(config),
                stdin=io.StringIO('{"family": {"owner": {"name": "Mary"}}}'))
    assert "the gate says" in str(refusal.value)
    assert "calendar.account is blank" in str(refusal.value)


def test_a_draft_starts_from_nothing_but_a_patch_does_not(tmp_path):
    """The first answer arrives before any config exists.

    --patch must keep refusing that case -- it is how a mistyped path or a lost
    config announces itself instead of silently starting a new one.
    """
    config = tmp_path / "config.json"
    env = {"TZ": "America/Los_Angeles"}
    wc.main(["--draft"], env=env, config_path=str(config),
            stdin=io.StringIO('{"family": {"owner": {"name": "Mary"}}}'))
    assert config.is_file()

    with pytest.raises(SystemExit) as refusal:
        wc.main(["--patch"], env=env, config_path=str(tmp_path / "absent.json"),
                stdin=io.StringIO('{"family": {"owner": {"name": "Mary"}}}'))
    assert "could not read" in str(refusal.value)


def test_a_draft_still_refuses_a_key_the_template_does_not_have(tmp_path):
    """The relaxation is the gate, and only the gate.

    A model composes these from a sentence, and a misspelling merges in beside
    the real key: the answer reports success and the value never changes.
    """
    config = tmp_path / "config.json"
    with pytest.raises(SystemExit) as refusal:
        wc.main(["--draft"], env={"TZ": "America/Los_Angeles"}, config_path=str(config),
                stdin=io.StringIO('{"wether": {"location": "Denver"}}'))
    assert "unknown config key" in str(refusal.value)
    assert not config.exists()


def test_a_draft_refuses_a_timezone_the_container_does_not_share(tmp_path):
    """Early is not an excuse. A zone the container does not run in puts every
    card at the wrong local hour, and the fix (AGENT_TZ on the host) is the
    operator's -- so the owner has to hear it now, not after four more answers.
    """
    config = tmp_path / "config.json"
    with pytest.raises(SystemExit) as refusal:
        wc.main(["--draft"], env={"TZ": "America/Los_Angeles"}, config_path=str(config),
                stdin=io.StringIO('{"family": {"timezone": "America/New_York"}}'))
    assert "AGENT_TZ" in str(refusal.value)


def test_a_draft_without_a_timezone_yet_is_not_a_disagreement(tmp_path):
    """The name lands before the city does, and a config with no zone at all
    has nothing to disagree with -- refusing there would make the first answer
    of the conversation impossible to record."""
    config = tmp_path / "config.json"
    wc.main(["--draft"], env={"TZ": "America/Los_Angeles"}, config_path=str(config),
            stdin=io.StringIO('{"family": {"owner": {"name": "Mary"}}}'))
    assert "timezone" not in json.loads(config.read_text())["family"]


@pytest.mark.parametrize("payload,complaint", [
    # A name the owner never really gave. Written, it reads on the next turn as
    # a question already answered, and they are never asked again.
    ('{"family": {"owner": {"name": "   "}}}', "family.owner.name is blank"),
    ('{"family": {"owner": {"name": "[OWNER_NAME]"}}}', "placeholder"),
    ('{"family": {"owner": {"name": 5}}}', "not valid JSON"),
    # Calendar values ARE supplied later, by discovery -- the exemption covers
    # them only while they are absent.
    ('{"calendar": {"account": "a@b.test", "sources": [{"calendar_id": "", "name": "A"}]}}',
     "calendar.sources[].calendar_id is blank"),
    ('{"calendar": {"account": "a@b.test", "sources": ['
     '{"calendar_id": "x@y.test", "name": "A"}, {"calendar_id": "x@y.test", "name": "B"}]}}',
     "not unique"),
    ('{"calendar_nudge": {"owner_identities": []}}', "non-empty list"),
    ('{"calendar_nudge": {"lookahead_virtual_minutes": -5}}', "positive number"),
])
def test_a_draft_refuses_a_value_that_was_actually_supplied(tmp_path, payload, complaint):
    """The exemption is for questions not yet asked, and nothing else.

    A draft is the record of progress, so a bad value written here is worse
    than a refusal: the next turn reads it as answered and moves on, and the
    owner never gets asked again. Every gate check that judges something
    PRESENT is enforced exactly as --patch enforces it.
    """
    config = tmp_path / "config.json"
    with pytest.raises(SystemExit) as refusal:
        wc.main(["--draft"], env={"TZ": "America/Los_Angeles"}, config_path=str(config),
                stdin=io.StringIO(payload))
    assert "refusing to draft" in str(refusal.value)
    assert complaint in str(refusal.value)
    assert not config.exists(), "a refused draft must leave nothing behind"


def test_the_stand_ins_only_fill_what_is_absent():
    """fill_unasked() is the whole boundary between the two behaviours.

    If it ever overwrote a key that was present, a supplied blank name would be
    replaced by a valid stand-in and sail through -- which is exactly the bug
    the fill exists to avoid.
    """
    supplied = {"family": {"owner": {"name": "   "}},
                "calendar_nudge": {"lookahead_virtual_minutes": -5}}
    filled = wc.fill_unasked(supplied)
    assert filled["family"]["owner"]["name"] == "   "
    assert filled["calendar_nudge"]["lookahead_virtual_minutes"] == -5
    # ... while the untouched neighbours DO get stood in for.
    assert filled["calendar"]["account"]
    assert filled["calendar_nudge"]["lookahead_in_person_minutes"] > 0
    assert supplied == {"family": {"owner": {"name": "   "}},
                        "calendar_nudge": {"lookahead_virtual_minutes": -5}}, "fill mutated its input"


def test_no_script_output_is_pasted_to_the_owner_during_onboarding():
    """Onboarding is a conversation; the wall install is a walkthrough.

    The paste-everything rule exists for the wall, where the owner is watching
    an install. Applying it to onboarding puts a gate verdict full of calendar
    keys in front of someone who has just said their name -- and that verdict
    is EXPECTED noise there, so it reads as a fault when nothing is wrong.
    """
    onboarding = SKILL[SKILL.index("## Onboarding"):SKILL.index("## The wall (optional)")]
    assert "This output is yours, not the owner's" in onboarding
    assert "Paste its output verbatim" not in onboarding
    # The wall keeps the rule.
    wall = SKILL[SKILL.index("## The wall (optional)"):]
    assert "pasted verbatim" in SKILL[:SKILL.index("## Onboarding")] or "Paste" in wall


def test_the_opener_carries_none_of_the_introduction():
    """Spec 2.1 and 2.2: the opener gives a name, 2 gives the pitch.

    An earlier draft told the opener to "say who you are", one section above
    the one that introduces you -- read either way and both were defensible,
    which is exactly the shape that produces a capability blurb before anyone
    has said hello.
    """
    opener = SKILL[SKILL.index("### 1 · Opener"):SKILL.index("### 2 ·")]
    assert "Give your name, and only your name" in opener
    assert "The introduction is §2, not §1" in opener
    # None of 2's material may leak forward into it.
    for pitched in ("dog food", "refund", "vault", "plow.co/latch", "boundaries"):
        assert pitched not in opener, f"the opener pitches {pitched!r}; that is §2"


def test_a_message_the_owner_must_see_is_sent_before_its_answer_is_written():
    """Config is the resume record, so it must never claim more than was shown.

    The name is what makes a resumed turn skip the introduction. Written before
    that introduction is sent, a restart in the gap leaves a config saying the
    question was answered while the owner never saw the pitch OR the Latch
    link -- and nothing later in the conversation would ever notice, because
    every subsequent turn reads the same config and skips the same section.
    """
    assert "the message goes out before the answer goes in" in ONBOARDING.lower()

    intro = ONBOARDING[ONBOARDING.index("### 2 ·"):ONBOARDING.index("### 3 ·")]
    assert "Introduce yourself first, then draft the name" in intro
    # And the draft instruction physically follows the link in the section.
    assert intro.index("https://plow.co/latch") < intro.index("**Now** draft their name"), \
        "the name is drafted before the link is sent"

    teams = ONBOARDING[ONBOARDING.index("### 3 ·"):ONBOARDING.index("### 4 ·")]
    assert "Send §4's close before drafting either" in teams

    close = ONBOARDING[ONBOARDING.index("### 4 ·"):ONBOARDING.index("### 5 ·")]
    assert "goes out BEFORE the teams draft" in close
    # The marker was already ordered this way; keep it that way.
    assert close.index("Tell them they are set") < close.index("> /opt/data/ld/onboarding-complete")


PRIVACY_LINE = ("The app on your Mac is where your accounts live: your logins stay in a\n"
                "      vault there that I can use but never see, and you set the boundaries I\n"
                "      work inside.")


def test_onboarding_is_gated_to_a_solo_dm_with_the_owner():
    """Both dimensions, because either alone leaves the hole open.

    Owner-only without solo-DM still runs the interview in a group the owner is
    in -- their name, their city and their teams collected in front of an
    audience. Solo-DM without owner-only still lets a member DM the agent and
    have their answers written into the owner's config. And "every inbound
    message", which is what this said before, is both at once.
    """
    # Whitespace-normalised: this is prose wrapped at 79 columns, so every
    # phrase worth pinning is one reflow away from spanning a line break.
    trigger = " ".join(SOUL[SOUL.index("# First run"):SOUL.index("# The wall")].split())
    assert "every inbound message is part of that first conversation" not in trigger, \
        "the trigger is unconditional again"
    assert "solo one-to-one DM with the owner" in trigger
    # The three facts the platform reports, each named.
    assert "role is **owner**" in trigger
    assert "type is a **DM**" in trigger
    assert "roster is just the two of you" in trigger
    # And the negative case says what NOT to do, including the writes.
    assert "onboarding does not exist" in trigger
    assert "no `--draft`, no config, no marker" in trigger


@pytest.mark.parametrize("where,text", [
    # The frontmatter is what the skill scanner reads to decide whether to load
    # this sheet at all, so it is an entry instruction in its own right.
    ("the frontmatter", " ".join(SKILL.split("---", 2)[1].split())),
    # And the section's own opening lines are what the model reads once it is
    # loaded. Three statements of one rule that can disagree is worse than one:
    # a turn that finds the loosest of them has its permission.
    ("the onboarding section's entry condition",
     " ".join(SKILL[SKILL.index("## Onboarding"):SKILL.index("### 1 · Opener")].split())),
])
def test_every_entry_point_states_the_same_three_part_gate(where, text):
    """SOUL.md, the frontmatter and the section all gate the same way.

    They were three different instructions: SOUL.md required a solo owner DM,
    the frontmatter said "any inbound message", and the section said "the first
    inbound message from an owner" with nothing about where. A model reconciles
    competing permissions by taking the widest, so the strictest one was the
    one that did not count.
    """
    assert "any inbound message" not in text, f"{where} still admits any inbound"
    assert "owner" in text and "DM" in text, f"{where} does not name the gate"
    assert "roster is just the two of you" in text, f"{where} omits the solo condition"


def test_the_wall_token_handoff_is_dm_only():
    """The one place a bearer token is allowed to cross chat.

    In a group every participant keeps it in their own history forever, and
    there is nothing to rotate short of re-minting and re-shipping the Pi.
    """
    wall = SKILL[SKILL.index("**No Mac (or no Latch):**"):]
    handoff = " ".join(wall[:wall.index("date -u +%FT%TZ > /opt/data/ld/pi-brought-up")].split())
    assert "Only in a solo DM with the owner, and never in a group" in handoff
    assert handoff.index("Only in a solo DM") < handoff.index("text the owner, verbatim"), \
        "the gate has to come before the instruction it gates"


def test_the_privacy_line_is_verbatim_and_does_not_claim_local_execution():
    """The one sentence in this conversation the model may not rephrase.

    The agent runs in a cloud VM; Latch is what is on the Mac, and the vault is
    Latch's. The earlier wording invited "I run on your machine, not someone
    else's server", and that is what came out in testing -- a false claim about
    where someone's credentials live, made at the moment they are deciding
    whether to trust it.
    """
    intro = ONBOARDING[ONBOARDING.index("### 2 ·"):ONBOARDING.index("### 3 ·")]
    assert PRIVACY_LINE in intro
    assert "not** in your own words" in intro
    # The counter-example is quoted in the sheet on purpose, on its own line
    # behind a NOT: marker, so the scan can tell "never say this" from "say
    # this" without matching the warning that exists to prevent it.
    said = "\n".join(line for line in intro.splitlines() if "NOT:" not in line)
    for false_claim in ("run on your own machine", "run on their machine",
                        "nothing ever leaves", "not someone else's server"):
        assert false_claim not in said, f"the intro still invites {false_claim!r}"
    assert "NOT: I run on your own machine" in intro, "the counter-example is what makes it stick"


def test_the_photo_stack_is_baked_and_sent_in_order():
    """Four screenshots, in the order that makes the argument.

    Vault login first because it is the privacy line made concrete, then the
    ordinary errands, then the medical one -- small trust to larger. A stack
    that ships in a different order than it is baked in is a silent 404 or a
    picture in the wrong place, neither of which raises anything.
    """
    intro = ONBOARDING[ONBOARDING.index("### 2 ·"):ONBOARDING.index("### 3 ·")]
    assert "Want to see the kind of thing I mean?" in intro
    names = ["work-1-vault-login.png", "work-2-instacart-grocery.png",
             "work-3-amazon-shopping.png", "work-4-medical-discovery.png"]
    positions = []
    for name in names:
        assert f"MEDIA:/srv/plow-assets/{name}" in intro, f"{name} is not sent"
        assert f"COPY docs/onboarding-v2/assets/{name} /srv/plow-assets/{name}" in DOCKERFILE
        assert (ROOT / "docs/onboarding-v2/assets" / name).is_file()
        positions.append(intro.index(name))
    assert positions == sorted(positions), "the stack is sent out of order"
    # After the privacy line, before the Latch link -- the slot the spec names.
    assert intro.index(PRIVACY_LINE) < positions[0] < intro.index("https://plow.co/latch")
    # And it does not become a checkpoint the conversation waits on.
    assert "a question you do not wait for an answer to" in intro


def test_a_nameless_agent_still_opens_the_conversation():
    """Observed: with no name configured, the agent asked the OWNER to name it
    -- as a numbered menu, as the first thing it ever said -- and then took
    "Mary" (the owner's name) as its own.

    Nothing in this repo gives the agent a name, so "introduce yourself by
    name" without an escape hatch is a dead end, and the model resolves a dead
    end by improvising at the owner's expense.
    """
    opener = SKILL[SKILL.index("### 1 · Opener"):SKILL.index("### 2 ·")]
    assert "if you have one" in opener
    assert "say hello without one and carry on" in opener
    assert "do not ask the owner to name you" in opener
    assert "numbered multiple-choice question" in opener


def test_the_owners_name_may_only_come_from_their_reply():
    """Observed: family.owner.name was written as "You".

    The plugin prepends a roster preamble to every turn, and "You" is what it
    calls the human. Taken as an answer it is unrecoverable -- from the next
    turn on the question reads as answered, so nothing ever asks again.
    """
    intro = ONBOARDING[ONBOARDING.index("### 2 ·"):ONBOARDING.index("### 3 ·")]
    assert "comes from their reply and from nothing else" in intro
    assert "roster preamble" in intro
    assert "`You`" in intro


def test_the_opener_is_a_hello_a_gif_and_a_name():
    """Spec 2.1, and the two transcripts that failed it.

    The model opened with a capability blurb and a /help menu, and put the
    question in the same message as the GIF so the picture landed after it.
    Both are pinned here because both read as a form, which is the one thing
    this rewrite exists to stop being.
    """
    opener = " ".join(SKILL[SKILL.index("### 1 · Opener"):SKILL.index("### 2 ·")].split())
    assert "`/help`" in opener and "no capability blurb" in opener.lower()
    # One message carrying all three, in a stated order. The picture lands
    # under the question because attachments follow their message's text;
    # that is the platform, and the sheet says so rather than wishing.
    assert "One message, and it holds three things in this order" in opener
    assert "The picture lands under the question rather than above it" in opener


def test_the_latch_url_is_sent_bare_on_its_own_line():
    """The phone renders a preview for a bare URL and not for a linked one.

    It is also the single action the whole introduction exists to produce, so
    an intro that describes the install without carrying the link leaves the
    owner with nothing to do.
    """
    intro = " ".join(ONBOARDING[ONBOARDING.index("### 2 ·"):ONBOARDING.index("### 3 ·")].split())
    assert "https://plow.co/latch" in intro
    assert "**bare, on its own line**" in intro


def test_no_mode_of_write_config_touches_the_crons():
    """The script writes one file. Its docstring once said otherwise.

    The claim that --patch re-registered the crons survived three readings of
    this file, in the module docstring, a merged PR description and a review
    note, while no version of the code had ever done it. Prose about behaviour
    is only as good as something that fails when it stops being true.
    """
    source = (ROOT / "ld-setup/scripts/write_config.py").read_text()
    assert "subprocess" not in source
    assert "register_crons" not in source.split('"""', 2)[2], \
        "the body names register_crons -- either it runs it now, or the name is stale"
    assert "NO mode touches the crons" in source
    # And the sheet the agent actually follows says the same thing.
    assert "It does **not** touch the crons" in SKILL


def test_draft_and_patch_are_not_both_accepted():
    """Two merge modes with different verdicts; silently preferring one would
    make the strict one unreachable from a caller that thought it asked."""
    with pytest.raises(SystemExit) as refusal:
        wc.main(["--patch", "--draft"], env={"TZ": "UTC"},
                stdin=io.StringIO("{}"), config_path="/nonexistent/config.json")
    assert "not both" in str(refusal.value)
