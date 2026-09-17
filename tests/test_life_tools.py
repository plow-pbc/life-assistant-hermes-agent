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
sys.path.append(str(REPO_ROOT / "ld-shared" / "scripts"))  # atomic_write, for the handoff
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
    for script in [life_tools.HOUSEHOLD_TODO.script, *(s for s, _ in life_tools.CARDS.values())]:
        local = REPO_ROOT / script.replace("/opt/hermes/skills/", "")
        assert local.exists(), script
        assert local.stat().st_mode & stat.S_IXUSR, script


@pytest.mark.parametrize("args,expected", [
    ({"action": "show"}, ["show"]),
    ({"action": "add", "text": "Renew passports", "why": "Oct 3 trip"}, ["add", "Renew passports", "--why", "Oct 3 trip"]),
    ({"action": "done", "id": "a1b2"}, ["done", "a1b2"]),
    ({"action": "rename", "name": "Weekend jobs"}, ["rename", "Weekend jobs"]),
    ({"action": "rule_add", "rule": "Groceries last"}, ["rule", "add", "Groceries last"]),
    ({"action": "rule_remove", "rule": "2"}, ["rule", "remove", "2"]),
    ({"action": "rank", "ids": ["b", "a"]}, ["rank", "b", "a"]),
    ({"action": "rank", "ids": []}, ["rank"]),  # the last item done: an empty order is valid
    ({"action": "why", "id": "a1", "why": ""}, ["why", "a1", ""]),
    ({"action": "post", "dry_run": True}, ["post", "--dry-run"]),
])
def test_todo_argv_maps_each_action(args, expected):
    assert life_tools.todo_argv(args) == expected


@pytest.mark.parametrize("args", [
    {"action": "add"}, {"action": "done"}, {"action": "rename", "name": " "},
    {"action": "rule_add"}, {"action": "bogus"}, {}, {"action": "rank"},
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


def test_a_missing_script_is_an_error_not_a_traceback(todo_via_tmp_manifest):
    """A skills-layout mismatch in the image is the interpreter's own exit, not
    an exception out of the handler -- run() invokes [sys.executable, script],
    so the script never has to exist for the call itself to succeed."""
    gone = life_tools.Tool(**{**todo_via_tmp_manifest.__dict__, "script": "/nonexistent/priorities.py"})
    out = json.loads(life_tools.run(gone, {"action": "show"}))
    assert out["ok"] is False and out["exit"] != 0 and "/nonexistent/priorities.py" in out["stderr"]


def test_household_todo_refuses_before_running(todo_via_tmp_manifest):
    out = json.loads(life_tools.run(todo_via_tmp_manifest, {"action": "add"}))
    assert out["ok"] is False and "required" in out["error"]


def test_cards_map_matches_the_poster_wrappers():
    for card, (script, handoff) in life_tools.CARDS.items():
        local = REPO_ROOT / script.replace("/opt/hermes/skills/", "")
        src = local.read_text()
        assert f'MESSAGE_FILE = "{handoff}"' in src, card
        assert f'BODY_TYPE = "{card}"' in src, card


def test_kiosk_post_card_writes_the_handoff_and_dry_runs_the_poster(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_ENDPOINT_URL", "https://x.test/api/message")
    monkeypatch.setenv("DASHBOARD_TOKEN", "t")
    handoff = tmp_path / "weather-text"
    wrapper = tmp_path / "post_weather_tmp.py"
    shared = REPO_ROOT / "ld-shared" / "scripts"
    wrapper.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(shared)!r})\n"
        "import post_to_kiosk\n"
        f"post_to_kiosk.MESSAGE_FILE = {str(handoff)!r}\n"
        "post_to_kiosk.CARD = '3'; post_to_kiosk.BODY_TYPE = 'weather'; post_to_kiosk.TITLE = ''\n"
        "post_to_kiosk.main(sys.argv[1:])\n"
    )
    # The registered row itself, so run()'s per-card CARDS lookup is exercised.
    monkeypatch.setitem(life_tools.CARDS, "weather", (str(wrapper), str(handoff)))
    tile = "<div class=\"weather\">72°</div>"
    out = json.loads(life_tools.run(life_tools.KIOSK_POST_CARD,
                                    {"card": "weather", "text": tile, "dry_run": True}))
    assert out["ok"] is True, out
    preview = json.loads(out["stdout"])
    assert preview["body"]["card"] == "3" and preview["body"]["type"] == "weather"
    assert handoff.read_text() == tile
    assert oct(handoff.stat().st_mode)[-3:] == "600"


def test_kiosk_post_card_holds_the_handoff_lock_across_the_poster(tmp_path, monkeypatch):
    """Two overlapping posts to one card must not have the second's text land
    before the first's poster has read it. The poster here IS the second
    writer: it tries the lock non-blockingly and reports what it found."""
    handoff = tmp_path / "weather-text"
    wrapper = tmp_path / "poster.py"
    wrapper.write_text(
        "import fcntl\n"
        f"f = open({str(handoff) + '.lock'!r}, 'a+')\n"
        "try:\n"
        "    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "    print('free')\n"
        "except BlockingIOError:\n"
        "    print('held')\n"
    )
    monkeypatch.setitem(life_tools.CARDS, "weather", (str(wrapper), str(handoff)))
    out = json.loads(life_tools.run(life_tools.KIOSK_POST_CARD, {"card": "weather", "text": "72"}))
    assert out["ok"] is True and out["stdout"] == "held"


def test_a_lock_failure_is_the_tools_error_not_the_hosts_exit(tmp_path, monkeypatch):
    """exclusive_lock exits rather than raises when it cannot take the lock, and
    the registry dispatches tools under `except Exception` -- so an escaping
    SystemExit would end the host over one failed card."""
    handoff = tmp_path / "not-a-dir" / "sub" / "weather-text"
    handoff.parent.parent.write_text("")  # the lock's parent cannot be created
    monkeypatch.setitem(life_tools.CARDS, "weather", (str(tmp_path / "poster.py"), str(handoff)))
    out = json.loads(life_tools.run(life_tools.KIOSK_POST_CARD, {"card": "weather", "text": "72"}))
    assert out["ok"] is False and "could not take the lock" in out["error"]


def test_kiosk_post_card_refuses_an_unknown_card_or_empty_text():
    assert "card" in life_tools.card_argv({"card": "priorities", "text": "x"})
    assert "text" in life_tools.card_argv({"card": "weather", "text": " "})


def test_image_ships_the_plugin_and_persona_routes_todos():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    assert "COPY plugins/life_tools/" in dockerfile and "/opt/hermes/plugins/life_tools/" in dockerfile
    persona = (REPO_ROOT / "runtime" / "persona.md").read_text()
    assert "first tool call is `household_todo`" in persona
    for skill in ("ld-weather", "ld-sports", "ld-morning-triage", "ld-morning-updates"):
        assert "kiosk_post_card" in (REPO_ROOT / skill / "SKILL.md").read_text(), skill


def test_a_mutation_refreshes_the_wall_by_itself(todo_via_tmp_manifest):
    """The owner adds an item and the card should follow without a second call.
    With no wall marker the post reports NO WALL; a refused change posts nothing."""
    t = todo_via_tmp_manifest
    added = json.loads(life_tools.run(t, {"action": "add", "text": "Renew passports"}))
    assert added["ok"] is True and added["wall"].startswith("NO WALL")
    shown = json.loads(life_tools.run(t, {"action": "show"}))
    assert "wall" not in shown
    refused = json.loads(life_tools.run(t, {"action": "done", "id": "nope"}))
    assert refused["ok"] is False and "wall" not in refused
