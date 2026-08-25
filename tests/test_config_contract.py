"""The contract this repo commits to: one isolated agent on someone else's account.

Every assertion here exists because getting it wrong is quiet rather than loud.
A stray mount reaches another agent's state — and unlike this repo's three
siblings, the state on the other side of that mistake belongs to a different
person. A defaulted uid/gid re-owns live state in place. A branch ref in a pin
silently re-points a running agent at whatever landed upstream. A literal
credential ships a secret.
"""
import json
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
    for path in FORBIDDEN_PATHS:
        assert not _names_path((ROOT / "justfile").read_text(), path), (
            f"no recipe may reach {path}"
        )


@pytest.mark.parametrize("name", ["activate", "install-plugin", "install-connectors", "restore"])
def test_every_writer_names_this_agents_home(name):
    # The other half of the scan above: these four write to a host path, and
    # each must say which. A whole-file absence check cannot see a recipe that
    # names no home at all and defaults to upstream's.
    assert ".hermes-rowan" in _recipe(name), f"{name} must name this agent's own home"


def test_activation_refuses_a_home_it_was_edited_to_point_elsewhere():
    # The guard, not just the literal: the assertion above reads the recipe as
    # committed, and this is what stops an edited copy at runtime.
    recipe = _recipe("activate")
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
    assert '"$HOME/.hermes-rowan/skills/plow-connectors"' in _recipe("install-connectors")
    assert "/opt/data/skills/plow-connectors/plow_connector.py" in _recipe("check-connectors")


def test_both_installs_read_the_same_pin():
    # One upstream SHA covers the plugin and the connector skill. Two pins that
    # can drift would mean the skill reading the mail and the plugin holding the
    # token came from different upstream trees.
    for name in ("install-plugin", "install-connectors", "activate"):
        assert "runtime/plow-chat-plugin.ref" in _recipe(name)


def test_no_recipe_starts_a_second_gateway():
    """No recipe may `docker compose run`.

    The image's s6 entrypoint starts a gateway whatever command it is given, so
    `run` brings up a second one against this same /opt/data. With a chat
    activated both connect to it and answer every message, so every text gets
    two replies. `exec` uses the gateway that is already there.

    Comments may name `run` — that is where the reasoning lives.
    """
    offenders = []
    for i, line in enumerate((ROOT / "justfile").read_text().splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        if "docker compose run" in line:
            offenders.append(f"justfile:{i}")
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


def test_sign_in_derives_its_provider_from_the_config(config):
    """One copy of the provider, not two that a test has to keep in sync.

    `hermes auth add <provider>` used to hard-code the value that
    runtime/config.yaml also declares, and this test asserted the two agreed by
    matching the recipe's text. Every text match was wrong in one direction or
    the other: exact-equality on the whole `model` mapping froze `model.default`
    and forbade keys the config's own header invites, and a bare substring let
    `provider: openai` pass against `hermes auth add openai-codex` — authenticating
    as one provider while the config named another, which is the exact silent
    failure the assertion existed to prevent.

    So the recipe reads the config instead. With one copy there is no drift to
    detect, and what is left to assert is that it stayed that way.
    """
    recipe = _recipe("sign-in")
    assert "runtime/config.yaml" in recipe, (
        "sign-in must read model.provider from the config, not restate it"
    )
    assert f"hermes auth add {config['model']['provider']}" not in recipe, (
        "the provider is hard-coded again — that is the second copy this "
        "recipe was changed to remove"
    )


@pytest.mark.parametrize("name", ["sign-in", "activate"])
def test_every_recipe_that_rewrites_a_credential_reloads_the_gateway(name):
    # The gateway reads auth.json and .env at boot. sign-in writes the first,
    # activate replaces PLOW_CHAT_TOKEN in the second, and a gateway left
    # running holds the previous value while the recipe prints success.
    assert "docker compose restart hermes" in _recipe(name), (
        f"{name} rewrites a credential the gateway only reads at boot"
    )
