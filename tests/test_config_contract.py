"""What makes this agent THIS agent -- on someone else's account.

The fleet-wide invariants moved to plow-pbc/agent-mgr with the deployment: the
home mount, the uid/gid contract, no credential through compose, no recipe
starting a second gateway, activation refusing a home it does not own. They are
asserted there once for every agent rather than restated in each repo.

What is left is the instance layer, plus the one thing this repo owns outright:
scripts/latch-verdict.py, which is why `check-latch` survived the migration.
Every assertion here exists because getting it wrong is quiet rather than loud,
and unlike this repo's siblings the state on the other side of a mistake belongs
to a different person.
"""
import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def dotenv(path):
    """The KEY=VALUE lines of a dotenv, stripped, comments dropped.

    One reader for every assertion this file makes about a dotenv. There were
    three hand-rolled ones and they disagreed: this filters on the STRIPPED
    line, where an earlier descriptor() tested `#` on the raw line -- so an
    indented comment reached its dict(split) and raised ValueError, taking down
    every test that called it, while the newer copies quietly skipped it. Two
    views of the same file is the one thing a file whose job is asserting that
    file's contract cannot have.
    """
    lines = [l for l in (x.strip() for x in path.read_text().splitlines())
             if l and not l.startswith("#")]
    for l in lines:
        # Loud, not skipped. An `"=" in l` filter here would drop a bare
        # `sk-...` line silently -- and agent.env is exempted from the
        # credential guard on the strength of a test that reads through this,
        # so a line this reader cannot see is a line nothing checks.
        assert "=" in l, f"{path.name}: not a KEY=VALUE line: {l!r}"
    return lines


def descriptor():
    return dict(line.split("=", 1) for line in dotenv(ROOT / "agent.env"))

def config():
    return yaml.safe_load((ROOT / "runtime" / "config.yaml").read_text())


DESCRIPTOR_KEYS = {"AGENT_CONFIG"}


def test_the_descriptor_carries_nothing_but_the_shared_config_path():
    """Closed set, deliberately: every instance reads this one file, so a key
    added here is given to ALL of them.

    That is why AGENT_TZ is absent. A shared descriptor holds one value, and a
    zone is per-person -- shipping one would boot every other instance on
    someone else's clock, which no test could catch because the value would be
    right for whoever it was chosen for.

    Closing the set rather than listing what is forbidden does three jobs at
    once. AGENT_HOME / AGENT_CONTAINER / AGENT_PROJECT are excluded as a
    consequence rather than as a second list to keep in sync. A person-valued
    key cannot arrive at all -- documenting one does not make it shippable,
    because every instance reads this file.

    And it is what backs agent.env's exemption from the credential guard: that
    exemption has to rest on something checked, the way .env.example's
    blank-value test backs its own. A key-shape rule was too weak for the job --
    every AGENT_* name passed it, so AGENT_TOKEN=sk-... would have shipped green.
    One exact key cannot."""
    assert set(descriptor()) == DESCRIPTOR_KEYS, (
        "agent.env is a CLOSED SET: it holds AGENT_CONFIG and nothing else. "
        "Every instance reads this one file, so adding a key means editing "
        "DESCRIPTOR_KEYS deliberately -- see README.md. A person-valued key "
        "(a timezone, a locale) does not belong here at all, and identity "
        "(AGENT_HOME/AGENT_CONTAINER/AGENT_PROJECT) is agent-mgr's to derive "
        "from the registry name."
    )


def test_the_descriptor_names_where_this_agents_config_lives():
    assert descriptor()["AGENT_CONFIG"] == "runtime/config.yaml"
    assert (ROOT / "runtime" / "config.yaml").is_file()


def test_the_phone_line_is_enabled():
    cfg = config()
    assert "plow-chat-platform" in cfg["plugins"]["enabled"]
    assert cfg["platforms"]["plow_chat"]["enabled"] is True


def test_latch_is_the_only_mcp_server():
    assert list(config()["mcp_servers"]) == ["latch"]


def test_latch_is_configured_from_the_environment_not_from_git():
    """DOMO_DEVICE_UID decides which Mac an instance can drive -- its owner's, not
    the operator's. It never appears in this repo."""
    latch = config()["mcp_servers"]["latch"]
    assert "${DOMO_DEVICE_UID}" in latch["url"]
    assert "${DOMO_MCP_TOKEN}" in latch["headers"]["Authorization"]


def test_every_pinned_skill_is_a_sha_not_a_branch():
    """Empty today, and the emptiness is the point.

    plow-connectors was the only row, and it went out with the dashboard work:
    the four producers that read Gmail, Google Calendar and Slack have no data
    source on this agent, so nothing here reaches a connector. latch#183 is what
    refills this file -- a vendored gog behind Latch -- and the per-row rule
    below is what will check that row when it lands.

    Deliberately NOT asserting the file is non-empty. It used to say the
    connector skill is what lets an instance reach its owner's mail, which was
    true while there was one; asserting it now would fail the suite for being
    correct."""
    rows = [r for r in (ROOT / "skills.tsv").read_text().splitlines() if r.strip()]
    for row in rows:
        repo, ref, dest = row.split("\t")[:3]
        assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), row
        assert repo and dest


def test_skills_tsv_carries_no_comment_lines():
    """A comment here breaks `agent-mgr restore`, and only at deploy time.

    agent-mgr gates the replay on `[ -s skills.tsv ]` -- size, not content --
    then feeds every line with a non-empty first tab-field to lib/fetch-tree. A
    zero-byte file is skipped cleanly, but a file holding only `# see latch#183`
    is non-empty, so the comment text becomes the repo argument and restore dies
    on it. The explanation belongs in a docstring like this one, never in the
    file itself, and this test is what keeps a well-meaning edit from putting it
    there."""
    text = (ROOT / "skills.tsv").read_text()
    for line in text.splitlines():
        assert not line.lstrip().startswith("#"), (
            f"skills.tsv carries a comment line: {line!r} -- agent-mgr feeds it to "
            "fetch-tree as a repo name and restore dies. Keep the file empty or "
            "tab-separated rows only."
        )


def test_no_credential_file_is_tracked():
    """Credentials live in this agent's home dotenv, which is outside the repo.

    Two named exemptions, and everything else keeps the broad shape rule. An
    earlier pass swapped the suffix rule for exact basenames to stop `agent.env`
    tripping it -- and quietly stopped catching `prod.env`, `secrets.env`,
    `auth.json.bak` and `latch-auth.json` along the way. A false positive on one
    known filename is an allowlist problem, not a reason to narrow the rule.

    -z, because this is a security guard and a filename must not be able to
    defeat it: git C-quotes paths with non-ASCII bytes, so `café/.env` arrives
    as `"caf\303\251/.env"` and its basename computes to `.env"`, and
    whitespace-splitting fragments any path containing a space.
    """
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    for name in out.stdout.split("\0")[:-1]:
        base = name.rsplit("/", 1)[-1]
        # Anchored to the full path git prints, not the basename. The two
        # exemptions are excused because two other tests cover those exact
        # files -- and those tests read ROOT/agent.env and ROOT/.env.example, so
        # a `secrets/agent.env` or `runtime/.env.example` matched by basename
        # would be excused by a promise nothing checks. Same reasoning as -z
        # above, one level up: the allowlist must not be the weakest link.
        if name in ("agent.env", ".env.example"):
            continue
        assert not base.endswith(".env"), f"{name} is tracked"
        assert not base.startswith(".env."), f"{name} is tracked"
        assert "auth.json" not in base and "auth.lock" not in base, f"{name} is tracked"


def test_the_dotenv_example_carries_no_values():
    """The exemption above rests on this: it is a shape, not a secret store."""
    keys = dotenv(ROOT / ".env.example")
    assert keys, ".env.example declares no keys -- is it still the skeleton?"
    for line in keys:
        key, value = line.split("=", 1)
        assert value == "", f".env.example carries a value for {key}"

def _recipe(name: str) -> str:
    """One recipe's body, from the justfile. Read as text rather than run.

    These assertions are about which paths a recipe may name, and running one to
    find out would reach a live container.
    """
    lines = (ROOT / "justfile").read_text().splitlines()
    # Parameters may be any just identifier, not just uppercase: `check-latch`
    # takes a lowercase `agent` so one shared repo can probe whichever instance
    # is asked for. A regex admitting only [A-Z] silently found no recipe and
    # raised StopIteration from `next`, which reads as "the test is broken"
    # rather than "the recipe grew a parameter".
    pattern = rf"^{re.escape(name)}( [A-Za-z_][A-Za-z0-9_-]*)*:$"
    start = next(
        (i for i, l in enumerate(lines) if re.match(pattern, l)),
        None,
    )
    assert start is not None, f"no recipe named {name!r} in the justfile"
    body = []
    for line in lines[start + 1:]:
        if line and not line[0].isspace():
            break
        body.append(line)
    return "\n".join(body)


def _recipe_code(name: str) -> str:
    """One recipe's body with comment lines removed.

    Every assertion about what a recipe *does* has to read this, not _recipe().
    The reasoning blocks in this justfile quote the shapes they warn against, so
    a substring check against the full body passes on the warning while the code
    below it does the opposite.
    """
    return "\n".join(
        l for l in _recipe(name).splitlines() if not l.lstrip().startswith("#")
    )


def _latch_module():
    """The script the check-latch recipe runs, loaded once per call site."""
    spec = importlib.util.spec_from_file_location(
        "latch_verdict", ROOT / "scripts" / "latch-verdict.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _verdict():
    return _latch_module().verdict







def test_every_interpolation_in_the_config_is_declared_in_the_dotenv():
    """A ${NAME} with no matching key ships a literal, unexpanded string.

    The gateway would send `Bearer ${DOMO_MCP_TOKEN}` verbatim, the relay would
    answer 401, and check-latch would report the token REVOKED — sending the
    operator to the owner's Mac to re-mint a credential that was never wrong. A
    rename on either side is silent otherwise: the config test only checks the
    ${...} spellings, and the dotenv test only checks lines carry no value.
    """
    referenced = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)\}", (ROOT / "runtime" / "config.yaml").read_text()))
    declared = {l.split("=", 1)[0] for l in dotenv(ROOT / ".env.example")}
    missing = referenced - declared
    assert missing == set(), (
        f"runtime/config.yaml interpolates {sorted(missing)}, which .env.example "
        "does not declare — the gateway would send the literal ${...} text"
    )


def test_check_latch_actually_runs_the_verdict_script():
    """The verdict tests are worthless if the recipe stops calling it.

    Same contract this file already holds for scripts/model-provider and
    scripts/reload-if-running, and for the same reason: if check-latch drifts
    back to an HTTP-status-only `case`, every verdict test above keeps passing
    against a script nobody runs, and the suite goes green on the exact
    regression that script exists to prevent.
    """
    code = _recipe_code("check-latch")
    assert "scripts/latch-verdict.py" in code, (
        "check-latch must delegate its pass/fail decision to the tested script"
    )
    # And that it is not deciding for itself alongside it: a status-code case
    # statement here is how the two would diverge.
    assert "200)" not in code, (
        "check-latch must not re-implement a status verdict next to the script"
    )



def test_check_latch_does_not_reintroduce_the_double_zero_fallback():
    # curl's own -w already emits 000 on a failed transfer; a `|| printf 000`
    # next to it is what produced "000000".
    code = _recipe_code("check-latch")
    assert "printf 000" not in code, (
        "curl already writes 000 via -w on a failed transfer; a fallback printf "
        "doubles it and the transport-failure verdict becomes unreachable"
    )




def test_latch_verdict_recognises_a_real_answer_in_any_framing():
    """The one thing it must get right: a Mac that answered.

    streamable-HTTP lets the server emit notifications before the response and
    makes the space after `data:` optional, so all of these are the same
    successful answer. Joining the frames instead of parsing each one is what
    turned a legal two-frame reply into `{..}{..}`.
    """
    v = _verdict()
    answer = '{"id":1,"result":{"tools":[{"name":"plow_vault"}]}}'
    for label, body in {
        "spaced frame": "data: " + answer,
        "spaceless frame": "data:" + answer,
        "notification first": 'data: {"method":"notifications/message"}\n\ndata: ' + answer,
        "bare json": answer,
        # A malformed early frame must not shadow the real answer behind it:
        # selection is "the frame carrying tools", not "the first with a key".
        # id:1 is load-bearing — the removed classifier preferred the id-1
        # frame, so a noise frame WITHOUT it was already handled correctly and
        # this row would pass under both implementations, pinning nothing.
        "malformed frame wearing the answer's id":
            'data: {"id":1,"error":"boom"}\n\ndata: ' + answer,
    }.items():
        assert "1 tools" in v("200", body), f"{label} should be recognised"

    # More tools than the preview shows — the shape actually observed live (12).
    # One case pins three things a one-tool list cannot separate: the count
    # comes from `tools` and not from the slice, the preview stops at three, and
    # the separator is ", ". The row that used to carry this went out with the
    # degraded rendering it also exercised.
    four = '{"id":1,"result":{"tools":[{"name":"a"},{"name":"b"},{"name":"c"},{"name":"d"}]}}'
    assert "4 tools (a, b, c…)" in v("200", four)




@pytest.mark.parametrize("code,body", [
    ("401", '{"error":"invalid token"}'),
    ("406", "Client must accept both application/json and text/event-stream"),
    ("000", ""),
    ("200", 'data: {"id":1,"error":{"code":-32001,"message":"device offline"}}'),
    ("200", '{"jsonrpc":"2.0","id":1}'),
    ("502", "<html>Bad Gateway</html>"),
    # A non-JSON-RPC proxy in front of the relay answers with a string-valued
    # `error`. The classifier version did `d["error"].get(...)` and died with
    # AttributeError — the recipe whose whole purpose is one actionable line
    # crashing instead of printing it. Nothing here reads `error` any more.
    ("200", '{"error":"unauthorized"}'),
    # The same unguarded shape lived on `result` until it was checked too — the
    # truthy-scalar form is what a proxy in front of the relay returns:
    # `{"result":"ok"}` crashed on .get, and a string-valued `tools` reported
    # len("nope") — four tools — as a SUCCESS, which is worse than crashing.
    ("200", '{"id":1,"result":"ok"}'),
    ("200", '{"id":1,"result":{"tools":"nope"}}'),
    # Malformed tool lists are not an answer — they take the failure path and
    # the body is shown. Kept as rows rather than deleted with the rendering
    # they used to exercise: both of these CRASHED before the unwraps were
    # shape-checked, so they pin a real regression, not a display contract.
    ("200", '{"id":1,"result":{"tools":[1,2]}}'),
    ("200", '{"id":1,"result":{"tools":[{"name":null}]}}'),
    # >600 chars: the cap that used to truncate here dropped the explaining
    # line exactly when the body was long enough to need reading.
    ("502", '{"detail":"' + "x" * 900 + '","reason":"the-line-that-explains-it"}'),
])
def test_latch_verdict_fails_loudly_and_shows_what_came_back(code, body):
    """No taxonomy — the response is the diagnosis.

    Every one of these used to get a hand-written label, and each round of
    review found another shape the labels got wrong. The contract now is only
    that failure is loud and the evidence is verbatim, so an unanticipated shape
    cannot be mislabelled — there is no label.
    """
    v = _verdict()
    with pytest.raises(SystemExit) as e:
        v(code, body)
    msg = str(e.value)
    assert "did NOT answer" in msg
    assert code in msg, "the status has to be in the message"
    assert (body in msg) if body.strip() else ("(empty body)" in msg)



def test_split_probe_survives_a_body_that_never_arrived():
    """The bug this pins shipped twice, and the trim deleted its only guard.

    A transport failure writes no body and therefore no newline, and
    `split("\\n", 1)` returns a one-element list that raises on unpack — the
    mutation this catches. The status-as-body bug came from the shell version
    (`${code#*$'\\n'}` does not strip when there is no newline); partition
    cannot reproduce it, which is why the branch guarding against it was removed
    as unreachable rather than kept.
    """
    mod = _latch_module()

    assert mod.split_probe("000") == ("000", ""), "no body must not echo the status"
    assert mod.split_probe('200\ndata: {"x":1}') == ("200", 'data: {"x":1}')
    assert mod.split_probe("200\n") == ("200", ""), "a trailing newline is still no body"


def override():
    """The parsed override, with a message that names the file when it is gone."""
    path = ROOT / "compose.override.yml"
    assert path.is_file(), (
        "compose.override.yml is missing -- it is how the ld- skills reach the "
        "container, and agent-mgr loads it only if it exists (lib/common.sh:577)"
    )
    return yaml.safe_load(path.read_text())


SKILL_DIRS = sorted(p.name for p in ROOT.glob("ld-*") if p.is_dir())


def test_every_skill_is_mounted_flat_and_read_only():
    """Four declarative strings, asserted exactly.

    This replaces a brace-aware volume parser, its own unit test, and four
    overlapping invariant paths -- read-only, strict-child, one-segment-deep,
    ${AGENT_DIR}-rooted, not-an-ancestor-of-the-home -- with the strings those
    invariants were describing. Every one of them is still enforced, because an
    exact set cannot be satisfied by a mount that breaks any of them: drop `:ro`,
    nest a level, mount the checkout root, make a source relative, or forget a
    skill entirely and this fails with both sets printed.

    Derived from the tree, so adding a producer without its mount fails here
    rather than at 06:00 as a cron running a skill the container does not have.

    Why these strings and not others -- why flat, why read-only, why
    ${AGENT_DIR} and not `./` -- is compose.override.yml's own comment. It is the
    file a reader opens; restating it here was a second copy to keep in step."""
    assert set(override()["services"]["hermes"]["volumes"]) == {
        f"${{AGENT_DIR:?set by agent-mgr from the registry}}/{name}"
        f":/opt/data/skills/{name}:ro"
        for name in SKILL_DIRS
    } | {
        # The one non-skill bind: the shared ld-config pinned read-only over
        # itself. /opt/data is the agent's writable home, so without this a
        # prompt-injected turn could rewrite the config every producer
        # trusts; the single-FILE bind keeps the fixed reader path while the
        # ld/ handoff directory stays writable. Same exact-string discipline:
        # drop :ro, widen it to the directory, or reroot the source and this
        # fails with both sets printed.
        "${AGENT_HOME:?set by agent-mgr from the instance descriptor}"
        "/ld/config.json:/opt/data/ld/config.json:ro"
    }


def test_every_skill_path_in_a_skill_md_resolves_in_the_tree():
    """Every path a SKILL.md hands the agent, checked where the agent will use it.

    Absolute, all of them, and that is the contract rather than a style
    preference. The container's WorkingDir and the gateway's cwd are both
    /opt/hermes (measured), and `hermes cron create` sets no --workdir, so a bare
    `ld-shared/references/kiosk-protocol.md` resolves to
    /opt/hermes/ld-shared/... and is simply not there. The producers compose
    their tiles from that spec, so the failure lands at 06:00, inside the
    container, as an agent that cannot find the contract it was told to read.

    The check is only worth anything because the mapping is earned:
    test_every_skill_is_mounted_flat_and_read_only pins
    ${AGENT_DIR}/<name> -> /opt/data/skills/<name>, so resolving these against
    ROOT really does mean the agent can open them.

    It checks that the absolute paths RESOLVE; it does not check that a new
    reference is written absolute. A linter for that was built and removed: at
    three hand-authored files it cost more than the drift it fenced, and the
    eight paths it found are fixed regardless. The convention is visible in the
    files themselves -- every path in all three is absolute."""
    prefix = "/opt/data/skills/"
    leaves = set(SKILL_DIRS)
    seen = 0
    for skill_md in sorted(ROOT.glob("ld-*/SKILL.md")):
        text = skill_md.read_text()
        for ref in re.findall(r"/opt/data/skills/([\w./-]+)", text):
            ref = ref.rstrip(".").rstrip("/")
            head, _, rest = ref.partition("/")
            assert head in leaves, (
                f"{skill_md.name} names {prefix}{ref}, but {head} is not a skill "
                "directory in this tree"
            )
            target = ROOT / head / rest if rest else ROOT / head
            assert target.is_file() or target.is_dir(), (
                f"{skill_md.name} names {prefix}{ref}, which is not in the tree"
            )
            seen += 1

    assert seen, (
        "no /opt/data/skills/ paths found in any SKILL.md -- has the reference "
        "style changed?"
    )


# Hermes confines its file-writing tool to this root; a handoff outside it is
# denied at 06:00, in front of nobody. The image sets it, not this repo.
WRITE_SAFE_ROOT = "/opt/data"

# Listed, not globbed: discovery needed a floor (an empty glob SKIPS a
# parametrized test), a helper exclusion and a sheet-presence rule -- three
# guards on the finder, none on the contract, and together bigger than it.
PRODUCERS = [
    ("ld-morning-triage", "post_alert.py"),
    ("ld-morning-updates", "post_message.py"),
    ("ld-weekly-digest", "post_digest.py"),
    ("ld-calendar-nudge", "post_nudge.py"),
    ("ld-weather", "post_weather.py"),
    ("ld-sports", "post_sports.py"),
]
# The helper ships no sheet; its constant is the docstring example the next
# producer copies, so it is pinned for the write-safe check alone.
HANDOFFS = PRODUCERS + [("ld-shared", "post_to_kiosk.py")]
BEFORE, AFTER = r"(?<![\w.\-/])", r"(?![\w.\-/])"


def _handoff(skill, wrapper):
    source = (ROOT / skill / "scripts" / wrapper).read_text()
    paths = re.findall(r'MESSAGE_FILE\s*=\s*"([^"]+)"', source)
    # ld-shared's only quoted assignment is its docstring EXAMPLE -- the real
    # constant is `MESSAGE_FILE: str | None = None`, which this cannot see. So a
    # reworded docstring lands here, and a bare unpack would blame neither the
    # file nor the contract.
    assert len(paths) == 1, (
        f"{skill}/scripts/{wrapper} declares {len(paths)} quoted MESSAGE_FILE "
        "assignments; expected exactly 1"
    )
    return paths[0]


def test_the_handoff_table_lists_every_wrapper():
    """The one guard the literal cannot encode: a wrapper with no row.

    Four more producers are specified in ld-dashboard's JOBS table, and the row
    nobody remembers to add is the one that ships on /tmp and logs the denial in
    front of nobody. Set equality fails on an empty glob rather than skipping,
    so it needs none of the machinery discovery did."""
    listed = set(HANDOFFS)
    found = {(w.parent.parent.name, w.name) for w in ROOT.glob("ld-*/scripts/post_*.py")}
    assert found == listed, (
        f"{sorted(found - listed)} exist with no row in HANDOFFS; "
        f"{sorted(listed - found)} name a wrapper that is gone"
    )


@pytest.mark.parametrize(("skill", "wrapper"), HANDOFFS, ids=[s for s, _ in HANDOFFS])
def test_every_handoff_path_is_inside_the_write_safe_root(skill, wrapper):
    """The AGENT writes this file, so its file tool has to be able to create it.

    Shipped on /tmp, which write_file refuses. Both producers logged the denial
    on their first unattended run and the cards landed anyway -- the agent fell
    back to the shell -- so the failure is invisible from the kiosk and recurs
    as a coin flip. test_post_to_kiosk drives MESSAGE_FILE through tmp_path, so
    it passes against any path at all; nothing else catches this."""
    path = _handoff(skill, wrapper)
    assert path.startswith(WRITE_SAFE_ROOT + "/"), (
        f"{skill}/scripts/{wrapper} hands the agent {path}, which its file tool "
        f"cannot create -- outside HERMES_WRITE_SAFE_ROOT ({WRITE_SAFE_ROOT})"
    )


@pytest.mark.parametrize(("skill", "wrapper"), PRODUCERS, ids=[s for s, _ in PRODUCERS])
def test_each_producer_sheet_names_the_handoff_its_wrapper_reads(skill, wrapper):
    """The wrapper is what runs; the SKILL.md is what the agent is TOLD.

    Each sheet names the handoff twice -- to write, then to read back -- so a
    half-applied change leaves one stale and the agent reads two files. Both
    scans are whole-token: /mnt/opt/data/ld/weather-text and
    /opt/data/ld/weather-text.tmp otherwise read as agreement. The stale scan
    wants a PATH ending in -text, never a bare token, because "plain-text
    cards" is the wording cards 1/2/4 use -- exactly the blocked producers;
    anchoring to the handoff's directory instead fails a correct sheet, since
    /opt/data/ld/config.json lives there too."""
    path = _handoff(skill, wrapper)
    sheet = (ROOT / skill / "SKILL.md").read_text()

    assert re.search(BEFORE + re.escape(path) + AFTER, sheet), (
        f"{skill}/SKILL.md never names {path}, the handoff its wrapper reads -- "
        "the agent would write somewhere else entirely"
    )
    stale = set(re.findall(BEFORE + r"(/[\w./-]*-text)" + AFTER, sheet)) - {path}
    assert not stale, (
        f"{skill}/SKILL.md still names {sorted(stale)} alongside {path} -- a "
        "half-applied path change, and the agent is told two different files"
    )


def test_the_nudge_filter_writes_exactly_what_each_leg_consumes():
    """nudge_candidates.py writes the two handoffs; post_nudge.py and
    send_nudge_chat.py each read-and-consume their own. A drifted path on
    either side tells a leg to read a file nobody writes — an unattended
    half-hourly failure in front of nobody. Both stay under the write-safe
    root the whole ld/ data directory lives in."""
    nc_src = (ROOT / "ld-calendar-nudge" / "scripts" / "nudge_candidates.py").read_text()
    (kiosk_written,) = re.findall(r'KIOSK_FILE = "([^"]+)"', nc_src)
    (chat_written,) = re.findall(r'CHAT_FILE = "([^"]+)"', nc_src)
    assert kiosk_written == _handoff("ld-calendar-nudge", "post_nudge.py")
    chat_src = (ROOT / "ld-calendar-nudge" / "scripts" / "send_nudge_chat.py").read_text()
    (chat_read,) = re.findall(r'HANDOFF = "([^"]+)"', chat_src)
    assert chat_written == chat_read
    assert chat_written.startswith(WRITE_SAFE_ROOT + "/")


def test_the_config_template_cannot_hide_a_placeholder_from_the_gate():
    """The gate's placeholder check matches whole strings only, so the
    template's new key must ship as the exact whole-string form the gate
    can see — a mid-string embedding ("/Users/[USER]/...") would pass the
    gate unfilled and reach the 07:05 gather. Template-wide embedded-
    placeholder scanning was deliberately cut (review round 3); this pin
    covers only the key this feature adds — the template's other
    placeholders are unpinned by design."""
    spec = importlib.util.spec_from_file_location(
        "ld_config_gate", ROOT / "ld-shared" / "scripts" / "ld_config_gate.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)

    template = ROOT / "ld-shared" / "references" / "config.example.json"
    parsed = json.loads(template.read_text())
    assert "an unfilled [UPPER_SNAKE] placeholder remains" in gate.gate(parsed)
    assert parsed["morning_triage"]["chat_db_path"] == "[CHAT_DB_PATH]"
