"""life_tools -- the Life assistant's first-class tools.

The model picks actions from its tools list, not from skill prose, so the
capabilities it must reach for on turn one are registered here as tools. Each
row wraps one of this image's skill scripts and runs it as a subprocess; the
description on each row is the routing lever and is quoted from the spec.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from functools import partial

SKILLS = "/opt/hermes/skills"
TOOLSET = "life"

# The shared writer every producer publishes through, on the skills root this
# image ships and on the repo two levels up, which is the one the tests import.
# A path that is not there is skipped by the import machinery.
for _shared in (f"{SKILLS}/ld-shared/scripts",
                os.path.join(os.path.dirname(os.path.realpath(__file__)),
                             "..", "..", "ld-shared", "scripts")):
    if _shared not in sys.path:
        sys.path.append(_shared)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    script: str
    argv: object  # callable(args) -> list[str] | str (an error message)
    handoff: str | None = None  # a fixed file the handler writes `text` to first

    @property
    def schema(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


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
    if need and not (args.get(need) if need == "ids" else _clean(args.get(need))):
        return f"{need} is required for {action}"
    if action == "add":
        argv = ["add", _clean(args["text"])]
        if _clean(args.get("why")):
            argv += ["--why", _clean(args["why"])]
        return argv
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


def run(tool: Tool, args: dict, **_kwargs) -> str:
    args = args or {}
    argv = tool.argv(args)
    if isinstance(argv, str):
        return json.dumps({"ok": False, "error": argv})
    if tool.handoff:
        _write_handoff(tool.handoff, args["text"].strip())
    try:
        proc = subprocess.run([sys.executable, tool.script, *argv],
                              capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return json.dumps({"ok": False, "error": f"{tool.name} timed out after 60s"})
    if proc.returncode != 0:
        return json.dumps({"ok": False, "exit": proc.returncode,
                           "stderr": (proc.stderr or proc.stdout).strip()})
    return json.dumps({"ok": True, "stdout": proc.stdout.strip()})


def _write_handoff(path: str, text: str) -> None:
    from atomic_write import atomic_write  # the same writer the producers use
    atomic_write(path, text)


TOOLS: tuple[Tool, ...] = (HOUSEHOLD_TODO,)


def register(ctx) -> None:
    for tool in TOOLS:
        ctx.register_tool(name=tool.name, toolset=TOOLSET, schema=tool.schema,
                          handler=partial(run, tool), description=tool.description)
