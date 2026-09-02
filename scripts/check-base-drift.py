#!/usr/bin/env python3
"""What has landed in the base image since this repo pinned it, and does it matter here?

Usage: check-base-drift.py [--offline]   → prints a report, exits 0 on no drift

This repo's Dockerfile builds FROM an immutable `base-<sha>` tag naming one
commit of plow-pbc/plow-hermes-agent. That repo ships no CI of its own, so
nothing announces a base change and nothing pushes a tag for it: a commit there
becomes a published `base-<sha>` only when plow's `.exe.hermes.revision` is
moved to it and merged. Both halves of that are silent from in here, which is
why this exists — the pin can be months stale with no signal anywhere.

Two questions, in order, because the second is worthless without the first:

  1. Is there anything in `pinned..main` that reaches the image this repo
     builds? Most base commits do not. README prose does not, and neither does
     the seed SOUL.md -- our Dockerfile copies runtime/SOUL.md straight over it,
     so a base rewrite of that file lands in the layer and is then overwritten.
  2. If so, is the newer base PUBLISHED? An upgrade cannot be pinned before the
     tag exists, and making it exist is a pull request in a third repository.

The verdict is deliberately advisory. "Matters" is a path classification, not a
reading of the change: a one-word comment fix in first-boot.sh trips it. The
report prints the commits and the files so the reader decides; what the script
guarantees is that they were shown, not that they were right.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

BASE_REPO = "https://github.com/plow-pbc/plow-hermes-agent"
REGISTRY_HOST = "public.ecr.aws"
REGISTRY_REPO = "e1h7x4a2/plow-cloud-agents"

REPO_ROOT = Path(__file__).resolve().parent.parent

# Paths in the base repo that reach the image this repo builds. Anything not
# matched here is reported but not counted -- see WHY_NOT below for the ones
# that look like they should be.
MATTERS = {
    "Dockerfile": "the image itself: upstream hermes-agent digest, PLOW_CHAT_PLUGIN_SHA, layout, ownership",
    "image/seed/config.yaml": "the config every tenant boots with -- model, provider, mcp_servers keys",
    "image/systemd/": "the units that start the gateway and run first boot",
    "image/first-boot.sh": "what runs once on the tenant VM before the gateway starts",
    "image/seed/skills/": "skills baked beside ours in /var/lib/hermes/skills",
}

# Not counted, and each for a reason worth stating rather than a silent miss.
WHY_NOT = {
    "image/seed/SOUL.md": "our Dockerfile copies runtime/SOUL.md over it",
    "README.md": "prose",
    "scripts/": "the base repo's own build/CI checks; the image does not run them",
    ".gitignore": "not in the image",
    ".dockerignore": "build context only",
}


def run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


# Beside the checkout's git dir, not in /tmp: a mirror survives reboots and is
# shared by every worktree. `--git-common-dir`, not `.git`: in a worktree that
# is a *file* pointing elsewhere, and every worktree of this repo should share
# one mirror rather than each cloning the base repo again.
CACHE = Path(run("git", "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=REPO_ROOT)) / "base-drift-mirror"


def pinned_sha() -> str:
    """The base commit this repo's Dockerfile names, from the tag half of the FROM."""
    text = (REPO_ROOT / "Dockerfile").read_text()
    # Tag and digest both, but the tag is what names a commit; the digest names
    # bytes. Matching the tag is what lets this talk about git history at all.
    m = re.search(r"^FROM\s+\S*/plow-cloud-agents:base-([0-9a-f]{40})@sha256:[0-9a-f]{64}", text, re.M)
    if not m:
        raise SystemExit("Dockerfile has no `FROM …/plow-cloud-agents:base-<sha>@sha256:<digest>` line")
    return m.group(1)


def mirror(offline: bool) -> Path:
    if not CACHE.exists():
        if offline:
            raise SystemExit(f"--offline and no mirror at {CACHE}; run once without it")
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        run("git", "clone", "--quiet", "--mirror", BASE_REPO, str(CACHE))
    elif not offline:
        run("git", "fetch", "--quiet", "origin", "+refs/heads/*:refs/heads/*", cwd=CACHE)
    return CACHE


def upstream_pins(sha: str) -> tuple[str, str]:
    """The upstream image digest and plugin commit the base's own Dockerfile pins.

    Drift here is drift the base repo inherited rather than authored: a bumped
    `nousresearch/hermes-agent` digest changes the runtime under every variant
    at once, and it is a one-line diff that reads like nothing.
    """
    try:
        text = run("git", "show", f"{sha}:Dockerfile", cwd=CACHE)
    except subprocess.CalledProcessError:
        return ("<no Dockerfile>", "<no Dockerfile>")
    image = re.search(r"^FROM\s+(nousresearch/hermes-agent@sha256:[0-9a-f]{64})", text, re.M)
    plugin = re.search(r"^ARG\s+PLOW_CHAT_PLUGIN_SHA=([0-9a-f]{7,40})", text, re.M)
    return (image.group(1) if image else "<unpinned>", plugin.group(1) if plugin else "<unpinned>")


def classify(paths: list[str]) -> list[tuple[str, str]]:
    """(path, why it matters) for the paths that reach our image."""
    hits = []
    for p in paths:
        for prefix, why in MATTERS.items():
            if p == prefix or p.startswith(prefix):
                hits.append((p, why))
                break
    return hits


def published(tag: str) -> bool | None:
    """Is `base-<sha>` in the registry? None when the registry could not be asked.

    Anonymous: the repository is public, which is the whole point of it -- exe.dev
    pulls it with no credential. None rather than False on a failure, because
    "we could not tell" and "it is not there" ask for different things from the
    reader, and conflating them is how a deploy gets pointed at a missing tag.
    """
    try:
        auth = f"https://{REGISTRY_HOST}/token/?scope=repository:{REGISTRY_REPO}:pull&service={REGISTRY_HOST}"
        with urllib.request.urlopen(auth, timeout=20) as r:
            token = json.load(r)["token"]
        req = urllib.request.Request(
            f"https://{REGISTRY_HOST}/v2/{REGISTRY_REPO}/tags/list",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return tag in json.load(r).get("tags", [])
    except Exception as e:  # noqa: BLE001 -- any failure is the same answer: unknown
        print(f"  (could not reach the registry: {e})", file=sys.stderr)
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="use the cached mirror and skip the registry")
    args = ap.parse_args()

    pin = pinned_sha()
    mirror(args.offline)
    head = run("git", "rev-parse", "main", cwd=CACHE)

    print(f"base repo   {BASE_REPO}")
    print(f"pinned      {pin}  {run('git', 'log', '-1', '--format=%cs %s', pin, cwd=CACHE)}")
    print(f"base main   {head}  {run('git', 'log', '-1', '--format=%cs %s', head, cwd=CACHE)}")
    print()

    if pin == head:
        print("VERDICT: NO MATERIAL DRIFT -- the pin is base main.")
        return 0

    # An ancestry check, not a date comparison: a pin can name a commit that
    # was never on main (a PR branch someone published a tag for), and
    # `pinned..main` would then silently list main's whole history as "new".
    on_main = subprocess.run(
        ["git", "merge-base", "--is-ancestor", pin, head], cwd=CACHE, capture_output=True
    ).returncode == 0
    if not on_main:
        print("!! the pinned commit is NOT an ancestor of base main.")
        print("   It is a branch commit someone published a tag for. Everything below is")
        print("   the diff against main's history, not an upgrade path -- read it as such.")
        print()

    shas = run("git", "rev-list", "--reverse", "--no-merges", f"{pin}..{head}", cwd=CACHE).split()
    material: list[tuple[str, str, list[tuple[str, str]]]] = []

    print(f"{len(shas)} non-merge commit(s) in pinned..main:")
    print()
    for sha in shas:
        subject = run("git", "log", "-1", "--format=%s", sha, cwd=CACHE)
        files = run("git", "show", "--pretty=", "--name-only", sha, cwd=CACHE).splitlines()
        hits = classify(files)
        flag = "MATTERS" if hits else "       "
        print(f"  {flag}  {sha[:7]}  {subject}")
        for f in files:
            note = ""
            if not any(f == p for p, _ in hits):
                for prefix, why in WHY_NOT.items():
                    if f == prefix or f.startswith(prefix):
                        note = f"   ({why})"
                        break
            print(f"             {f}{note}")
        if hits:
            material.append((sha, subject, hits))
        print()

    old_img, old_plug = upstream_pins(pin)
    new_img, new_plug = upstream_pins(head)
    print("upstream pins the base itself carries:")
    print(f"  hermes-agent  pinned: {old_img}")
    print(f"                  main: {new_img}   {'UNCHANGED' if old_img == new_img else '*** MOVED ***'}")
    print(f"  plow_chat     pinned: {old_plug}")
    print(f"                  main: {new_plug}   {'UNCHANGED' if old_plug == new_plug else '*** MOVED ***'}")
    print()

    reasons: list[str] = []
    if old_img != new_img:
        reasons.append("the upstream hermes-agent digest moved")
    if old_plug != new_plug:
        reasons.append("the plow_chat plugin pin moved")
    seen: set[str] = set()
    for _, _, hits in material:
        for _, why in hits:
            if why not in seen:
                seen.add(why)
                reasons.append(why)

    tag = f"base-{head}"
    if args.offline:
        print(f"registry: not checked (--offline). Confirm {tag} before pinning it.")
    else:
        state = published(tag)
        if state is True:
            print(f"registry: {tag} IS PUBLISHED -- it can be pinned in the Dockerfile now.")
        elif state is False:
            print(f"registry: {tag} is NOT published.")
            print( "          plow-hermes-agent has no CI; publishing it means a plow PR moving")
            print( "          .exe.hermes.revision to that commit and merging, THEN this bump.")
        else:
            print(f"registry: unknown -- check {tag} by hand before pinning it.")
    print()

    if reasons:
        print(f"VERDICT: UPGRADE RECOMMENDED ({'; '.join(reasons)})")
        return 1
    print("VERDICT: NO MATERIAL DRIFT (commits landed, none of them reach this image)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
