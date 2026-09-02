#!/usr/bin/env python3
"""Walk the whole pin chain and name every gap, in the order they have to close.

Usage: release-check.py [all|base]

Five repositories hold six pins between a plugin commit and a running tenant,
and not one of them is watched by anything:

    hermes-plow-chat main
      -> plow-hermes-agent Dockerfile ARG PLOW_CHAT_PLUGIN_SHA   (hop 1)
      -> a published base-<sha> tag in the registry              (hop 2)
      -> plow agents.json .exe.hermes.revision                   (hop 3)
      -> this repo's Dockerfile FROM base-<sha>@sha256:<digest>  (hop 4)
      -> plow agents.json .exe.life.revision                     (hop 5)
      -> an API deploy

Every hop is a manual pull request in a different repository, several of them
in repositories with no CI at all, and each one is invisible from the one below
it. That is the whole reason this exists: "is anything stale?" was a question
nobody could answer without walking all five by hand, and the answer tonight was
yes at four of the six hops at once.

Each hop prints CURRENT vs LATEST with a commit count and a verdict. The run
ends with an ORDERED list of the pull requests needed to bring everything
current -- repository, file, field, target SHA -- which is the release plan, and
the order is not advice: each step's target does not exist until the one above
it merges and its build publishes.

Subcommands:
  all    the whole chain and the release plan (default)
  base   hop 4 only -- what landed in the base since this repo pinned it

The verdicts are advisory. "Matters" is a path classification, not a reading of
the change: a comment fix in first-boot.sh trips it. What this guarantees is
that the gaps were put in front of you, not that closing them is right.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

PLUGIN_REPO = "https://github.com/plow-pbc/hermes-plow-chat"
BASE_REPO = "https://github.com/plow-pbc/plow-hermes-agent"
LIFE_REPO = "https://github.com/plow-pbc/life-assistant-hermes-agent"
PLOW_SLUG = "plow-pbc/plow"
AGENTS_PATH = "api/cloud-agents/agents.json"

REGISTRY_HOST = "public.ecr.aws"
REGISTRY_REPO = "e1h7x4a2/plow-cloud-agents"

REPO_ROOT = Path(__file__).resolve().parent.parent

# Paths in the base repo that reach the image this repo builds. Anything not
# matched is reported but not counted -- see WHY_NOT for the ones that look
# like they should be.
MATTERS = {
    "Dockerfile": "the image itself: upstream hermes-agent digest, PLOW_CHAT_PLUGIN_SHA, layout, ownership",
    "image/seed/config.yaml": "the config every tenant boots with -- model, provider, mcp_servers keys",
    "image/systemd/": "the units that start the gateway and run first boot",
    "image/first-boot.sh": "what runs once on the tenant VM before the gateway starts",
    "image/seed/skills/": "skills baked beside ours in /var/lib/hermes/skills",
    # Shapes what the base's own COPY lines can see, so it changes the built
    # filesystem without appearing in any Dockerfile diff.
    ".dockerignore": "the base's build context -- what its COPY lines can reach",
}

# Not counted, and each for a reason worth stating rather than a silent miss.
WHY_NOT = {
    "image/seed/SOUL.md": "our Dockerfile copies runtime/SOUL.md over it",
    "README.md": "prose",
    "scripts/": "the base repo's own build/CI checks; the image does not run them",
    ".gitignore": "not in the image",
}


# ---------------------------------------------------------------- primitives

def run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def try_run(*args: str, cwd: Path | None = None) -> str | None:
    """Same, but a failure is an answer rather than a crash: None means unknown."""
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


# Beside the checkout's git dir, not in /tmp: mirrors survive reboots and are
# shared by every worktree. `--git-common-dir`, not `.git`: in a worktree that
# is a *file* pointing elsewhere, and one mirror per repo beats one per worktree.
MIRRORS = Path(run("git", "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=REPO_ROOT)) / "release-check-mirrors"


def mirror(url: str, name: str) -> Path:
    """A fresh mirror of `url`. Always fetches: a stale answer is worse than none.

    There is no offline mode. The whole output is "what is the latest", and a
    cached mirror answers that question with yesterday's tips while looking
    exactly like a live run -- a verdict of NO MATERIAL DRIFT computed against a
    week-old main is the single most dangerous thing this could print.
    """
    path = MIRRORS / f"{name}.git"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        run("git", "clone", "--quiet", "--mirror", url, str(path))
    else:
        run("git", "fetch", "--quiet", "origin", "+refs/heads/*:refs/heads/*", cwd=path)
    return path


def is_ancestor(repo: Path, older: str, newer: str) -> bool:
    """Ancestry, never dates. A pin can name a commit that was never on main --
    a PR branch someone published a tag for -- and `older..newer` would then
    report main's whole history as new."""
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", older, newer], cwd=repo, capture_output=True
    ).returncode == 0


def behind(repo: Path, older: str, newer: str) -> int | None:
    """Commits in older..newer, merges included; None when they do not relate."""
    if older == newer:
        return 0
    if not is_ancestor(repo, older, newer):
        return None
    out = try_run("git", "rev-list", "--count", f"{older}..{newer}", cwd=repo)
    return int(out) if out is not None else None


# Commit subjects are attacker-controlled text from repositories this script
# clones. A subject carrying ESC, CR or a newline can repaint or overwrite the
# report -- hiding a whole hop's verdict behind a cursor move -- on a terminal
# whose reader is deciding what to publish. Printable ASCII plus a bounded
# length is all a subject line needs to be.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def sanitize(text: str, limit: int = 120) -> str:
    """Remote text, made safe to print. Never used on anything we compare."""
    clean = _CONTROL.sub("?", text)
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def subject(repo: Path, sha: str) -> str:
    raw = try_run("git", "log", "-1", "--format=%cs %s", sha, cwd=repo)
    return sanitize(raw) if raw else "<unknown commit>"


# ------------------------------------------------------------------ classify

def classify(paths: list[str]) -> dict[str, str]:
    """{path: why it matters} for the paths that reach our image.

    A dict rather than a list: the caller wants the distinct reasons, and the
    same path cannot match two rules anyway.
    """
    hits = {}
    for p in paths:
        for prefix, why in MATTERS.items():
            if p == prefix or p.startswith(prefix):
                hits[p] = why
                break
    return hits


def why_not(path: str) -> str | None:
    for prefix, reason in WHY_NOT.items():
        if path == prefix or path.startswith(prefix):
            return reason
    return None


# ------------------------------------------------------------------- remotes

def http_json(url: str, headers: dict[str, str] | None = None, timeout: int = 20):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def registry_tags(fetch=http_json) -> set[str] | None:
    """Every published tag, or None when the registry could not be asked.

    Anonymous: the repository is public, which is the point of it -- exe.dev
    pulls it with no credential. None rather than an empty set on failure,
    because "could not tell" and "not there" ask different things of the reader,
    and conflating them is how a deploy gets pointed at a missing tag.
    """
    try:
        auth = f"https://{REGISTRY_HOST}/token/?scope=repository:{REGISTRY_REPO}:pull&service={REGISTRY_HOST}"
        token = fetch(auth)["token"]
        data = fetch(
            f"https://{REGISTRY_HOST}/v2/{REGISTRY_REPO}/tags/list",
            {"Authorization": f"Bearer {token}"},
        )
        return set(data.get("tags", []))
    except Exception as e:  # noqa: BLE001 -- every failure is the same answer
        print(f"  (could not reach the registry: {e})", file=sys.stderr)
        return None


def agents_json(ref: str) -> dict | None:
    """plow's pin registry at a ref, via gh. None when gh cannot answer.

    Through `gh` rather than a checkout: plow is not necessarily cloned beside
    this repo, and the pin file is what the API ships, so reading it at an
    arbitrary ref is exactly what the DEPLOYED question needs.
    """
    out = try_run("gh", "api", f"repos/{PLOW_SLUG}/contents/{AGENTS_PATH}?ref={ref}", "--jq", ".content")
    if out is None:
        return None
    try:
        return json.loads(base64.b64decode(out))
    except Exception:  # noqa: BLE001
        return None


def deployed_ref() -> str | None:
    """The commit the last API deploy shipped, from the deploy/api/* tag it pushed.

    deploy-api.sh tags the built commit, so the newest such tag is what
    production is running -- including which agents.json went out with it. The
    tag names are timestamps, so lexical order is chronological order.
    """
    out = try_run("gh", "api", f"repos/{PLOW_SLUG}/git/matching-refs/tags/deploy/api/",
                  "--jq", ".[].object.sha")
    if not out:
        return None
    return out.splitlines()[-1]


def base_dockerfile_pins(repo: Path, sha: str) -> tuple[str | None, str | None]:
    """(upstream hermes-agent digest, plow_chat plugin sha) the base pins at `sha`.

    These are pins the base *inherits* rather than authors: one-line diffs that
    read like nothing and change the runtime under every variant at once.
    """
    text = try_run("git", "show", f"{sha}:Dockerfile", cwd=repo)
    if text is None:
        return (None, None)
    img = re.search(r"^FROM\s+(nousresearch/hermes-agent@sha256:[0-9a-f]{64})", text, re.M)
    plug = re.search(r"^ARG\s+PLOW_CHAT_PLUGIN_SHA=([0-9a-f]{40})", text, re.M)
    return (img.group(1) if img else None, plug.group(1) if plug else None)


def life_pinned_base() -> str:
    """The base commit this repo's Dockerfile names, from the tag half of the FROM.

    The tag is what names a commit; the digest names bytes. Matching the tag is
    what lets any of this talk about git history.
    """
    text = (REPO_ROOT / "Dockerfile").read_text()
    m = re.search(r"^FROM\s+\S*/plow-cloud-agents:base-([0-9a-f]{40})@sha256:[0-9a-f]{64}", text, re.M)
    if not m:
        raise SystemExit("Dockerfile has no `FROM …/plow-cloud-agents:base-<sha>@sha256:<digest>` line")
    return m.group(1)


def newest_published_on_main(repo: Path, tags: set[str] | None, head: str) -> str | None:
    """The newest published base-<sha> that is an ancestor of base main.

    Ancestry-filtered on purpose: the registry holds tags for commits that were
    never on main -- someone pinned a PR branch -- and calling one of those "the
    latest base" ships unreviewed work under a name that reads like a release.
    """
    if tags is None:
        return None
    cands = []
    for t in tags:
        if not t.startswith("base-"):
            continue
        sha = t[len("base-"):]
        if try_run("git", "cat-file", "-e", f"{sha}^{{commit}}", cwd=repo) is None:
            continue   # a tag for the life repo's own commits, or a stray
        if is_ancestor(repo, sha, head):
            cands.append(sha)
    if not cands:
        return None
    # Topological, not by date: committer dates are attacker- and rebase-
    # controlled, and this picks what gets pinned.
    ordered = run("git", "rev-list", "--topo-order", head, cwd=repo).splitlines()
    rank = {s: i for i, s in enumerate(ordered)}
    return min(cands, key=lambda s: rank.get(s, len(ordered)))


# ---------------------------------------------------------------- reporting

class Report:
    def __init__(self) -> None:
        self.gaps: list[str] = []
        self.plan: list[dict[str, str]] = []

    def hop(self, n: int, title: str, current: str, latest: str, count: int | None, verdict: str | None) -> None:
        print(f"── hop {n}: {title}")
        print(f"     CURRENT  {current}")
        print(f"     LATEST   {latest}")
        if count is None:
            # Not "0 behind", and never an UPGRADE recommendation. A pin that is
            # not an ancestor of main is a commit somebody published off a
            # branch: there is no upgrade path to compute, the file lists would
            # describe a diff nobody is proposing, and the honest output is to
            # say so and refuse to rank it. It counts as a gap, so the run still
            # exits non-zero -- an unanswerable hop must never read as a clean
            # one, which is the shape that lets a stale chain look released.
            print("     behind   n/a -- not on the same line of descent")
            print("     VERDICT  DIVERGED -- manual comparison required")
            self.gaps.append(f"hop {n} diverged")
            print()
            return
        print(f"     behind   {count} commit(s)")
        if verdict:
            print(f"     VERDICT  {verdict}")
            self.gaps.append(verdict)
        else:
            print("     VERDICT  current")
        print()

    def unknown(self, n: int, title: str, why: str) -> None:
        """A hop that could not be read at all. Loud, and a gap."""
        print(f"── hop {n}: {title}")
        print(f"     VERDICT  UNKNOWN -- {why}")
        print("     This hop was not checked. Treat the chain as unverified.")
        self.gaps.append(f"hop {n} unknown")
        print()

    def step(self, repo: str, file: str, field: str, target: str, note: str) -> None:
        self.plan.append({"repo": repo, "file": file, "field": field, "target": target, "note": note})


def short(sha: str | None) -> str:
    return (sha or "unknown")[:12]


# --------------------------------------------------------------------- hops

def hop4_base_drift(rep: Report, base: Path, pin: str, head: str, published: str | None, tags: set[str] | None) -> None:
    """What landed in the base since this repo pinned it, and does it reach our image?"""
    print(f"── hop 4: this repo's Dockerfile FROM  <-  the base")
    print(f"     CURRENT  base-{pin}  {subject(base, pin)}")
    print(f"     LATEST   base-{head}  {subject(base, head)}   (base main)")
    if published:
        print(f"     newest published on main: base-{published}")
    print()

    if pin == head:
        print("     VERDICT  current -- the pin is base main.")
        print()
        return

    if not is_ancestor(base, pin, head):
        # Short-circuit, not a warning above an otherwise normal report. The
        # pinned commit is on a branch that never merged -- someone published a
        # tag for it -- so `pinned..main` is not an upgrade path and the MATTERS
        # set computed from it would describe a change nobody is proposing.
        # Printing UPGRADE RECOMMENDED off that is worse than printing nothing:
        # it names a direction that does not exist.
        print("     behind   n/a -- not on the same line of descent")
        print("     VERDICT  DIVERGED -- manual comparison required")
        print()
        print("     The pinned commit is NOT an ancestor of base main: it is a branch")
        print("     commit someone published a tag for. Compare the two by hand and")
        print("     decide what this repo should be building on; there is no upgrade")
        print("     path to compute, so none is offered.")
        print()
        rep.gaps.append("hop 4 diverged")
        return

    # The whole diff, not per-commit name-only unions: a file touched and then
    # reverted inside the range is not drift, and summing commits reports it as
    # if it were. The commit list is separate, and keeps merges -- they are how
    # the base's history actually reads.
    files = (try_run("git", "diff", "--name-only", f"{pin}..{head}", cwd=base) or "").splitlines()
    commits = (try_run("git", "log", "--oneline", f"{pin}..{head}", cwd=base) or "").splitlines()

    print(f"     {len(commits)} commit(s) in pinned..main (merges included):")
    for line in commits:
        print(f"       {sanitize(line)}")
    print()

    hits = classify(files)
    print(f"     net diff touches {len(files)} file(s):")
    for f in sorted(files):
        if f in hits:
            print(f"       MATTERS  {sanitize(f)}")
        else:
            reason = why_not(f)
            print(f"                {sanitize(f)}" + (f"   ({reason})" if reason else ""))
    print()

    reasons = sorted(set(hits.values()))
    if reasons:
        print(f"     VERDICT  life base pin behind ({'; '.join(reasons)})")
        rep.gaps.append("life base pin behind")
    else:
        print("     VERDICT  commits landed, none of them reach this image")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("subcommand", nargs="?", default="all", choices=["all", "base"])
    args = ap.parse_args()

    base = mirror(BASE_REPO, "plow-hermes-agent")
    base_head = run("git", "rev-parse", "main", cwd=base)
    life_pin = life_pinned_base()
    tags = registry_tags()
    published = newest_published_on_main(base, tags, base_head)

    rep = Report()
    # Once a plugin bump is planned, base main is no longer `base_head` by the
    # time anything downstream is opened -- that merge creates a new tip. Naming
    # today's sha in step 2 would send the next reader to publish a commit that
    # is already superseded, which is exactly the mistake this script exists to
    # catch. So downstream targets go symbolic the moment an upstream step
    # enters the plan.
    def base_target() -> str:
        return "<base main after step 1 merges>" if rep.plan else base_head

    if args.subcommand == "base":
        print(f"base repo   {BASE_REPO}\n")
        hop4_base_drift(rep, base, life_pin, base_head, published, tags)
        return 1 if rep.gaps else 0

    plugin = mirror(PLUGIN_REPO, "hermes-plow-chat")
    plugin_head = run("git", "rev-parse", "main", cwd=plugin)

    print("PIN CHAIN\n")

    # hop 1 -- the plugin the base fetches
    _, base_plugin_pin = base_dockerfile_pins(base, base_head)
    n = behind(plugin, base_plugin_pin, plugin_head) if base_plugin_pin else None
    verdict = None
    if base_plugin_pin and n:
        verdict = f"plugin pin behind upstream by {n}"
    rep.hop(1, "plow-hermes-agent ARG PLOW_CHAT_PLUGIN_SHA  <-  hermes-plow-chat main",
            f"{short(base_plugin_pin)}  {subject(plugin, base_plugin_pin) if base_plugin_pin else ''}",
            f"{short(plugin_head)}  {subject(plugin, plugin_head)}", n, verdict)
    if base_plugin_pin and n:
        print("     the plugin commits it is missing:")
        for line in (try_run("git", "log", "--oneline", f"{base_plugin_pin}..{plugin_head}", cwd=plugin) or "").splitlines():
            print(f"       {sanitize(line)}")
        print()
        rep.step("plow-pbc/plow-hermes-agent", "Dockerfile", "ARG PLOW_CHAT_PLUGIN_SHA", plugin_head,
                 "moves the plugin; publishes nothing on its own")

    # hop 2 -- is base main published at all?
    if tags is None:
        rep.unknown(2, "registry  <-  plow-hermes-agent main",
                    "the registry could not be reached; check base-<main sha> by hand")
    elif f"base-{base_head}" in tags:
        rep.hop(2, "registry  <-  plow-hermes-agent main",
                f"base-{short(base_head)} IS published", f"base-{short(base_head)}", 0, None)
    else:
        n2 = behind(base, published, base_head) if published else None
        rep.hop(2, "registry  <-  plow-hermes-agent main",
                f"base-{short(published)} (newest published on main)",
                f"base-{short(base_head)}  {subject(base, base_head)}", n2,
                "base main unpublished -- needs a plow exe.hermes.revision bump")
        rep.step("plow-pbc/plow", AGENTS_PATH, ".exe.hermes.revision", base_target(),
                 "this merge IS the publish; nothing else pushes a base-<sha> tag")

    # hops 3 and 5 -- what plow pins, and what it has actually deployed
    live = agents_json("main")
    dep_ref = deployed_ref()
    dep = agents_json(dep_ref) if dep_ref else None

    if live is None:
        rep.unknown(3, "plow .exe.hermes.revision  <-  the base",
                    "plow's agents.json could not be read (is `gh` installed and authenticated?)")
        rep.unknown(5, "plow .exe.life.revision  <-  this repo's main",
                    "plow's agents.json could not be read (is `gh` installed and authenticated?)")
    else:
        hermes_pin = live["exe"]["hermes"]["revision"]
        life_ref_pinned = live["exe"]["life"]["revision"]

        n3 = behind(base, hermes_pin, base_head)
        rep.hop(3, "plow .exe.hermes.revision  <-  the base",
                f"{short(hermes_pin)}  {subject(base, hermes_pin)}",
                f"{short(base_head)}  {subject(base, base_head)}   (base main)", n3,
                f"hermes pin behind main by {n3}" if n3 else None)

        life = mirror(LIFE_REPO, "life-assistant-hermes-agent")
        life_head = run("git", "rev-parse", "main", cwd=life)
        n5 = behind(life, life_ref_pinned, life_head)
        rep.hop(5, "plow .exe.life.revision  <-  this repo's main",
                f"{short(life_ref_pinned)}  {subject(life, life_ref_pinned)}",
                f"{short(life_head)}  {subject(life, life_head)}", n5,
                f"life pin behind main by {n5}" if n5 else None)

        if dep is None:
            print("     DEPLOYED  UNKNOWN -- no deploy/api/* tag readable, so what is actually")
            print("               live could not be established. Not the same as up to date.")
            rep.gaps.append("deployed state unknown")
            print()
        else:
            print(f"     DEPLOYED  as of plow {short(dep_ref)}: "
                  f"hermes={short(dep['exe']['hermes']['revision'])} "
                  f"life={short(dep['exe']['life']['revision'])}")
            if dep != live:
                print("               main is ahead of the last deploy -- an API deploy is pending")
                rep.gaps.append("deploy pending")
            print()

    # hop 4 -- the base drift analysis
    hop4_base_drift(rep, base, life_pin, base_head, published, tags)
    if "life base pin behind" in rep.gaps:
        rep.step("plow-pbc/life-assistant-hermes-agent", "Dockerfile", "FROM base-<sha>@sha256:<digest>",
                 base_target(), "the tag must be PUBLISHED first -- read the digest off the registry")
        rep.step("plow-pbc/plow", AGENTS_PATH, ".exe.life.revision", "<that PR's merge SHA>",
                 "does not exist until the step above merges")

    # hop 6 -- the upstream runtime, reported only
    up_pinned, _ = base_dockerfile_pins(base, life_pin)
    up_head, _ = base_dockerfile_pins(base, base_head)
    print("── hop 6: the upstream runtime the base inherits (reported, not checked against a 'latest')")
    print(f"     at our pinned base  {up_pinned}")
    print(f"     at base main        {up_head}"
          f"   {'UNCHANGED' if up_pinned == up_head else '*** MOVED ***'}")
    print("     No upstream lookup: nousresearch/hermes-agent's tags move, and asking")
    print("     'what does v2026.8.18 resolve to today' answers a question nobody should")
    print("     act on -- the digest is pinned precisely so it does not follow a tag.")
    print()

    # ------------------------------------------------------------- the plan
    print("=" * 78)
    if not rep.plan:
        print("RELEASE PLAN: nothing to open." if not rep.gaps
              else f"RELEASE PLAN: nothing to open, but: {'; '.join(rep.gaps)}")
        return 1 if rep.gaps else 0

    print("RELEASE PLAN -- open these in this order. Each target does not exist until")
    print("the step above it merges and its build publishes.\n")
    for i, s in enumerate(rep.plan, 1):
        print(f"  {i}. {s['repo']}")
        print(f"       {s['file']}   {s['field']}")
        print(f"       -> {s['target']}")
        print(f"       ({s['note']})")
    print("\n  last. Dispatch Deploy API in plow-pbc/plow. The registry ships inside the")
    print("        API image, so nothing above reaches a tenant until this runs.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
