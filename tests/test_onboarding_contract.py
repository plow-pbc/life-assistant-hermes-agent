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
    """Email, calendar ids and the Mac username arrive through connectors.

    Asking for them is not merely redundant -- it is a question the owner
    cannot answer usefully at that point, in the middle of the one conversation
    that decides whether they stay.
    """
    onboarding = SKILL[SKILL.index("## Onboarding"):SKILL.index("## The wall (optional)")]
    for asked in ("owner_email", "extra_calendar_ids", "mac_username"):
        assert asked not in onboarding, f"onboarding asks for {asked}"


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


def test_draft_and_patch_are_not_both_accepted():
    """Two merge modes with different verdicts; silently preferring one would
    make the strict one unreachable from a caller that thought it asked."""
    with pytest.raises(SystemExit) as refusal:
        wc.main(["--patch", "--draft"], env={"TZ": "UTC"},
                stdin=io.StringIO("{}"), config_path="/nonexistent/config.json")
    assert "not both" in str(refusal.value)
