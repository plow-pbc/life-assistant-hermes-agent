"""Behavioural tests for the life_tools Hermes plugin: what the model would see
go wrong -- a tool missing from the list, a todo that never reaches the
manifest, a required field silently dropped."""
import json
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugins"))
import life_tools  # noqa: E402


class FakeCtx:
    def __init__(self):
        self.tools = {}

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = (toolset, schema, handler)


def test_register_puts_every_row_in_the_tools_list():
    ctx = FakeCtx()
    life_tools.register(ctx)
    assert set(ctx.tools) == {t.name for t in life_tools.TOOLS}
    for t in life_tools.TOOLS:
        toolset, schema, handler = ctx.tools[t.name]
        assert toolset == "life"
        assert schema["name"] == t.name and schema["description"] == t.description
        assert schema["parameters"]["type"] == "object"
        assert callable(handler)


def test_every_row_points_at_an_executable_script_in_this_tree():
    for t in life_tools.TOOLS:
        local = REPO_ROOT / t.script.replace("/opt/hermes/skills/", "")
        assert local.exists(), t.script
        assert local.stat().st_mode & stat.S_IXUSR, t.script


@pytest.mark.parametrize("args,expected", [
    ({"action": "show"}, ["show"]),
    ({"action": "add", "text": "Renew passports", "why": "Oct 3 trip"}, ["add", "Renew passports", "--why", "Oct 3 trip"]),
    ({"action": "done", "id": "a1b2"}, ["done", "a1b2"]),
    ({"action": "rename", "name": "Weekend jobs"}, ["rename", "Weekend jobs"]),
    ({"action": "rule_add", "rule": "Groceries last"}, ["rule", "add", "Groceries last"]),
    ({"action": "rule_remove", "rule": "2"}, ["rule", "remove", "2"]),
    ({"action": "rank", "ids": ["b", "a"]}, ["rank", "b", "a"]),
    ({"action": "why", "id": "a1", "why": ""}, ["why", "a1", ""]),
    ({"action": "post", "dry_run": True}, ["post", "--dry-run"]),
])
def test_todo_argv_maps_each_action(args, expected):
    assert life_tools.todo_argv(args) == expected


@pytest.mark.parametrize("args", [
    {"action": "add"}, {"action": "done"}, {"action": "rename", "name": " "},
    {"action": "rule_add"}, {"action": "bogus"}, {},
])
def test_todo_argv_refuses_a_missing_field_without_running(args):
    out = life_tools.todo_argv(args)
    assert isinstance(out, str) and ("required" in out or "unknown" in out)


@pytest.fixture
def todo_via_tmp_manifest(tmp_path):
    """A test-only wrapper that runs the REAL priorities.py CLI against a tmp
    manifest, through the same subprocess boundary the plugin uses."""
    scripts = REPO_ROOT / "ld-priorities" / "scripts"
    shared = REPO_ROOT / "ld-shared" / "scripts"
    wrapper = tmp_path / "priorities-tmp.py"
    wrapper.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(scripts)!r}); sys.path.insert(0, {str(shared)!r})\n"
        "import priorities\n"
        f"priorities.MANIFEST = {str(tmp_path / 'priorities.json')!r}\n"
        f"priorities.MESSAGE_FILE = {str(tmp_path / 'priorities-text')!r}\n"
        f"priorities.WALL_READY = {str(tmp_path / 'setup-complete')!r}\n"
        "priorities.main(sys.argv[1:])\n"
    )
    wrapper.chmod(0o755)
    return life_tools.Tool(**{**life_tools.HOUSEHOLD_TODO.__dict__, "script": str(wrapper)})


def test_household_todo_round_trips_through_the_real_cli(todo_via_tmp_manifest):
    t = todo_via_tmp_manifest
    added = json.loads(life_tools.run(t, {"action": "add", "text": "Renew passports"}))
    assert added["ok"] is True
    item_id = added["stdout"].strip()
    shown = json.loads(json.loads(life_tools.run(t, {"action": "show"}))["stdout"])
    assert [i["text"] for i in shown["items"]] == ["Renew passports"]
    assert json.loads(life_tools.run(t, {"action": "rank", "ids": [item_id]}))["ok"] is True
    assert json.loads(life_tools.run(t, {"action": "done", "id": item_id}))["ok"] is True
    shown = json.loads(json.loads(life_tools.run(t, {"action": "show"}))["stdout"])
    assert shown["items"] == [] and shown["done"][0]["text"] == "Renew passports"


def test_household_todo_surfaces_the_scripts_refusal(todo_via_tmp_manifest):
    out = json.loads(life_tools.run(todo_via_tmp_manifest, {"action": "done", "id": "nope"}))
    assert out["ok"] is False and "unknown" in out["stderr"] and out["exit"] != 0


def test_household_todo_refuses_before_running(todo_via_tmp_manifest):
    out = json.loads(life_tools.run(todo_via_tmp_manifest, {"action": "add"}))
    assert out["ok"] is False and "required" in out["error"]
