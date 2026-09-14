#!/usr/bin/env python3
"""priorities.py -- the household to-do list's manifest, and the card it feeds.

One file, `/opt/data/ld/priorities.json`, holds the list's name, the open
items IN RANK ORDER, the rules the assistant has learned about how the owner
wants things ranked, and what got done. Every mutation is a subcommand here so
the model decides only three things -- the order, a reason chip, and a rule --
and never rewrites the file by hand. Read-modify-write under
`ld-shared/scripts/exclusive_lock.py`; written tmp+fchmod+fsync+os.replace,
published mode 600.

    show | add <text> [--why T] | done <id> | remove <id> | rename <name>
    rule add <sentence> | rule remove <n> | rank <id>... | why <id> <text>
    post [--dry-run]
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import html
import json
import os
import secrets
import sys
import tempfile

_SCRIPTS_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(_SCRIPTS_DIR, "..", "..", "ld-shared", "scripts"))
from exclusive_lock import exclusive_lock  # noqa: E402
import post_to_kiosk  # noqa: E402

MANIFEST = "/opt/data/ld/priorities.json"
MESSAGE_FILE = "/opt/data/ld/priorities-text"
WALL_READY = "/opt/data/ld/setup-complete"
DEFAULT_NAME = "Our to-do list"
SHOWN = 6
DONE_KEPT = 50


def load() -> dict:
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            m = json.load(f)
    except FileNotFoundError:
        return {"name": DEFAULT_NAME, "rules": [], "items": [], "done": []}
    for key, default in (("name", DEFAULT_NAME), ("rules", []), ("items", []), ("done", [])):
        m.setdefault(key, default)
    return m


def save(m: dict) -> None:
    directory = os.path.dirname(MANIFEST)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".priorities-", suffix=".json")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(m, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, MANIFEST)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _today() -> str:
    return dt.date.today().isoformat()


def _find(m: dict, item_id: str) -> int:
    for i, item in enumerate(m["items"]):
        if item["id"] == item_id:
            return i
    sys.exit(f"refusing: unknown item id {item_id!r}; run `show` for the open ids")


def _new_id(m: dict) -> str:
    taken = {i["id"] for i in m["items"]} | {d["id"] for d in m["done"]}
    while True:
        candidate = secrets.token_hex(2)
        if candidate not in taken:
            return candidate


def _text(value: str, what: str) -> str:
    value = " ".join(value.split())
    if not value:
        sys.exit(f"refusing: {what} is empty")
    return value


def compose(m: dict) -> str:
    style = (
        "<style>"
        ".pr-list{flex:1;min-height:0;display:flex;flex-direction:column;justify-content:flex-start}"
        ".pr-empty{text-align:center;color:var(--muted);font-size:var(--t-card)}"
        ".pr-item{display:grid;grid-template-columns:2ch 1fr;column-gap:10px;align-items:baseline;padding:8px 0}"
        ".pr-item + .pr-item{border-top:1px solid var(--hair)}"
        ".pr-n{font-family:var(--ff-mono);font-weight:500;font-size:13px;letter-spacing:0.06em;color:var(--accent-ink,var(--clay-ink));text-align:right}"
        ".pr-text{font-family:var(--ff-body);font-weight:500;font-size:18px;line-height:1.15;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
        ".pr-why{grid-column:2;font-family:var(--ff-mono);font-weight:var(--cap-weight);font-size:var(--cap-size);letter-spacing:var(--cap-tracking);text-transform:uppercase;color:var(--faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
        "</style>"
    )
    items = m["items"][:SHOWN]
    if not items:
        return f'{style}<div class="pr-list"><div class="pr-empty">Nothing on the list</div></div>'
    rows = []
    for n, item in enumerate(items, 1):
        why = item.get("why")
        chip = f'<span class="pr-why">{html.escape(why, quote=True)}</span>' if why else ""
        rows.append(
            f'<div class="pr-item"><span class="pr-n">{n}</span>'
            f'<span class="pr-text">{html.escape(item["text"], quote=True)}</span>{chip}</div>'
        )
    return f'{style}<div class="pr-list">{"".join(rows)}</div>'


def _mutate(fn):
    """Run `fn(manifest)` under the lock; save; print whatever it returns."""
    with exclusive_lock(MANIFEST, "refusing to write"):
        m = load()
        out = fn(m)
        save(m)
    if out:
        print(out)


def cmd_add(a):
    def go(m):
        item = {"id": _new_id(m), "text": _text(a.text, "item text"), "added": _today()}
        if a.why:
            item["why"] = _text(a.why, "why")
        m["items"].append(item)
        return item["id"]

    _mutate(go)


def cmd_done(a):
    def go(m):
        item = m["items"].pop(_find(m, a.id))
        item["done"] = _today()
        m["done"] = (m["done"] + [item])[-DONE_KEPT:]

    _mutate(go)


def cmd_remove(a):
    def go(m):
        m["items"].pop(_find(m, a.id))

    _mutate(go)


def cmd_rename(a):
    def go(m):
        m["name"] = _text(a.name, "list name")

    _mutate(go)


def cmd_rule(a):
    def go(m):
        if a.action == "add":
            m["rules"].append(_text(a.value, "rule"))
        else:
            n = int(a.value)
            if not 1 <= n <= len(m["rules"]):
                sys.exit(f"refusing: no rule {n}; there are {len(m['rules'])}")
            del m["rules"][n - 1]

    _mutate(go)


def cmd_rank(a):
    def go(m):
        by_id = {i["id"]: i for i in m["items"]}
        unknown = [i for i in a.ids if i not in by_id]
        if unknown:
            sys.exit(f"refusing: unknown item id(s) {unknown}; run `show` for the open ids")
        if sorted(a.ids) != sorted(by_id):
            sys.exit(
                "refusing: rank must list every open item exactly once "
                f"(got {len(a.ids)}, have {len(by_id)}); run `show` for the open ids"
            )
        m["items"] = [by_id[i] for i in a.ids]

    _mutate(go)


def cmd_why(a):
    def go(m):
        item = m["items"][_find(m, a.id)]
        text = " ".join(a.text.split())
        if text:
            item["why"] = text
        else:
            item.pop("why", None)

    _mutate(go)


def cmd_show(_a):
    print(json.dumps(load(), indent=2, ensure_ascii=False))


def cmd_post(a):
    if not os.path.exists(WALL_READY):
        print("NO WALL: the list is kept, nothing was posted (ld-wall-setup has not finished)")
        return
    m = load()
    fd = os.open(MESSAGE_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(compose(m))
    post_to_kiosk.MESSAGE_FILE = MESSAGE_FILE
    post_to_kiosk.CARD = "6"
    post_to_kiosk.BODY_TYPE = "priorities"
    post_to_kiosk.TITLE = m["name"]
    sys.argv = ["post_priorities.py"] + (["--dry-run"] if a.dry_run else [])
    post_to_kiosk.main()


def main(argv=None):
    p = argparse.ArgumentParser(description="The household to-do list's manifest and card.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add"); s.add_argument("text"); s.add_argument("--why", default=""); s.set_defaults(fn=cmd_add)  # noqa: E702,E501
    s = sub.add_parser("done"); s.add_argument("id"); s.set_defaults(fn=cmd_done)  # noqa: E702
    s = sub.add_parser("remove"); s.add_argument("id"); s.set_defaults(fn=cmd_remove)  # noqa: E702
    s = sub.add_parser("rename"); s.add_argument("name"); s.set_defaults(fn=cmd_rename)  # noqa: E702
    s = sub.add_parser("rule"); s.add_argument("action", choices=("add", "remove")); s.add_argument("value"); s.set_defaults(fn=cmd_rule)  # noqa: E702,E501
    s = sub.add_parser("rank"); s.add_argument("ids", nargs="+"); s.set_defaults(fn=cmd_rank)  # noqa: E702
    s = sub.add_parser("why"); s.add_argument("id"); s.add_argument("text"); s.set_defaults(fn=cmd_why)  # noqa: E702
    s = sub.add_parser("show"); s.set_defaults(fn=cmd_show)  # noqa: E702
    s = sub.add_parser("post"); s.add_argument("--dry-run", action="store_true"); s.set_defaults(fn=cmd_post)  # noqa: E702
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
