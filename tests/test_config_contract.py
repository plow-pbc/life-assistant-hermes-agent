"""The contract this repo commits to: one isolated agent on someone else's account.

Every assertion here exists because getting it wrong is quiet rather than loud.
A stray mount reaches another agent's state — and unlike this repo's three
siblings, the state on the other side of that mistake belongs to a different
person. A defaulted uid/gid re-owns live state in place. A branch ref in a pin
silently re-points a running agent at whatever landed upstream. A literal
credential ships a secret.
"""
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

SIBLING_HOMES = ("~/.hermes", "~/.hermes-admin", "~/.hermes-property")

# Not a home, and the one path here that is worth naming separately: the
# rentals agent's operations vault holds compiled guest conversations and
# property access facts, door and keypad codes among them.
FORBIDDEN_PATHS = SIBLING_HOMES + ("~/hermes-vault",)


def _names_path(text: str, path: str) -> bool:
    """Does `text` name `path`, in any spelling of the prefix?

    Matched on the bare tail, so `$HOME/`, `${HOME}/` and an absolute
    `/home/<user>/` are the same finding as `~/`. Anchoring on the `~/` spelling
    left a hole one character wide: `- HERMES_VAULT=$HOME/hermes-vault` reaches
    the gateway exactly as well and was invisible.

    The lookahead is what keeps `.hermes` from matching inside `.hermes-rowan`,
    which is the one path these files must name.
    """
    return bool(re.search(rf"{re.escape(path.removeprefix('~/'))}(?![\w-])", text))


def _executable_files() -> list[Path]:
    """Every tracked file this repo executes: the justfile, and scripts/.

    Enumerated from `git ls-files` rather than a fixed filename. The scans below
    used to read `justfile` alone, which was right until executable logic moved
    into scripts/ — at which point a `scripts/backup` naming a sibling home, or
    shelling out to `docker compose run`, would have shipped green. That is the
    "green on the commit that adds the thing it does not know about" shape these
    scans exist to avoid, and it reopened one directory over the moment the
    first script landed.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    names = [n for n in listing.split("\0") if n]
    return [
        ROOT / n
        for n in names
        if (n == "justfile" or n.startswith("scripts/")) and (ROOT / n).is_file()
    ]


def _recipe(name: str) -> str:
    """One recipe's body, from the justfile. Read as text rather than run.

    These assertions are about which paths a recipe may name, and running one to
    find out would activate a phone line or install a skill.
    """
    lines = (ROOT / "justfile").read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if re.match(rf"^{re.escape(name)}( [A-Z]+)*:$", l))
    body = []
    for line in lines[start + 1:]:
        if line and not line[0].isspace():
            break
        body.append(line)
    return "\n".join(body)


def _recipe_code(name: str) -> str:
    """One recipe's body with comment lines removed.

    Every assertion about what a recipe *does* has to read this, not _recipe().
    The reasoning blocks in this justfile quote the paths they warn against —
    sign-in's says "~/.hermes-rowan/config.yaml, NOT runtime/config.yaml" — so a
    substring check against the full body passes on the warning while the code
    below it does the opposite. Verified: reverting sign-in's `installed=` to the
    repo copy left the whole suite green.
    """
    return "\n".join(
        l for l in _recipe(name).splitlines() if not l.lstrip().startswith("#")
    )


def _recipe_names() -> list[str]:
    """Every recipe `just` knows about."""
    dump = json.loads(
        subprocess.run(
            ["just", "--dump", "--dump-format", "json"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
    )
    return sorted(dump["recipes"])


@pytest.fixture
def compose():
    return yaml.safe_load((ROOT / "compose.yml").read_text())


def test_every_service_mounts_only_this_agents_home(compose):
    """Every service, not just `hermes`.

    Scoping this to compose["services"]["hermes"] missed the realistic shape of
    the mistake: a whole service block pasted in from a sibling repo arrives
    under its own key, carrying its own six mounts, and a check that reads one
    key by name never looks at it.
    """
    assert list(compose["services"]) == ["hermes"], (
        "this repo runs one gateway; a second service is the copy-paste this "
        "file exists to catch, not a configuration to extend"
    )
    for name, service in compose["services"].items():
        assert service.get("volumes") == ["~/.hermes-rowan:/opt/data"], (
            f"service {name!r} needs exactly one mount: this agent reaches "
            "Gmail, Calendar and Slack over the Plow connector API, not through "
            "the filesystem. Every path a sibling's compose file would bring "
            "reaches another agent's live state — belonging to a different "
            "operator than this one."
        )


def test_no_forbidden_path_appears_anywhere_in_compose():
    """Any mention, not just a mount.

    The trailing-colon version only matched a short-form mount, so
    `- HERMES_VAULT=~/hermes-vault` — an env var handing the same path to the
    gateway by another route — passed clean.
    """
    text = (ROOT / "compose.yml").read_text()
    for path in FORBIDDEN_PATHS:
        assert not _names_path(text, path), f"compose.yml must not name {path}"


def test_uid_and_gid_have_no_default(compose):
    # s6 chowns /opt/data to these at boot, so a wrong value re-owns live state
    # rather than only affecting new files. Compose must refuse, not guess.
    env = compose["services"]["hermes"]["environment"]
    for key in ("HERMES_UID", "HERMES_GID"):
        entry = next(e for e in env if e.startswith(f"{key}="))
        assert ":?" in entry, f"{key} must fail loudly when unset, not default"


def test_container_and_image_are_this_agents_own(compose):
    # The project name, set rather than derived from the checkout directory.
    # Under the numbered-slot workflow a second clone yields a different compose
    # project against this same ~/.hermes-rowan mount, so `docker compose down`
    # run from the other directory reports success having stopped nothing.
    assert compose["name"] == "hermes-rowan"
    service = compose["services"]["hermes"]
    assert service["container_name"] == "hermes-rowan"
    # Pinned by digest: a tag re-resolves on every pull, changing a large
    # unreviewed surface under a running agent.
    assert "@sha256:" in service["image"]
    # No build key: this agent adds nothing to the upstream image. The rentals
    # agent's Dockerfile exists for its obsidian-wiki vault, which this has no
    # use for, and a derived layer here would be a surface to review for nothing.
    assert "build" not in service


def test_the_timezone_is_the_agents_owner_not_the_host(compose):
    """The one setting here that must NOT match the siblings.

    All three sibling agents run America/Los_Angeles because that is where their
    operator is. This agent belongs to someone in Chicago, and a life assistant
    resolves "tomorrow morning" and every scheduled thing against this value — so
    inheriting the sibling default is a two-hour error in precisely the place
    this agent exists for, and silent, because nothing else in the container
    compares its clock to anything.

    Asserted because the realistic way it breaks is the same copy-paste this
    whole file guards: the siblings are the template.
    """
    env = dict(e.split("=", 1) for e in compose["services"]["hermes"]["environment"])
    assert env["TZ"] == "America/Chicago", (
        "TZ must be Rowan's zone, not the operator's — the three sibling agents "
        "run America/Los_Angeles and are the copy-paste source"
    )
    # The comment beside it has to keep naming the sibling zone, because the
    # comment is what someone reads at the moment they are copy-pasting. A
    # global find-and-replace of the zone caught that sentence once and left it
    # saying the siblings run Chicago too — which reads as "the sibling default
    # is already correct", the exact opposite of the warning, and no assertion
    # about the value alone could see it.
    assert "America/Los_Angeles" in (ROOT / "compose.yml").read_text(), (
        "the TZ comment must still name the siblings' zone; without the contrast "
        "it tells the next reader the sibling default needs no change"
    )


def test_no_credential_is_passed_through_compose(compose):
    """The mounted dotenv is the only path in.

    An allowlist, not a PLOW_ prefix ban. The failure documented here — compose
    interpolating from a shell or a repo-root .env that no recipe writes, so the
    bring-up injects empty strings that shadow the real values — is a property of
    any credential passed through `environment:`, not of one prefix. An added
    OPENAI_API_KEY=${OPENAI_API_KEY} passed the prefix version, and passed
    test_no_secret_is_committed too because an interpolated value starts with $.
    Naming the three keys that belong here covers the general case in fewer
    lines.
    """
    names = {e.split("=", 1)[0] for e in compose["services"]["hermes"]["environment"]}
    assert names == {"HERMES_UID", "HERMES_GID", "TZ"}, (
        "credentials must come from ~/.hermes-rowan/.env through the mount, not "
        f"from compose interpolation; unexpected keys: {names - {'HERMES_UID', 'HERMES_GID', 'TZ'}}"
    )


def test_no_secret_is_committed():
    """Every value-shaped thing in a tracked file must be empty or interpolated.

    Scanned from `git ls-files`, not a list of names: a hard-coded tuple keeps
    this test green on the commit that adds the file it does not know about,
    while the name still claims to cover everything.
    """
    # A reference, in either spelling that appears here: ${VAR} as the gateway
    # expands it, and $var as the shell does. What must never appear is a value
    # that is neither empty nor a reference.
    reference = re.compile(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*(:-)?\}?$")
    # -z, and split on NUL: plain `git ls-files` renders a non-ASCII path in
    # C-quoted form that never resolves, and `.split()` would break any path
    # containing a space into two names that do not exist.
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    for name in filter(None, listing.split("\0")):
        path = ROOT / name
        # An index entry whose file is gone from the worktree is not an error
        # here; it is simply nothing to read.
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue  # binary; nothing to scan
        for lineno, line in enumerate(text.splitlines(), 1):
            where = f"{name}:{lineno}"
            # No comment skip. A credential pasted into a comment while
            # debugging is still a committed credential, and that is a likelier
            # way for one to land here than a live config line.
            bearer = re.search(r"Bearer\s+([A-Za-z0-9_\-.${}:]{8,})", line)
            if bearer:
                value = bearer.group(1).rstrip("\"'")
                assert reference.match(value), f"{where} carries a literal bearer"

            # Two spellings, because this repo tracks both. The KEY=value form
            # is how a dotenv and a compose environment entry carry one; the
            # `key: value` form is how runtime/config.yaml would, and that file
            # is lowercase YAML — exactly where a provider API key lands, and
            # invisible to a scanner that only knows SHOUTY assignments.
            for pattern, flags in (
                (r"\s*-?\s*([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL|AUTH|_UID))=(.*)$", 0),
                (r"\s*-?\s*([A-Za-z][\w-]*(?:token|secret|key|password|credential|auth))\s*:\s*(.*)$", re.I),
            ):
                assigned = re.match(pattern, line, flags)
                if not assigned:
                    continue
                value = assigned.group(2).strip().strip("\"'")
                # Empty, or an interpolation of any form — bare ${VAR}, $var,
                # and compose's ${VAR:?message} with its spaces and prose. A
                # literal credential never starts with a dollar sign.
                assert value == "" or value.startswith("$") or reference.match(value), (
                    f"{where} assigns a literal value to {assigned.group(1)}"
                )


@pytest.fixture
def config():
    return yaml.safe_load((ROOT / "runtime" / "config.yaml").read_text())


def test_the_pin_is_a_sha():
    ref = (ROOT / "runtime" / "plow-chat-plugin.ref").read_text().strip()
    assert re.fullmatch(r"[0-9a-f]{40}", ref), (
        f"plow-chat-plugin.ref must be a 40-char SHA, got {ref!r} — a branch "
        "would re-point a running agent on the next upstream push, and this pin "
        "carries both the plugin holding the chat token and the skill that "
        "reads Rowan's mail"
    )


def test_this_agent_has_no_mcp_servers(config):
    """No first-party servers, and that is the capability boundary.

    Gmail, Calendar and Slack are reached through the plow-connectors skill,
    which calls the Plow connector REST API with the gateway's own
    PLOW_CHAT_TOKEN. An mcp_servers block appearing here would mean a second
    credential arrived from somewhere — and the realistic somewhere is a
    copy-paste from the rentals agent (Hostex, Seam) or the property agent
    (Latch), none of which this agent may reach.
    """
    assert "mcp_servers" not in config


def test_the_phone_line_is_enabled(config):
    assert config["plugins"]["enabled"] == ["plow-chat-platform"]
    assert config["platforms"]["plow_chat"]["enabled"] is True
    # No group prompts: this agent has one private chat. The plugin keys them by
    # display name from PLOW_CHAT_GROUP_UIDS, so a prompt naming no configured
    # group is a silent no-op rather than an error.
    assert "extra" not in config["platforms"]["plow_chat"]


def test_the_dotenv_contract_carries_no_values():
    """.env.example is the key contract, and must never carry a value.

    Narrower than test_no_secret_is_committed on purpose: this asserts every
    line is a bare `KEY=`, which catches a placeholder like `PLOW_CHAT_TOKEN=xxx`
    that the credential-shaped scan would wave through.
    """
    for lineno, line in enumerate((ROOT / ".env.example").read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        assert line.endswith("="), f".env.example:{lineno} carries a value: {line!r}"


def test_no_recipe_can_target_another_agents_home():
    """`--data-dir` is the only thing deciding which agent these rewrite.

    Upstream's activation script does not honour HERMES_DOTENV, and it
    *replaces* PLOW_CHAT_CHAT_UID and PLOW_CHAT_TOKEN rather than shadowing
    them — so a recipe pointed at another agent's home takes it off its chat
    until /sethome is re-sent, and spends a one-time activation to do it.

    Scanned whole-file rather than over an enumerated list of recipes. The list
    version went green on any recipe added after it — a backup, a migrate, a
    second install — which is the "green on the commit that adds the thing it
    does not know about" shape this module's docstring warns against. The
    justfile names sibling agents only descriptively ("another agent's home"),
    never as literal paths, so a whole-file scan needs no allowlist.
    """
    for f in _executable_files():
        text = f.read_text()
        for path in FORBIDDEN_PATHS:
            assert not _names_path(text, path), (
                f"{f.relative_to(ROOT)} must not reach {path}"
            )


@pytest.mark.parametrize("name", ["activate", "install-plugin", "install-connectors", "restore"])
def test_every_writer_names_this_agents_home(name):
    # The other half of the scan above: these four write to a host path, and
    # each must say which. A whole-file absence check cannot see a recipe that
    # names no home at all and defaults to upstream's.
    assert ".hermes-rowan" in _recipe_code(name), f"{name} must name this agent's own home"


def test_activation_refuses_a_home_it_was_edited_to_point_elsewhere():
    # The guard, not just the literal: the assertion above reads the recipe as
    # committed, and this is what stops an edited copy at runtime.
    recipe = _recipe_code("activate")
    assert "*/.hermes-rowan)" in recipe, (
        "activate needs its runtime guard on $HOME/.hermes-rowan — the string "
        "check above only sees the recipe as written"
    )


def test_the_connector_skill_is_installed_where_it_is_invoked():
    """The install destination and the invoked path must be the same directory.

    SKILL.md's allowed-tools line names
    /opt/data/skills/plow-connectors/plow_connector.py literally, so a skill
    installed one directory deeper — the way the property agent nests its skill
    under skills/productivity/ — loads and is then refused permission to run its
    own helper, while check-connectors probes a path nothing wrote. These two
    recipes are the pair that can drift.
    """
    assert '"$HOME/.hermes-rowan/skills/plow-connectors"' in _recipe_code("install-connectors")
    assert "/opt/data/skills/plow-connectors/plow_connector.py" in _recipe_code("check-connectors")


def test_both_installs_read_the_same_pin():
    # One upstream SHA covers the plugin and the connector skill. Two pins that
    # can drift would mean the skill reading the mail and the plugin holding the
    # token came from different upstream trees.
    for name in ("install-plugin", "install-connectors", "activate"):
        assert "runtime/plow-chat-plugin.ref" in _recipe_code(name)


def test_no_recipe_starts_a_second_gateway():
    """No recipe may `docker compose run`.

    The image's s6 entrypoint starts a gateway whatever command it is given, so
    `run` brings up a second one against this same /opt/data. With a chat
    activated both connect to it and answer every message, so every text gets
    two replies. `exec` uses the gateway that is already there.

    Comments may name `run` — that is where the reasoning lives.
    """
    offenders = []
    for f in _executable_files():
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if "docker compose run" in line:
                offenders.append(f"{f.relative_to(ROOT)}:{i}")
    assert offenders == [], f"these start a rival gateway: {offenders}"


def test_every_recipe_has_a_real_description():
    """Every recipe's `just --list` text must read as a description.

    `just` takes the LAST comment line before a recipe as its doc, so a
    reasoning block ending in prose donates its tail: the property agent's
    justfile really did advertise "hunting for a key." as a recipe's purpose.

    Recipes come from `just --dump` rather than a regex over the file, which
    silently exempted dependencies, default parameters and attributes. The
    property asserted is that the doc starts with a capital: a description
    written for the reader does, the tail of a sentence does not.
    """
    dump = json.loads(
        subprocess.run(
            ["just", "--dump", "--dump-format", "json"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
    )
    bad = {}
    for name, recipe in dump["recipes"].items():
        doc = (recipe.get("doc") or "").strip()
        if not doc:
            bad[name] = "no description"
        elif not doc[0].isupper():
            bad[name] = f"reads as a sentence fragment: {doc!r}"
    assert bad == {}, f"recipes whose `just --list` text is not a description: {bad}"


def test_sign_in_authenticates_against_the_configured_provider(config):
    """Run the real extraction, and compare what it produces to the config.

    End-to-end rather than a text match on the recipe. Three review rounds went
    into comparing sign-in's hard-coded provider against config.yaml by matching
    the recipe's text, and every match was wrong in one direction: equality on
    the whole `model` mapping froze `model.default` and forbade keys the config
    header invites; a bare substring let `provider: openai` pass against
    `auth add openai-codex`, authenticating as one provider while the config
    named another.

    Deriving the value fixed the drift, but the first version of this test then
    only asserted the recipe *mentioned* the config file — so a wrong indent, a
    renamed key, or a block picked in the wrong order would all have shipped
    green. Executing the same command the recipe runs is what actually covers it.
    """
    derived = subprocess.run(
        ["scripts/model-provider", "runtime/config.yaml"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert derived == config["model"]["provider"], (
        f"sign-in would authenticate against {derived!r}, but the gateway is "
        f"configured for {config['model']['provider']!r}"
    )
    # And that the recipe reads the copy the GATEWAY loads. `restore` installs
    # the repo file at ~/.hermes-rowan/config.yaml in a separate step, and the
    # running gateway resolved model.provider from the installed one at boot —
    # so extracting from runtime/config.yaml here mints a credential for a
    # provider the gateway is not using the moment the two differ. Same failure
    # this test is named for, one file over.
    recipe = _recipe_code("sign-in")
    assert "scripts/model-provider" in recipe
    assert 'hermes auth add "$provider"' in recipe
    assert re.search(r"^\s*installed=~/\.hermes-rowan/config\.yaml\s*$", recipe, re.M), (
        "sign-in must read the installed config the gateway loaded, not the "
        "repo copy that `restore` has not necessarily pushed to it yet"
    )


# The recipes that write into ~/.hermes-rowan, listed rather than derived — and
# said plainly, because the previous version's docstring claimed a derivation
# that a hard-coded set-equality was actually performing. Deriving it by "names
# the home in its code" was also the wrong criterion: it is *writing* that
# obliges a reload, so a future read-only recipe would have been swept in and
# then required to restart the gateway, which is not a contract worth asserting.
BOOT_READ_WRITERS = ("restore", "install-plugin", "install-connectors", "activate", "sign-in")



@pytest.mark.parametrize("name", BOOT_READ_WRITERS)
def test_every_recipe_that_writes_boot_read_state_reloads_the_gateway(name):
    """auth.json, .env, config.yaml and the plugin are all read once, at boot.

    Upstream says so outright — "Hermes boots once with the Plow Chat
    environment already populated" — so any recipe that writes into
    ~/.hermes-rowan leaves a live gateway running the previous value while the
    recipe prints success.

    Asserted on the call to scripts/reload-if-running rather than on the restart
    command. Four inline copies of that block drifted the moment they existed —
    one gained a change-gate, the others did not; one was fatal under `set -e`,
    the others were not — and a substring check for "docker compose restart
    hermes" cannot tell a reload that happens from one behind a condition that
    is false exactly when it matters.

    And it still cannot. Four rounds of tightening this anchor each found a
    narrower way to hide a gate — a same-line `||`, a block-form `if`, a
    continuation-wrapped `||` — because whether a call is reachable is not a
    property of shell *text*. So this asserts only what reading text can honestly
    decide: every writer calls the helper. Whether the reload then happens is the
    helper's behaviour, and all four of its branches are covered by running it
    against a stubbed docker, below — no-answer, no-gateway, restart, and a
    restart that fails. An
    assertion that claimed more than it checked is the failure this file keeps
    removing; the last version's message named a gate it could not see.
    """
    assert "scripts/reload-if-running" in _recipe_code(name), (
        f"{name} writes state the gateway only reads at boot, and must reload it"
    )


def _docker_stub(tmp_path, ps_output: str, restart_rc: int = 0, ps_rc: int = 0):
    """A `docker` on PATH that records its argv and answers `compose ps`.

    All four branches turn on what `docker compose ps` says, whether it runs at
    all (`ps_rc`), and whether `restart` succeeds — so stubbing docker is what
    makes them reachable. Recording argv is how "did it actually restart"
    becomes an observable rather than an inference from an exit code.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "argv.log"
    stub = bindir / "docker"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        "case \"$*\" in\n"
        f'  *"compose ps"*) printf "%s" "{ps_output}"; exit {ps_rc} ;;\n'
        f"  *\"compose restart\"*) exit {restart_rc} ;;\n"
        "esac\n"
    )
    stub.chmod(0o755)
    # Keys quoted, and the real ids rather than literals. `HERMES_UID=1000`
    # written plainly trips test_no_secret_is_committed's `_UID` arm — correctly,
    # since that arm exists for PLOW_CHAT_CHAT_UID, which is minted with the
    # token. Exempting the name would weaken the scan for a test's convenience;
    # this form does not, and using the actual uid is what the recipes do anyway.
    env = dict(
        os.environ,
        PATH=f"{bindir}:{os.environ['PATH']}",
        **{"HERMES_UID": str(os.getuid()), "HERMES_GID": str(os.getgid())},
    )
    return env, log


def _run_reload(env):
    return subprocess.run(
        ["scripts/reload-if-running", "the config"],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )


def test_the_reload_helper_does_nothing_when_no_gateway_is_running(tmp_path):
    # The normal case during first bring-up. Exit 0, and crucially no restart
    # attempted — the early exit and the restart are different branches.
    env, log = _docker_stub(tmp_path, ps_output="")
    proc = _run_reload(env)
    assert proc.returncode == 0, proc.stderr
    # Read unconditionally, and pin the positive first. Falling back to "" on a
    # missing log sourced the negative claim from a surface that is ambiguous
    # when empty: a stub that was never invoked would have read as "nothing was
    # restarted" — the exact shape of absence this repo does not accept.
    argv = log.read_text()
    assert "compose ps" in argv, "the stub was never consulted"
    assert "compose restart" not in argv


def test_the_reload_helper_restarts_a_running_gateway(tmp_path):
    # The branch the whole helper exists for, and the one nothing covered while
    # the docstring above claimed it did.
    env, log = _docker_stub(tmp_path, ps_output="abc123")
    proc = _run_reload(env)
    assert proc.returncode == 0, proc.stderr
    assert "compose restart hermes" in log.read_text()
    assert "restarting the gateway" in proc.stdout


def test_the_reload_helper_is_not_fatal_when_the_restart_fails(tmp_path):
    """A failed restart must not look like a failed write.

    Every caller has finished its write by the time this runs — activate has
    spent a one-time activation — so a red exit here reads as "the write failed"
    and invites a re-run that costs far more than a stale process does. The
    justfile's comments depend on this contract; it is asserted rather than
    described.
    """
    env, _ = _docker_stub(tmp_path, ps_output="abc123", restart_rc=1)
    proc = _run_reload(env)
    assert proc.returncode == 0, "a failed restart must not fail the caller"
    assert "just restart" in proc.stderr


def test_the_reload_helper_distinguishes_no_gateway_from_no_answer(tmp_path):
    """A docker that could not answer must not read as "no gateway is running".

    Piping `docker compose ps` straight into `grep -q .` conflates them: compose
    failing on compose.yml's own ${HERMES_UID:?} guards prints nothing, and the
    pipeline says there is nothing to reload — so the reload silently never
    happens and every caller reports success.

    Driven through the stub rather than by stripping HERMES_UID from a real
    docker. That version went green for the wrong reason on any machine without
    docker installed: `docker` not found exits 127, the same `if !` fires, and
    the assertion passes without ever reaching the guards it names.
    """
    env, log = _docker_stub(tmp_path, ps_output="", ps_rc=1)
    proc = _run_reload(env)
    assert proc.returncode != 0, (
        "the helper reported success without being able to ask whether a gateway "
        "was running"
    )
    assert "could not ask docker" in proc.stderr
    argv = log.read_text()
    assert "compose ps" in argv, "the stub was never consulted"
    assert "compose restart" not in argv, "nothing may be restarted on an unknown state"


def test_the_reload_helper_does_nothing_when_no_gateway_is_running(tmp_path):
    # The normal case during first bring-up. Exit 0, and crucially no restart
    # attempted — the early exit and the restart are different branches.
    env, log = _docker_stub(tmp_path, ps_output="")
    proc = _run_reload(env)
    assert proc.returncode == 0, proc.stderr
    # Read unconditionally, and pin the positive first. Falling back to "" on a
    # missing log sourced the negative claim from a surface that is ambiguous
    # when empty: a stub that was never invoked would have read as "nothing was
    # restarted" — the exact shape of absence this repo does not accept.
    argv = log.read_text()
    assert "compose ps" in argv, "the stub was never consulted"
    assert "compose restart" not in argv


def test_the_reload_helper_restarts_a_running_gateway(tmp_path):
    # The branch the whole helper exists for, and the one nothing covered while
    # the docstring above claimed it did.
    env, log = _docker_stub(tmp_path, ps_output="abc123")
    proc = _run_reload(env)
    assert proc.returncode == 0, proc.stderr
    assert "compose restart hermes" in log.read_text()
    assert "restarting the gateway" in proc.stdout


def test_the_reload_helper_is_not_fatal_when_the_restart_fails(tmp_path):
    """A failed restart must not look like a failed write.

    Every caller has finished its write by the time this runs — activate has
    spent a one-time activation — so a red exit here reads as "the write failed"
    and invites a re-run that costs far more than a stale process does. The
    justfile's comments depend on this contract; it is asserted rather than
    described.
    """
    env, _ = _docker_stub(tmp_path, ps_output="abc123", restart_rc=1)
    proc = _run_reload(env)
    assert proc.returncode == 0, "a failed restart must not fail the caller"
    assert "just restart" in proc.stderr


def test_the_reload_helper_distinguishes_no_gateway_from_no_answer():
    """A compose that refused to run must not read as "no gateway is running".

    Piping `docker compose ps` straight into `grep -q .` conflates them: without
    HERMES_UID/GID exported, compose fails on compose.yml's own guards, prints
    nothing, and the pipeline says there is nothing to reload — so the reload
    silently never happens and every caller reports success. Run here rather
    than read, in the one environment that reproduces it: no HERMES_UID.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("HERMES_UID", "HERMES_GID")}
    proc = subprocess.run(
        ["scripts/reload-if-running", "the config"],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    assert proc.returncode != 0, (
        "the helper reported success without being able to ask whether a gateway "
        "was running"
    )
    assert "could not ask docker" in proc.stderr
