"""life_tools -- the Life assistant's first-class tools.

The model picks actions from its tools list, not from skill prose, so the
capabilities it must reach for on turn one are registered here as tools. Each
row wraps one of this image's skill scripts and runs it as a subprocess; the
description on each row is the routing lever and is quoted from the spec.
"""
from __future__ import annotations

import contextlib
import json
import subprocess
import sys
from dataclasses import dataclass
from functools import partial

SKILLS = "/opt/hermes/skills"
TOOLSET = "life"

# The shared writer every producer publishes through.
sys.path.append(f"{SKILLS}/ld-shared/scripts")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    argv: object                # callable(args) -> list[str] | str (an error message)
    script: str = ""            # the one script this row runs
    handoff: str | None = None  # a fixed file the handler writes `text` to first
    resolve: object = None      # callable(args) -> (script, handoff), for a row that varies per call

    @property
    def schema(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    def target(self, args: dict) -> tuple[str, str | None]:
        """The script to run and the handoff to write first, for THIS call."""
        return self.resolve(args) if self.resolve else (self.script, self.handoff)


TODO_ACTIONS = ("show", "add", "done", "remove", "rename", "rank", "rule_add", "rule_remove", "why", "post")
_TODO_REQUIRED = {"add": "text", "done": "id", "remove": "id", "rename": "name",
                  "rule_add": "rule", "rule_remove": "rule", "rank": "ids", "why": "id"}


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def todo_argv(args: dict):
    action = args.get("action")
    if action not in TODO_ACTIONS:
        return f"unknown action {action!r}; one of {', '.join(TODO_ACTIONS)}"
    need = _TODO_REQUIRED.get(action)
    # `ids` is gated on presence, not truthiness: `rank` with no ids left is the
    # valid order once the last item is done, and priorities.py accepts it.
    if need and (args.get(need) is None if need == "ids" else not _clean(args.get(need))):
        return f"{need} is required for {action}"
    if action == "add":
        why = _clean(args.get("why"))
        return ["add", _clean(args["text"]), *(["--why", why] if why else [])]
    if action in ("done", "remove"):
        return [action, _clean(args["id"])]
    if action == "rename":
        return ["rename", _clean(args["name"])]
    if action == "rule_add":
        return ["rule", "add", _clean(args["rule"])]
    if action == "rule_remove":
        return ["rule", "remove", _clean(args["rule"])]
    if action == "rank":
        return ["rank", *[_clean(i) for i in args["ids"]]]
    if action == "why":
        return ["why", _clean(args["id"]), _clean(args.get("why"))]
    if action == "post":
        return ["post", "--dry-run"] if args.get("dry_run") else ["post"]
    return ["show"]


HOUSEHOLD_TODO = Tool(
    name="household_todo",
    description=(
        "The household to-do list shown on the kitchen wall. Any todo, task, or "
        "'remind me to…' from the owner or a trusted household member goes here, on "
        "turn one, before any Mac tool. It RECORDS the item; it never carries the item "
        "out, looks anything up, or sends anything to anyone. Apple Reminders only when "
        "the owner names that app or their iPhone. Actions: show, add, done, remove, "
        "rename, rank, rule_add, rule_remove, why, post."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(TODO_ACTIONS)},
            "text": {"type": "string", "description": "the item, for add"},
            "id": {"type": "string", "description": "an item id from show, for done/remove/why"},
            "ids": {"type": "array", "items": {"type": "string"}, "description": "every open id in the new order, for rank"},
            "name": {"type": "string", "description": "the list's new name, for rename"},
            "rule": {"type": "string", "description": "a ranking rule sentence for rule_add, or its 1-based number for rule_remove"},
            "why": {"type": "string", "description": "a short reason chip for add/why; empty clears"},
            "dry_run": {"type": "boolean", "description": "for post: preview without sending"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
    script=f"{SKILLS}/ld-priorities/scripts/priorities.py",
    argv=todo_argv,
)


# card -> (poster wrapper, the fixed handoff file that wrapper reads). Card 1's
# calendar-nudge producer owns its own JSON handoff and card 6 posts through
# household_todo, so neither is here.
CARDS = {
    "alert":       (f"{SKILLS}/ld-morning-triage/scripts/post_alert.py",    "/var/lib/hermes/ld/morning-triage-text"),
    "affirmation": (f"{SKILLS}/ld-morning-updates/scripts/post_message.py", "/var/lib/hermes/ld/morning-updates-text"),
    "weather":     (f"{SKILLS}/ld-weather/scripts/post_weather.py",         "/var/lib/hermes/ld/weather-text"),
    "digest":      (f"{SKILLS}/ld-weekly-digest/scripts/post_digest.py",    "/var/lib/hermes/ld/weekly-digest-text"),
    "sports":      (f"{SKILLS}/ld-sports/scripts/post_sports.py",           "/var/lib/hermes/ld/sports-text"),
}


def card_argv(args: dict):
    card = args.get("card")
    if card not in CARDS:
        return f"card must be one of {', '.join(CARDS)}"
    if not str(args.get("text") or "").strip():
        return "text is required"
    return ["--dry-run"] if args.get("dry_run") else []


KIOSK_POST_CARD = Tool(
    name="kiosk_post_card",
    description=(
        "Post one card to the household wall. Use for the scheduled weather, sports, "
        "alert, affirmation and digest cards after composing the text or tile per that "
        "card's skill. One call replaces writing the handoff file and running the poster."
    ),
    parameters={
        "type": "object",
        "properties": {
            "card": {"type": "string", "enum": list(CARDS)},
            "text": {"type": "string", "description": "the composed card text or self-contained HTML tile"},
            "dry_run": {"type": "boolean", "description": "preview the request without sending"},
        },
        "required": ["card", "text"],
        "additionalProperties": False,
    },
    argv=card_argv,
    resolve=lambda args: CARDS[args["card"]],
)


def run(tool: Tool, args: dict, **_kwargs) -> str:
    args = args or {}
    argv = tool.argv(args)
    if isinstance(argv, str):
        return json.dumps({"ok": False, "error": argv})
    script, handoff = tool.target(args)  # argv validated the card, so CARDS has it
    with _handoff_lock(handoff):
        if handoff:
            _write_handoff(handoff, str(args["text"]).strip())
        try:
            proc = subprocess.run([sys.executable, script, *argv],
                                  capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return json.dumps({"ok": False, "error": f"{tool.name} timed out after 60s"})
    if proc.returncode != 0:
        return json.dumps({"ok": False, "exit": proc.returncode,
                           "stderr": (proc.stderr or proc.stdout).strip()})
    return json.dumps({"ok": True, "stdout": proc.stdout.strip()})


def _handoff_lock(path: str | None):
    """The lock the producers take, held across the write AND the poster run.

    A card's handoff file is one fixed path, and the poster reads it in a
    second process. Two overlapping posts to the same card would otherwise
    have the second's text land before the first's poster read it -- both
    calls report success and the wall shows one card twice.
    """
    if not path:
        return contextlib.nullcontext()
    from exclusive_lock import exclusive_lock  # the producers' own lock

    return exclusive_lock(path, "refusing to post")


def _write_handoff(path: str, text: str) -> None:
    from atomic_write import atomic_write  # the same writer the producers use
    atomic_write(path, text)


TOOLS: tuple[Tool, ...] = (HOUSEHOLD_TODO, KIOSK_POST_CARD)


def register(ctx) -> None:
    for tool in TOOLS:
        ctx.register_tool(name=tool.name, toolset=TOOLSET, schema=tool.schema,
                          handler=partial(run, tool), description=tool.description)
