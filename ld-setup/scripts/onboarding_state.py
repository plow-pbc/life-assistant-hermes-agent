#!/usr/bin/env python3
"""onboarding_state.py -- what this turn does, decided here rather than read.

Reads the config and the answers this message carried, and prints ONE JSON
object saying what the turn does:

    {"missing": ["weather.location", "sports.followed", "calendar.sources"],
     "ask": "city",                 # or teams / calendars / name / null
     "write_now": ["family.owner.name", "weather.location"],
     "defer": [],                   # keys held back for the next turn
     "intro_due": false,
     "latch": "unconfigured"}       # or configured / connected

Every rule that used to live in prose lives here: the key order, what is
written now and what is held back, when the introduction is due, and whether
the calendars can be asked about at all. The sheet presents the result; it does
not re-derive it.

That split is the point. The same decision written as a table of turn shapes
grew a hole every time it was extended -- a step with no row, a resume between
two rows, an exit a row did not describe -- and every hole reached an owner as
a blocking `❓`, because a turn that cannot find its own shape improvises one.
Prose can be read three ways on three turns. This cannot.

INPUT is a JSON object on stdin, or in the file named by --input:

    {"answers": {"name": "Mary", "city": "Mountain View, California",
                 "teams": [...], "calendars": [...]},
     "carried": {"name": "Mary"},
     "listing": true}

`answers` holds only what THIS message actually carried -- the turn's own
reading of what the owner just said, and nothing recalled from earlier. It is
staged by the file tool, never composed into a shell command: those values are
the owner's own words, and a name with a quote in it is not a syntax error to
argue with, it is a person.

`carried` holds answers from EARLIER turns that are not in the config yet --
in practice the one deferred name. The split is what makes "was the name
learned this turn" decidable, and that question is what makes the introduction
one-time: without it, a name deferred on Monday looks freshly learned on
Tuesday, the introduction goes out again, and the deferral never resolves
because the same turn keeps holding it back.

`listing` says a calendar listing came back this turn, which is the difference
between a relay that is configured and one that is answering.

Standard library only, and the config's own key order is the ask order.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from latch_status import config_path, relay_configured  # noqa: E402

# The order is the config's own, and it is the only order. "Ask the first
# missing key" is undecidable without it, and when it was stated in prose
# instead a resumed conversation was asked for a city it had already stored.
ORDER = [
    ("name", "family.owner.name"),
    ("city", "weather.location"),
    ("teams", "sports.followed"),
    ("calendars", "calendar.sources"),
]

CONFIG = "/opt/data/ld/config.json"


def has_key(config, dotted):
    """True when the KEY is present, whatever its value.

    Present-but-empty is answered: `sports.followed: []` is "none", a real
    answer, and re-asking it is the failure the config exists to prevent.
    """
    node = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def latch_state(config_yaml_text, listing):
    """`connected` when a listing came back, else configured / unconfigured.

    A listing is the only proof a Mac answered. Configured-but-silent is a Mac
    that is asleep, which is not a state the owner is told about.
    """
    if listing:
        return "connected"
    return "configured" if relay_configured(config_yaml_text) else "unconfigured"


def decide(config, answers, latch, carried=None):
    """The whole of the turn's shape, from the config and what is in hand."""
    carried = carried or {}
    in_hand = {key: answers.get(key, carried.get(key)) for key, _ in ORDER}
    held = [key for key, _ in ORDER if in_hand.get(key) is not None]
    known = {dotted for key, dotted in ORDER
             if has_key(config, dotted) or in_hand.get(key) is not None}
    missing = [dotted for _, dotted in ORDER if dotted not in known]

    # Calendars are only askable where a relay can answer: the owner never
    # types one, so with no Mac reachable there is nothing to ask and the
    # conversation is as finished as it can get.
    askable = [dotted for dotted in missing
               if dotted != "calendar.sources" or latch == "connected"]
    ask = next((key for key, dotted in ORDER if dotted in askable), None)

    # The introduction is due on the turn the name is LEARNED. A name already
    # in the config means it has been sent -- nothing records whether it
    # actually was, deliberately, and re-introducing yourself to someone who
    # has been talking to you for a week is the error an owner notices.
    # `answers`, not `in_hand`: a name CARRIED from an earlier turn was learned
    # then, and the introduction went with it. Reading a carried name as freshly
    # learned would re-introduce you every turn and defer the write forever.
    intro_due = answers.get("name") is not None and not has_key(config, "family.owner.name")

    # Everything held is written now, with ONE exception: the name on the turn
    # that sends the introduction, because the introduction is one-time and a
    # crash between the write and the message would skip it for good. That
    # exception lapses when the turn asks nothing -- nothing is coming back to
    # carry the name, so deferring it means never writing it at all.
    dotted_held = [dotted for key, dotted in ORDER if key in held]
    defer, write_now = [], list(dotted_held)
    if intro_due and ask is not None and "family.owner.name" in write_now:
        write_now.remove("family.owner.name")
        defer = ["family.owner.name"]

    return {"missing": missing, "ask": ask, "write_now": write_now,
            "defer": defer, "intro_due": intro_due, "latch": latch}


def read_json(path):
    if path is None:
        text = sys.stdin.read()
    else:
        with open(path) as handle:
            text = handle.read()
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"refusing to decide: the staged answers are not JSON: {exc}") from None


def main(argv=None, env=None, config_path_override=None):
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env

    staged_path = None
    if argv:
        if argv[0] != "--input" or len(argv) != 2:
            raise SystemExit("usage: onboarding_state.py [--input <staged.json>]")
        staged_path = argv[1]

    staged = read_json(staged_path)
    if not isinstance(staged, dict):
        raise SystemExit("refusing to decide: the staged answers are not an object")
    answers = staged.get("answers") or {}
    carried = staged.get("carried") or {}
    if not isinstance(answers, dict) or not isinstance(carried, dict):
        raise SystemExit("refusing to decide: `answers`/`carried` is not an object")

    path = config_path_override or CONFIG
    try:
        with open(path) as handle:
            config = json.load(handle)
    except FileNotFoundError:
        config = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"refusing to decide: {exc}") from None
    if not isinstance(config, dict):
        raise SystemExit("refusing to decide: config.json is not an object")

    try:
        with open(config_path(env)) as handle:
            config_yaml = handle.read()
    except OSError:
        config_yaml = ""

    json.dump(decide(config, answers,
                     latch_state(config_yaml, bool(staged.get("listing"))), carried),
              sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
