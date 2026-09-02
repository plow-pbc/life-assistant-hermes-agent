"""The pin-chain walker's judgement calls, on inputs it cannot get wrong quietly.

`release-check.py` is only useful if a reader trusts its verdicts, and every
way it can lie is silent: an ancestry question answered by date order, a
material path classified as prose, a registry timeout read as "not published".
None of those raise, and each one produces a confident wrong release plan.

So the tests here are the four judgements, not the report. The report is I/O
against five repositories and reads correct or does not; these are the parts
that decide what it says.

The git fixtures build real repositories rather than mocking `git`: the
questions are about what git answers, and a mocked `git` would be a test of the
mock. They are two commits each and cost milliseconds.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "release-check.py"

sys.path.insert(0, str(ROOT / "scripts"))
import importlib.util

spec = importlib.util.spec_from_file_location("release_check", SCRIPT)
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A repository with `main` at two commits and a branch that left after the first.

    That shape is the one the registry actually holds -- someone published a tag
    for a commit on a PR branch -- and it is the shape a date comparison gets
    wrong, because the branch commit is the NEWEST commit in the repository and
    is on no release line at all.
    """
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "--quiet", "--initial-branch=main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")

    (r / "Dockerfile").write_text("FROM nousresearch/hermes-agent@sha256:" + "a" * 64 + "\n")
    git(r, "add", "-A")
    git(r, "commit", "--quiet", "-m", "first")
    first = git(r, "rev-parse", "HEAD")

    git(r, "checkout", "--quiet", "-b", "side")
    (r / "side.txt").write_text("x\n")
    git(r, "add", "-A")
    git(r, "commit", "--quiet", "-m", "on a branch that never merged")
    side = git(r, "rev-parse", "HEAD")

    git(r, "checkout", "--quiet", "main")
    (r / "image").mkdir()
    (r / "image" / "first-boot.sh").write_text("#!/bin/sh\n")
    git(r, "add", "-A")
    git(r, "commit", "--quiet", "-m", "second")
    head = git(r, "rev-parse", "HEAD")

    return {"path": r, "first": first, "head": head, "side": side}


class TestAncestry:
    def test_an_ancestor_is_recognised(self, repo):
        assert rc.is_ancestor(repo["path"], repo["first"], repo["head"])

    def test_a_branch_commit_is_not_an_ancestor(self, repo):
        # The whole point: `side` is the newest commit by date and still not on
        # the line that leads to main.
        assert not rc.is_ancestor(repo["path"], repo["side"], repo["head"])

    def test_a_commit_is_its_own_ancestor(self, repo):
        assert rc.is_ancestor(repo["path"], repo["head"], repo["head"])

    def test_behind_counts_commits_not_days(self, repo):
        assert rc.behind(repo["path"], repo["first"], repo["head"]) == 1

    def test_behind_is_zero_when_identical(self, repo):
        assert rc.behind(repo["path"], repo["head"], repo["head"]) == 0

    def test_behind_is_unknown_across_an_unmerged_branch(self, repo):
        # None, never 0. A caller printing "0 commits behind" for two commits
        # that do not relate has reported the opposite of the truth.
        assert rc.behind(repo["path"], repo["side"], repo["head"]) is None

    def test_newest_published_ignores_tags_off_main(self, repo):
        tags = {f"base-{repo['first']}", f"base-{repo['side']}"}
        assert rc.newest_published_on_main(repo["path"], tags, repo["head"]) == repo["first"]

    def test_newest_published_prefers_the_later_ancestor(self, repo):
        tags = {f"base-{repo['first']}", f"base-{repo['head']}"}
        assert rc.newest_published_on_main(repo["path"], tags, repo["head"]) == repo["head"]

    def test_newest_published_is_none_when_nothing_on_main_is_published(self, repo):
        assert rc.newest_published_on_main(repo["path"], {f"base-{repo['side']}"}, repo["head"]) is None

    def test_newest_published_is_none_when_the_registry_is_unknown(self, repo):
        # None in, None out: an unreachable registry must not read as "nothing
        # published", which would make every plan say "publish it first".
        assert rc.newest_published_on_main(repo["path"], None, repo["head"]) is None

    def test_tags_for_another_repos_commits_are_skipped(self, repo):
        # The tag namespace is shared with this repo's own commits, so most tags
        # name objects the base mirror has never heard of.
        tags = {f"base-{'d' * 40}", f"base-{repo['first']}"}
        assert rc.newest_published_on_main(repo["path"], tags, repo["head"]) == repo["first"]


class TestClassification:
    @pytest.mark.parametrize("path", [
        "Dockerfile",
        "image/seed/config.yaml",
        "image/systemd/hermes-gateway.service",
        "image/first-boot.sh",
        "image/seed/skills/growth/plow-invite/SKILL.md",
    ])
    def test_material_paths_are_flagged(self, path):
        assert rc.classify([path]) != {}

    @pytest.mark.parametrize("path,reason", [
        # The interesting one: a base rewrite of SOUL.md lands in the layer and
        # is then overwritten by our own Dockerfile, so it is not drift for us.
        ("image/seed/SOUL.md", "runtime/SOUL.md"),
        ("README.md", "prose"),
        ("scripts/check-image.sh", "does not run them"),
        (".gitignore", "not in the image"),
        (".dockerignore", "build context"),
    ])
    def test_immaterial_paths_are_not_flagged_but_are_explained(self, path, reason):
        assert rc.classify([path]) == {}
        assert reason in (rc.why_not(path) or "")

    def test_an_unknown_path_is_neither_flagged_nor_explained_away(self):
        # Silence is the failure mode to avoid: a new top-level file in the base
        # should show up unannotated rather than be quietly absorbed.
        assert rc.classify(["something-new.txt"]) == {}
        assert rc.why_not("something-new.txt") is None

    def test_the_reason_travels_with_the_path(self):
        hits = rc.classify(["image/seed/config.yaml", "README.md"])
        assert list(hits) == ["image/seed/config.yaml"]
        assert "mcp_servers" in hits["image/seed/config.yaml"]

    def test_soul_md_is_not_shadowed_by_the_skills_rule(self):
        # image/seed/SOUL.md sits beside image/seed/skills/, and a sloppier
        # prefix rule ("image/seed/") would swallow it.
        assert rc.classify(["image/seed/SOUL.md"]) == {}


class TestRegistryStates:
    def fake(self, tags):
        def fetch(url, headers=None, timeout=20):
            if "/token/" in url:
                return {"token": "t"}
            return {"tags": tags}
        return fetch

    def test_published_tags_come_back_as_a_set(self):
        got = rc.registry_tags(fetch=self.fake(["base-aaa", "base-bbb"]))
        assert got == {"base-aaa", "base-bbb"}

    def test_an_empty_repository_is_an_empty_set_not_unknown(self):
        # Empty and unknown are different answers; only one of them means
        # "safe to conclude nothing is published".
        assert rc.registry_tags(fetch=self.fake([])) == set()

    def test_a_failure_is_unknown_rather_than_unpublished(self):
        def boom(url, headers=None, timeout=20):
            raise OSError("timed out")
        assert rc.registry_tags(fetch=boom) is None

    def test_a_failure_on_the_second_call_is_also_unknown(self):
        def half(url, headers=None, timeout=20):
            if "/token/" in url:
                return {"token": "t"}
            raise OSError("timed out")
        assert rc.registry_tags(fetch=half) is None

    def test_a_malformed_body_is_unknown(self):
        def junk(url, headers=None, timeout=20):
            if "/token/" in url:
                return {"token": "t"}
            return ["not", "an", "object"]
        assert rc.registry_tags(fetch=junk) is None


class TestDockerfileParsing:
    def test_the_life_pin_is_read_from_the_tag_half(self, tmp_path, monkeypatch):
        sha = "b" * 40
        (tmp_path / "Dockerfile").write_text(
            f"FROM public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-{sha}@sha256:{'c' * 64}\n"
        )
        monkeypatch.setattr(rc, "REPO_ROOT", tmp_path)
        assert rc.life_pinned_base() == sha

    def test_a_digestless_from_is_refused_rather_than_half_read(self, tmp_path, monkeypatch):
        # A tag without a digest is a moving pin, which is the one thing the
        # FROM line exists to prevent. Refusing beats reporting on it.
        (tmp_path / "Dockerfile").write_text(
            f"FROM public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-{'b' * 40}\n"
        )
        monkeypatch.setattr(rc, "REPO_ROOT", tmp_path)
        with pytest.raises(SystemExit):
            rc.life_pinned_base()

    def test_the_base_repos_own_pins_are_read_at_a_commit(self, repo):
        img, plug = rc.base_dockerfile_pins(repo["path"], repo["first"])
        assert img == "nousresearch/hermes-agent@sha256:" + "a" * 64
        assert plug is None   # this fixture's Dockerfile has no ARG line

    def test_a_commit_without_a_dockerfile_is_unknown_not_a_crash(self, repo):
        assert rc.base_dockerfile_pins(repo["path"], "d" * 40) == (None, None)


class TestExitCodes:
    def test_the_help_text_names_both_subcommands(self):
        out = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, cwd=ROOT
        )
        assert out.returncode == 0
        assert "base" in out.stdout and "all" in out.stdout

    def test_an_unknown_subcommand_is_refused(self):
        out = subprocess.run(
            [sys.executable, str(SCRIPT), "sideways"], capture_output=True, text=True, cwd=ROOT
        )
        assert out.returncode != 0

    def test_offline_without_a_mirror_fails_instead_of_reporting_nothing(self, tmp_path, monkeypatch):
        # The dangerous shape is a clean exit 0 that checked nothing.
        monkeypatch.setattr(rc, "MIRRORS", tmp_path / "absent")
        with pytest.raises(SystemExit):
            rc.mirror("https://example.invalid/x", "x", offline=True)
