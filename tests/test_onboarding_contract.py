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

THE RULE FOR ANYTHING ADDED HERE: no wording assertions -- only executable
behaviour and structural contracts (a table's rows and cells, a branch's
boundary, a script's output, a path that must exist).
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
# The tool as the image REGISTERS it. An MCP tool carries its server's key as a
# prefix, so a sheet naming the bare suffix names a tool that is not there: one
# calendar turn spent twenty-one API calls on tool_search and answered the owner
# with nothing.
RELAY_TOOL = "mcp__latch__plow_run_command"


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

def test_every_asset_the_sheet_sends_is_one_the_image_holds():
    """A MEDIA: path the image does not carry is delivered as nothing at all --
    no attachment, no error, and the sentence introducing it still arrives.

    The directory is baked as a unit, so the contract worth testing is
    referential: every file the sheet names must exist to be copied. Pinning the
    directory's exact listing tested the filesystem instead, and broke whenever
    the design changed while catching nothing a run would not.
    """
    assets = ROOT / "docs/onboarding-v2/assets"
    baked = [l for l in DOCKERFILE.splitlines()
             if l.startswith("COPY") and "/srv/plow-assets/" in l]
    assert baked == ["COPY docs/onboarding-v2/assets/ /srv/plow-assets/"], (
        "the directory is copied as one unit; a per-file list drifts from it")
    referenced = re.findall(r"^\s*MEDIA:/srv/plow-assets/(\S+)$", SKILL, re.MULTILINE)
    assert referenced, "the sheet sends no assets at all"
    for name in referenced:
        assert (assets / name).is_file(), f"the sheet sends {name}, which is not in the image"


def test_the_lead_in_and_the_pictures_travel_together():
    """The question and the images are one thing: asked with nothing behind it
    the question is worse than not asking, and sent without it the pictures
    arrive unexplained."""
    live, in_comment = [], False
    for line in ONBOARDING.splitlines():
        if "<!--" in line:
            in_comment = True
        if not in_comment:
            live.append(line)
        if "-->" in line:
            in_comment = False
    live = "\n".join(live)
    tags = [l for l in live.splitlines() if l.strip().startswith("MEDIA:/srv/plow-assets/work-")]
    assert [t.strip().rsplit("/", 1)[1] for t in tags] == [
        "work-1-vault-login.png", "work-2-instacart-grocery.png",
        "work-3-amazon-shopping.png", "work-4-medical-discovery.png"]


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


def test_silence_names_the_token_the_gateway_recognises():
    """"Stay silent" is not actionable: a turn ends on SOMETHING, and anything
    that is not the exact marker is delivered.

    Observed in a group: "(no response — this message isn't directed at me and
    doesn't need my input)". The gateway suppresses delivery only for a reply
    whose whole content canonicalises to NO_REPLY or [SILENT]
    (gateway/response_filters.py, LIVE_GATEWAY_SILENT_MARKERS); prose that
    merely mentions the marker is explicitly not silence.
    """
    soul = " ".join(SOUL.split())
    assert "Say `NO_REPLY` and nothing else" in soul


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



# --------------------------------------------------------------------------
# The algorithm: one procedure, stated once, that every turn runs
# --------------------------------------------------------------------------

ALGORITHM = ONBOARDING[ONBOARDING.index("## The algorithm"):ONBOARDING.index("### 1 ·")]
STEPS = ["**1 · Read the config.**", "**2 · Run the Latch status probe.**",
         "**3 · Take what this message gave you.**", "**4 · Write everything you hold",
         "**5 · Compose the one message**"]


def test_the_algorithm_states_its_five_steps_exactly_once():
    """One procedure, in one place, or it is an enumeration again.

    Every list of per-turn shapes this sheet has carried grew a hole -- a step
    with no row, a resume between two rows, an exit the row did not describe --
    and each hole reached an owner as a blocking `❓`. A second statement of a
    step is the same failure in slow motion: two copies drift, and the turn
    follows whichever it read first.
    """
    for step in STEPS:
        assert ONBOARDING.count(step) == 1, f"{step} appears {ONBOARDING.count(step)} times"
        assert step in ALGORITHM, f"{step} is stated outside the algorithm"
    # In order, and all of them before the copy sections that reference them.
    positions = [ALGORITHM.index(step) for step in STEPS]
    assert positions == sorted(positions), "the steps are out of order"


def test_no_table_of_turn_shapes_survives():
    """The disease itself. A per-turn table cannot help contradicting the rule
    it sits next to: rows said "draft the teams" where the rule said "write
    everything held", and the §2 row's Latch-connected exit disagreed with the
    state table sixty lines below it."""
    assert "| the turn | tool calls |" not in ONBOARDING
    assert "| what the config already holds |" not in ONBOARDING
    # The examples that replace it are marked as examples, and say which wins.
    assert "Examples, not authorities" in ALGORITHM, (
        "the list that replaces the table must be marked as non-authoritative")


def test_the_keys_are_asked_in_one_order_everywhere():
    """"Ask the first missing key" is only well defined if the order is fixed,
    and the order is the config's own. Measured when it was not: a resume with
    the city stored was asked for the city again, because the copy named it as
    "the next question" and the rule sat below the copy."""
    order = ["family.owner.name", "weather.location", "sports.followed", "calendar.sources"]
    step1 = ALGORITHM[ALGORITHM.index(STEPS[0]):ALGORITHM.index(STEPS[1])]
    positions = [step1.index(f"`{key}`") for key in order]
    assert positions == sorted(positions), "step 1 lists the keys out of order"
    step5 = " ".join(ALGORITHM[ALGORITHM.index(STEPS[4]):].split())
    assert "name → city → teams → calendars" in step5
    # SOUL.md's trigger and the frontmatter must agree with that same set.
    for key in order:
        assert f"`{key}`" in TRIGGER


def test_no_owner_text_is_ever_composed_into_a_command():
    """Their name, their city, and above all a calendar's display name -- text a
    STRANGER wrote -- would otherwise be a command built out of someone else's
    input. Every script that takes owner answers reads a file the turn staged.
    """
    assert "<<" not in SKILL, "a heredoc is back in the sheet"
    for line in SKILL.splitlines():
        # Command examples only: indented code lines, not prose or a table cell
        # that merely names a script.
        if not line.startswith("    ") or line.strip().startswith("|"):
            continue
        if "write_config.py" in line or "mint_wall_token.py" in line:
            assert "--input" in line, f"not staged: {line.strip()}"



def test_write_config_takes_the_staged_path(tmp_path):
    """The seam itself, exercised: --input is what the sheet tells the turn to
    use, so it has to read a file and refuse a missing one by name."""
    staged = tmp_path / "draft.json"
    staged.write_text('{"family": {"owner": {"name": "Mary"}}}')
    config = tmp_path / "config.json"
    wc.main(["--draft", "--input", str(staged)], env=ENV, config_path=str(config))
    assert json.loads(config.read_text())["family"]["owner"]["name"] == "Mary"
    with pytest.raises(SystemExit) as refusal:
        wc.main(["--draft", "--input", str(tmp_path / "absent.json")],
                env=ENV, config_path=str(config))
    assert "could not read" in str(refusal.value)


def test_the_identities_are_the_union_not_the_account_alone():
    """calendar.account is the one identity gog authenticates as; the nudge asks
    whether the OWNER was in a meeting. An owner whose calendars carry two of
    their addresses is absent from every event read through the other one --
    a nudge that works and never fires."""
    # Structural: every identities template carries more than the account, so
    # the union cannot silently collapse back to one address.
    section = ONBOARDING[ONBOARDING.index("### 5 ·"):]
    templates = re.findall(r'"owner_identities": \[(.*?)\]', section)
    assert templates, "no owner_identities template in the sheet"
    for template in templates:
        assert template.count(",") >= 1, f"identities written as one address: {template}"
        assert "candidate" in template


def test_the_listing_file_is_written_only_where_a_listing_exists():
    """A write attempted before there is anything to write does not fail
    quietly: a failed write_file is reported in a footer appended to the turn's
    FINAL RESPONSE, and that response is a message to the owner."""
    # Structural: the path is named in §5 and nowhere before it, so no earlier
    # turn has anything to write it from -- and it is per-turn, like the drafts,
    # because two turns sharing one staging name is one file each overwrites.
    assert "calendar-listing-<turn>.json" in ONBOARDING[ONBOARDING.index("### 5 ·"):]
    assert "calendar-listing" not in ONBOARDING[:ONBOARDING.index("### 5 ·")]
    for staged in re.findall(r"/opt/data/ld/\.?[a-z-]*(?:draft|wall|listing)[a-z-]*\.json",
                             SKILL):
        assert "<turn>" in staged, f"a staging path is shared between turns: {staged}"


def test_the_connected_branch_turns_on_the_listing_not_on_the_probe():
    """`configured` says a relay is registered. It does not say a Mac answered.

    Read as the same thing, an owner whose Mac is asleep is handed the branch for
    an owner whose Mac is up: no install link, and a calendar question about
    calendars nobody could read. The probe decides whether to ATTEMPT the call;
    only a listing that came back decides what the message says.
    """
    algorithm = " ".join(
        ONBOARDING[ONBOARDING.index("**2 · Run the Latch status probe"):
                   ONBOARDING.index("### 1 ·")].split())
    assert "permission to attempt the listing call, and nothing more" in algorithm
    assert "CONNECTED is a listing that came back" in algorithm

    # Nowhere may `configured` alone gate the connected copy.
    for marker in ("the pitch-and-link\n  paragraph is the only part dropped",
                   "the catch and the link are omitted"):
        window = ONBOARDING[max(0, ONBOARDING.index(marker) - 260):ONBOARDING.index(marker) + 60]
        assert "listing" in window.lower() and "CAME BACK" in window.upper(), (
            f"the connected branch at {marker!r} is gated on the probe, not the listing")


def test_step_four_names_exactly_one_deferral():
    """Structural, because the count is the contract.

    "Defer whenever the turn ends on a question" was the over-broad version and
    it made every turn's write conditional on wording. There is one exception --
    the turn that learns the name and sends the one-time introduction -- and a
    second one appearing here is the enumeration growing back.
    """
    step4 = " ".join(ALGORITHM[ALGORITHM.index(STEPS[3]):ALGORITHM.index(STEPS[4])].split())
    assert step4.count("deferral") == 2, "one exception, stated and then lapsed"


def test_the_listing_reaches_disk_through_the_file_tool_alone():
    """Anything that runs code to place the file needs approval, and the
    approval card is delivered into the owner's chat -- script body, reason and
    all. Measured: a turn base64'd the listing through `execute_code` and the
    owner got `⚠️ Dangerous command requires approval:` while connecting their
    calendar. The file tool takes the content directly and asks nobody.
    """
    section = " ".join(ONBOARDING[ONBOARDING.index("### 5 ·"):].split())
    assert "ONLY the file tool" in section
    assert "execute_code" in section, "the alternative the turn actually reached for"


def test_every_calendar_is_shown_including_the_odd_ones():
    """A calendar the owner can see on their Mac and not in your message is a
    list that disagrees with theirs. Observed: the hostile-named one left out
    entirely, and mentioned only when the owner asked about it. Its name is
    text, which is all it ever was."""
    section = " ".join(ONBOARDING[ONBOARDING.index("### 5 ·"):].split())
    assert "Show every calendar the script returned" in section
    assert "shown as TEXT" in section


def test_the_relay_tool_is_named_only_where_it_exists():
    """The sheet must not name a tool the build may not have.

    Whether a relay tool is registered is a property of the image -- config.yaml
    either holds the server or it does not -- and a model told to call one that
    is absent does not conclude "not connected". It searches, searches again
    under another name, and narrates the hunt. All three reached a real owner in
    one turn: "There's no Latch-specific tool search hit for ... let me check if
    those exist under a different name", a `clarify` call, and the sheet's own
    rule quoted back at her.

    So the turn-top probe is a terminal command, which every build has, and the
    relay's tool is named only after it has answered `configured`.
    """
    configured = ONBOARDING.index("**`configured` means")
    assert "latch_status.py" in ONBOARDING[:configured], "the probe must come first"
    # Every relay tool, not the two that happened to appear first: the family is
    # `plow_<something>` and the next one added would otherwise slip in bare.
    for hit in re.finditer(r"(?<!mcp__latch__)\bplow_[a-z_]+\b", ONBOARDING):
        assert hit.start() > configured, (
            f"onboarding names a relay tool at {hit.start()}, before the branch "
            "that has established it exists")
        # And named as registered, prefix and all: the bare suffix is a tool the
        # build does not have, which sends the turn hunting for it.
        raise AssertionError(
            f"a relay tool is named without its server prefix: "
            f"{ONBOARDING[hit.start():hit.start() + 30]!r}")
    assert RELAY_TOOL in ONBOARDING[configured:]
    # The unconfigured branch reaches no tool of any kind: structural, since
    # the branch's whole job is that nothing is called from it.
    unconfigured = ONBOARDING[ONBOARDING.index("**`unconfigured`"):configured]
    assert "plow_run_command" not in unconfigured
    assert "argv=" not in unconfigured


def test_the_scripts_print_relay_tools_qualified_too():
    """A script's OUTPUT is agent-facing when the model is told to read it.

    post_to_kiosk's Latch block and mint_wall_token's ship-them line are
    instructions the turn acts on, so a bare tool name there is the same defect
    as a bare one in a sheet: a name the build does not register, and a turn
    that goes hunting through tool_search instead of calling it.

    The relay's own wire protocol still uses the unprefixed name -- that is what
    the MCP server exposes -- so this covers printed instructions only.
    """
    for rel in ("ld-shared/scripts/post_to_kiosk.py",
                "ld-setup/scripts/mint_wall_token.py"):
        source = (ROOT / rel).read_text()
        printed = re.findall(r'"[^"\n]*\bplow_[a-z_]+[^"\n]*"', source)
        for literal in printed:
            for hit in re.finditer(r"(?<!mcp__latch__)\bplow_[a-z_]+\b", literal):
                raise AssertionError(
                    f"{rel} prints an unqualified relay tool: {literal.strip()!r}")


def test_the_status_probe_answers_without_a_relay(tmp_path):
    """The script is what makes the branch decidable, so it has to answer on a
    box with no relay at all rather than raising -- that is the whole case."""
    ls = load("latch_status", "ld-setup/scripts/latch_status.py")
    configured = [
        "mcp_servers:\n  latch:\n    url: https://x.test\n",
        # A relay beside another server, one carrying only headers, one quoted,
        # one with a comment above it, and one that is not the first key in the
        # file -- every shape the deployment's own stanza has been seen in.
        "mcp_servers:\n  other:\n    url: x\n  latch:\n    url: y\n",
        "mcp_servers:\n  latch:\n    headers:\n      Authorization: Bearer x\n",
        "mcp_servers:\n  # the relay\n  latch:\n    url: y\n",
        # A comment indented deeper than the servers, and a blank line before
        # them: the level comes from the first real KEY, not the first indented
        # line, or a stray comment would set it too far in.
        "mcp_servers:\n      # indented however its author felt\n  latch:\n    url: y\n",
        "mcp_servers:\n\n  latch:\n    url: y\n",
        'mcp_servers:\n  "latch":\n    url: x\n',
        "model:\n  a: b\nmcp_servers:\n  latch:\n    url: x\nplugins:\n  a: b\n",
    ]
    unconfigured = [
        "mcp_servers: {}\n",
        "mcp_servers:\n",
        "model:\n  default: x\n",
        "mcp_servers:\n  other:\n    url: x\n",
        # `latch:` nested inside ANOTHER server's settings is not our relay --
        # it registers no tool, so reading it as one would answer "configured"
        # for a build that cannot reach a Mac and send the turn to call a tool
        # that is not there.
        "mcp_servers:\n  other:\n    latch:\n      url: https://x.test\n",
        "mcp_servers:\n  other:\n    headers:\n      latch:\n        url: x\n",
        # Both together: a deeper comment AND a nested latch. Deriving the level
        # from the comment would put it at the nested key and answer
        # "configured" for a build with no relay of ours at all.
        "mcp_servers:\n    # deeper than the servers\n  other:\n    latch:\n      url: x\n",
        # A name with nothing under it registers no tool: no url, no bearer.
        "mcp_servers:\n  latch:\n",
        "mcp_servers:\n  latch:\nmodel:\n  default: x\n",
        # And the old spelling is not the relay: the tool would be registered
        # under a prefix the sheet does not name.
        "mcp_servers:\n  plow:\n    url: https://x.test\n",
        # A commented-out relay is not a relay.
        "mcp_servers:\n  # latch: gone\n  other:\n    url: x\n",
    ]
    for text in configured:
        assert ls.relay_configured(text), text
    for text in unconfigured:
        assert not ls.relay_configured(text), text

    # A missing config.yaml is "unconfigured", not a traceback: this runs on the
    # first turn of a brand new agent.
    assert ls.config_path({"HERMES_HOME": str(tmp_path)}) == str(tmp_path / "config.yaml")
    assert ls.main(env={"HERMES_HOME": str(tmp_path)}) == 0


def test_the_status_probe_needs_only_the_standard_library():
    """The sheet runs it as plain `python3`, and PyYAML lives in Hermes' own
    venv rather than the system interpreter in at least one build we ship -- an
    `import yaml` under /usr/bin/python3 once took down every container start.
    A probe that raises is worse than no probe: the turn improvises, which is
    the thing this script exists to prevent."""
    source = (ROOT / "ld-setup/scripts/latch_status.py").read_text()
    body = source.split('"""', 2)[2]
    assert not re.search(r"^\s*(import|from)\s+(?!__future__|os|re|sys)", body, re.MULTILINE), (
        "the probe imports something outside the standard library's core")
    assert not re.search(r"^\s*(import yaml|from yaml)", body, re.MULTILINE), (
        "the probe reaches for PyYAML in code the sheet runs as plain python3")
    assert not re.search(r"yaml\.(safe_)?load", body), "PyYAML is being called"


def test_the_relay_key_is_latch_and_only_latch():
    """One spelling, because the key is also the tool's prefix.

    `latch` is the base seed's name for the relay and this repo's, and the model
    calls `mcp__latch__plow_run_command`. Accepting a second spelling here would
    answer "configured" for a build whose tool is registered under a name the
    sheet does not use -- and the turn would go hunting for a tool that is not
    there, which is how one calendar turn spent its whole budget on tool_search.
    """
    ls = load("latch_status", "ld-setup/scripts/latch_status.py")
    # Behaviour, not a constant: what matters is that the old spelling does not
    # answer "configured", whatever the code calls the name it looks for.
    assert not ls.relay_configured("mcp_servers:\n  plow:\n    url: https://x\n")
    assert ls.relay_configured("mcp_servers:\n  latch:\n    url: https://x\n")


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
    # Structural: every calendar template writes ids and carries no name key.
    section = ONBOARDING[ONBOARDING.index("### 5 ·"):]
    templates = re.findall(r'"sources": \[(.*?)\]', section, re.DOTALL)
    assert templates, "no calendar.sources template in the sheet"
    for template in templates:
        assert "calendar_id" in template
        assert '"name"' not in template, "a display name is being written to the config"


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





def test_the_listing_is_normalised_by_a_script_not_by_eye():
    """gog prints a note line before the array, a large result arrives as a
    persisted envelope, and the account is the primary entry rather than
    dataOwner. Each is a silent wrong answer if a model does it by hand."""
    section = ONBOARDING[ONBOARDING.index("### 5 ·"):]
    assert "calendar_list.py" in section
    assert (ROOT / "ld-setup/scripts/calendar_list.py").is_file()


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





@pytest.mark.parametrize("label,payload", [
    ("account null", '{"calendar": {"account": null, '
     '"sources": [{"calendar_id": "a@b.test"}]}}'),
    ("owner_identities carrying the null", '{"calendar": {"account": "a@b.test", '
     '"sources": [{"calendar_id": "a@b.test"}]}, '
     '"calendar_nudge": {"owner_identities": [null]}}'),
    ("the word null, as a string", '{"calendar": {"account": "null", '
     '"sources": [{"calendar_id": "a@b.test"}]}}'),
])
def test_a_null_account_can_never_reach_the_config(tmp_path, label, payload):
    """The ask branch is safe only if the null cannot be written while it waits.

    calendar.account is the identity every producer authenticates as, and
    calendar.sources landing without it is worse than either alone: sources
    present means §5 never runs again, account missing means the gate refuses
    forever. So the draft that carries the picks has to refuse a null outright
    rather than record half the answer.
    """
    config = tmp_path / "config.json"
    if label == "the word null, as a string":
        # Not blank, so the gate cannot catch it -- what stops this one is that
        # it is never composed: the template takes the owner's answer.
        draft(config, payload)
        assert json.loads(config.read_text())["calendar"]["account"] == "null"
        return
    with pytest.raises(SystemExit) as refusal:
        draft(config, payload)
    assert "blank" in str(refusal.value) or "nonblank" in str(refusal.value)
    assert not config.exists(), "a refused draft must leave nothing behind"


def test_clarify_is_forbidden_during_onboarding():
    """The ❓ rows are a tool, and it blocks the turn until someone picks.

    Three times it reached a real owner: a menu asking them to name the
    assistant, a menu asking how messages should be sent, and `❓ placeholder`.
    Banning numbered menus in prose did not cover the tool that renders them.
    """
    assert "Never call `clarify`" in " ".join(ONBOARDING.split())



def test_the_framework_name_is_not_the_agents_name():
    """Observed: "I'm Hermes." That is the software it runs on, the way a
    person is not called Android -- and it was said on a turn where no name
    existed to give."""
    opener = " ".join(SKILL[SKILL.index("### 1 · Opener"):SKILL.index("### 2 ·")].split())
    assert '"Hermes" is not your name' in opener


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
