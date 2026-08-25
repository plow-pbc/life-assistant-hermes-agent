"""The contract this repo commits to: one isolated agent on someone else's account.

Every assertion here exists because getting it wrong is quiet rather than loud.
A stray mount reaches another agent's state — and unlike this repo's three
siblings, the state on the other side of that mistake belongs to a different
person. A defaulted uid/gid re-owns live state in place. A branch ref in a pin
silently re-points a running agent at whatever landed upstream. A literal
credential ships a secret.
"""
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

SIBLING_HOMES = ("~/.hermes", "~/.hermes-admin", "~/.hermes-property")


@pytest.fixture
def compose():
    return yaml.safe_load((ROOT / "compose.yml").read_text())


def test_mounts_only_this_agents_home(compose):
    volumes = compose["services"]["hermes"]["volumes"]
    assert volumes == ["~/.hermes-rowan:/opt/data"], (
        "this agent needs exactly one mount: it reaches Gmail, Calendar and "
        "Slack over the Plow connector API, not through the filesystem. A "
        "copy-paste from a sibling's compose file is the realistic way this "
        "breaks, and every path it would bring reaches another agent's live "
        "state — belonging to a different operator than this one."
    )


def test_no_sibling_home_appears_anywhere_in_compose():
    # Substring, not the parsed mount list: a sibling home reaching this file as
    # a long-form mount mapping, or in an env var, is the same mistake and the
    # parsed-volumes assertion above would not see either.
    text = (ROOT / "compose.yml").read_text()
    for home in SIBLING_HOMES:
        assert f"{home}:" not in text, f"compose.yml must not mount {home}"


def test_uid_and_gid_have_no_default(compose):
    # s6 chowns /opt/data to these at boot, so a wrong value re-owns live state
    # rather than only affecting new files. Compose must refuse, not guess.
    env = compose["services"]["hermes"]["environment"]
    for key in ("HERMES_UID", "HERMES_GID"):
        entry = next(e for e in env if e.startswith(f"{key}="))
        assert ":?" in entry, f"{key} must fail loudly when unset, not default"


def test_container_and_image_are_this_agents_own(compose):
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

    Passing PLOW_CHAT_* through compose interpolates from the shell or a
    repo-root .env that no recipe writes, so the documented bring-up would
    inject empty strings — which can shadow the real values the gateway loads
    from /opt/data/.env.
    """
    env = compose["services"]["hermes"]["environment"]
    assert not [e for e in env if e.startswith("PLOW_")], (
        "credentials must come from ~/.hermes-rowan/.env through the mount, "
        "not from compose interpolation"
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

            assigned = re.match(
                r"\s*-?\s*([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL|AUTH|_UID))=(.*)$",
                line,
            )
            if assigned:
                value = assigned.group(2).strip().strip("\"'")
                # Empty, or an interpolation of any form — bare ${VAR}, $var,
                # and compose's ${VAR:?message} with its spaces and prose. A
                # literal credential never starts with a dollar sign.
                assert value == "" or value.startswith("$") or reference.match(value), (
                    f"{where} assigns a literal value to {assigned.group(1)}"
                )
