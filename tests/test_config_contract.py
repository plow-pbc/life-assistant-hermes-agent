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
import functools
import importlib.util
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


@functools.cache
def tracked_files():
    """Every tracked path, once, for the two callers that need one.

    -z, and split on NUL, because git C-quotes paths with non-ASCII bytes and
    whitespace-splitting fragments any path containing a space. That reasoning
    belongs to test_no_credential_file_is_tracked, which argued it first and in
    detail; this exists so the other caller inherits the ruling instead of
    re-deriving a plain `.split()` that reintroduces exactly what it forbids --
    a tracked `ld-shared/my notes/x.md` fragmenting into a spurious `notes`
    segment while the real one is lost, both silently.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [path for path in out.stdout.split("\0") if path]


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
    for name in tracked_files():
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
    }


def _is_hostname_head(ref):
    """A URL's host segment is not a citation of a repo directory.

    Dormant for the live `site.api.espn.com/apis/site/v2/sports/...` line in
    ld-sports/SKILL.md -- that string holds no tracked segment, so the pattern
    never matches it -- and load-bearing the day a tracked
    `ld-shared/references/sports/` appears. Which rows it decides is
    not restated here -- that count has been wrong or one short in three of the
    last four rounds, and each row's own comment sits next to its assertion.

    The cost, stated because an unstated hole is how the next round re-derives
    it as a bug or quietly widens it: any dotted first segment is treated as a
    host, so `config.d/scripts/x` or `ld-shared.old/scripts/y` is silently
    exempt though it resolves under /opt/hermes like every bare path. No tracked
    directory has a dot today. A LEADING dot is a hidden directory rather than a
    host, which covers `.` and `..` as a consequence.
    """
    head = ref.split("/", 1)[0]
    return not head.startswith(".") and "." in head


@functools.cache
def _unanchored_pattern():
    """Compiled once, and lazily, so only the guard tests depend on git.

    Built at import time this ran `git ls-files` with check=True during
    COLLECTION, so outside a checkout -- a source export, a container that copies
    the tree without .git, git absent from PATH -- the whole module died and took
    every unrelated dotenv, compose and mount assertion with it.
    """
    names = sorted({part for f in tracked_files() for part in Path(f).parent.parts})
    assert names, (
        "`git ls-files` reported no tracked directories -- an empty alternation "
        "collapses to (?:) and would flag anything containing a slash"
    )
    # A boundary before the segment, so a known name cannot match as the SUFFIX
    # of a longer token: `myscripts/foo` is not a citation of scripts/. No `^`
    # branch -- at offset 0 there is no preceding character, so the lookbehind
    # already succeeds, and without re.MULTILINE `^` could never match elsewhere.
    return re.compile(
        rf"[\w./-]*?(?<![\w-])(?:{'|'.join(map(re.escape, names))})/[\w./-]*"
    )


def unanchored_refs(text):
    """Path-like citations in a SKILL.md that the agent could not resolve.

    The defect is cwd-relative resolution: the container's WorkingDir and the
    gateway's cwd are both /opt/hermes (measured) and `hermes cron create` sets
    no --workdir, so any path handed to the agent that does not start with `/`
    resolves under /opt/hermes and is not there. ABSOLUTE is therefore the rule,
    not "starts with /opt/data/skills/" -- these files also cite
    /opt/data/cron/jobs.json, /opt/data/ld/config.json and /opt/hermes/bin/hermes,
    all correct and all immune.

    Segment names come from the TRACKED tree, so the guard is identical on every
    checkout. It was the filesystem, and that made coverage a function of local
    build state: an untracked docs/ from a plan doc written here put `docs`,
    `superpowers` and `plans` in the alternation on one machine and nobody
    else's. The cost of determinism, stated because an unstated hole is how the
    next round re-narrows this one: a directory that is never tracked -- a
    gitignored state/, an assets/ its author has not git added yet -- is
    invisible here, and a bare `state/foo.json` goes unflagged even though it
    resolves under /opt/hermes exactly like every spelling this does catch.

    Returns the offending strings so the caller can name them."""
    return sorted(
        {
            m
            for m in _unanchored_pattern().findall(text)
            if not m.startswith("/") and not _is_hostname_head(m)
        }
    )


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
    ROOT really does mean the agent can open them. A relative reference has no
    such backing, which is why the rule below is that there are none."""
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

        # No unanchored citation may creep back in, in ANY spelling. The rule
        # and its derivation live in unanchored_refs() so they are reachable
        # from a test -- four rounds of widening this were verified only by hand,
        # which is how round five silently re-narrows what round four widened.
        unanchored = unanchored_refs(text)
        assert not unanchored, (
            f"{skill_md.name} hands the agent unanchored path(s) {unanchored} -- "
            "the agent's cwd is /opt/hermes, so a relative path resolves to "
            "nothing. Give each an absolute container path"
        )

    assert seen, (
        "no /opt/data/skills/ paths found in any SKILL.md -- has the reference "
        "style changed?"
    )


@pytest.mark.parametrize("text,flagged", [
    # Every spelling four rounds of widening this guard were verified against by
    # hand. Each round's "verified red" evaporated into shell history, so round
    # five could silently re-narrow what round four widened. These are the rows
    # that stop it.
    ("read `ld-shared/references/kiosk-protocol.md`", ["ld-shared/references/kiosk-protocol.md"]),
    ("read `./ld-shared/references/kiosk-protocol.md`", ["./ld-shared/references/kiosk-protocol.md"]),
    ("the wrappers hop `../../ld-shared/scripts`", ["../../ld-shared/scripts"]),
    ("run `scripts/register_crons.py`", ["scripts/register_crons.py"]),
    # A first segment that is a real repo-root directory holding different
    # content -- reads fine in the checkout, finds nothing in the container.
    ("see `runtime/config.yaml`", ["runtime/config.yaml"]),
    ("see `tests/fixtures/hermes-cron-jobs.json`", ["tests/fixtures/hermes-cron-jobs.json"]),
    # Bare single-segment citations, which are prose natural to these files.
    ("vendored under `ld-shared/` and mounted", ["ld-shared/"]),
    ("see `references/` for the spec", ["references/"]),
    # Absolute is the rule, not the skills prefix: these three are correct and
    # immune to cwd, and all appear in ld-dashboard/SKILL.md today.
    ("persists to `/opt/data/cron/jobs.json`", []),
    ("reads `/opt/data/ld/config.json`", []),
    ("run `/opt/hermes/bin/hermes cron list`", []),
    ("run `/opt/data/skills/ld-weather/scripts/post_weather.py`", []),
    # The real ld-sports/SKILL.md line. Green by accident today -- it holds no
    # tracked segment, so the pattern never matches it -- and the row below is
    # the one that pins the exemption itself.
    ("site.api.espn.com/apis/site/v2/sports/<sport>/<league>/scoreboard", []),
    # The exemption on a real HOST head. `scripts` is tracked so the pattern
    # matches and the string is relative, so the heuristic is the only thing
    # returning [].
    ("site.api.espn.com/scripts/scoreboard", []),
    # Exempt via the absolute rule -- the match begins at the `//` -- not via the
    # heuristic. Pinned so that stays true.
    ("https://example.com/scripts/thing.py", []),
    # `scripts` is tracked so the pattern matches, and the head is a HIDDEN
    # DIRECTORY rather than a host -- it resolves under /opt/hermes like any
    # other bare path, so exempting it would be a silent miss. (A row naming an
    # untracked segment such as .github/workflows/ would assert nothing: the
    # pattern would not match it here at all.)
    ("see `.github/scripts/ci.yml`", [".github/scripts/ci.yml"]),
    # The same exemption on a DOTTED RELATIVE head, which is the documented miss
    # rather than a URL: a real relative citation the guard lets through, and the
    # narrowing cost _is_hostname_head states. Executable so a future widening
    # (a real host parse, a TLD check) flips a test rather than leaving the
    # docstring quietly stale.
    ("see `config.d/scripts/x`", []),
    # A known name inside a longer token is not a citation. Without a boundary
    # before the segment these are flagged and reported verbatim as paths the
    # author never wrote, and the name set grows with the tracked tree.
    ("the myscripts/foo helper", []),
    ("see old-shared/bar for context", []),
])
def test_unanchored_refs_flags_exactly_the_citations_the_agent_cannot_resolve(text, flagged):
    assert unanchored_refs(text) == flagged
