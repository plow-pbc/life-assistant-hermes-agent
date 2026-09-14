"""Behavioural tests for ld-priorities' manifest CLI: what the owner would see
go wrong — an item lost, a rank that silently drops one, a rename that does not
reach the card title."""
import contextlib
import io
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "ld-priorities" / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "ld-shared" / "scripts"))
import priorities  # noqa: E402


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(priorities, "MANIFEST", str(tmp_path / "priorities.json"))
    monkeypatch.setattr(priorities, "MESSAGE_FILE", str(tmp_path / "priorities-text"))
    monkeypatch.setattr(priorities, "WALL_READY", str(tmp_path / "setup-complete"))
    return tmp_path


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        priorities.main(list(argv))
    return out.getvalue().strip()


def ids():
    return [i["id"] for i in priorities.load()["items"]]


def test_add_appends_last_and_show_reports_it(manifest):
    a = run("add", "Renew passports", "--why", "before Oct 3 trip")
    b = run("add", "Clean gutters")
    shown = json.loads(run("show"))
    assert [i["text"] for i in shown["items"]] == ["Renew passports", "Clean gutters"]
    assert shown["items"][0]["why"] == "before Oct 3 trip"
    assert shown["name"] == priorities.DEFAULT_NAME
    assert ids() == [a, b]


def test_rank_must_be_a_full_permutation(manifest):
    a = run("add", "A")
    b = run("add", "B")
    c = run("add", "C")
    run("rank", c, a, b)
    assert ids() == [c, a, b]
    with pytest.raises(SystemExit, match="every open item"):
        run("rank", a, b)  # dropped c
    with pytest.raises(SystemExit, match="every open item"):
        run("rank", a, b, c, a)  # duplicate
    with pytest.raises(SystemExit, match="unknown"):
        run("rank", a, b, "zzzz")
    assert ids() == [c, a, b]  # a refused rank changes nothing
    with pytest.raises(SystemExit, match="every open item"):
        run("rank")  # empty rank while items are open
    for i in (a, b, c):
        run("done", i)
    run("rank")  # the last item done: an empty rank is the valid order
    assert ids() == []


def test_done_moves_to_done_and_remove_forgets(manifest):
    a = run("add", "A")
    b = run("add", "B")
    run("done", a)
    run("remove", b)
    m = priorities.load()
    assert m["items"] == []
    assert [d["text"] for d in m["done"]] == ["A"]
    assert "done" in m["done"][0]
    with pytest.raises(SystemExit, match="unknown"):
        run("done", "nope")


def test_rename_and_rules(manifest):
    run("rename", "Weekend jobs")
    run("rule", "add", "Groceries always go last")
    run("rule", "add", "Dated things first")
    run("rule", "remove", "1")
    m = priorities.load()
    assert m["name"] == "Weekend jobs"
    assert m["rules"] == ["Dated things first"]
    with pytest.raises(SystemExit):
        run("rename", "   ")
    with pytest.raises(SystemExit, match="not a rule number"):
        run("rule", "remove", "abc")


def test_why_sets_and_clears(manifest):
    a = run("add", "A")
    run("why", a, "blocks the trip")
    assert priorities.load()["items"][0]["why"] == "blocks the trip"
    run("why", a, "")
    assert "why" not in priorities.load()["items"][0]


def test_manifest_is_private_and_survives_a_concurrent_add(manifest, run_concurrently):
    errors = run_concurrently(lambda: run("add", "one"), lambda: run("add", "two"))
    assert errors == []
    assert sorted(i["text"] for i in priorities.load()["items"]) == ["one", "two"]
    assert oct(Path(priorities.MANIFEST).stat().st_mode)[-3:] == "600"


def test_compose_escapes_and_caps_at_six(manifest):
    for n in range(8):
        run("add", f"Item <{n}> & co")
    tile = priorities.compose(priorities.load())
    assert tile.count('class="pr-item"') == 6
    assert "Item &lt;0&gt; &amp; co" in tile
    assert "<script" not in tile and "<Item" not in tile


def test_compose_empty_list_still_renders(manifest):
    assert "Nothing on the list" in priorities.compose(priorities.load())


def test_compose_style_matches_the_protocol_doc(manifest):
    doc = (REPO_ROOT / "ld-shared" / "references" / "kiosk-protocol.md").read_text()
    block = re.search(r"### Priorities tile.*?```html\n(<style>.*?</style>)", doc, re.S).group(1)
    run("add", "A")
    assert priorities.compose(priorities.load()).startswith(re.sub(r"\n", "", block))


def test_post_without_a_wall_keeps_the_list_and_exits_zero(manifest):
    run("add", "A")
    assert run("post").startswith("NO WALL")
    assert not Path(priorities.MESSAGE_FILE).exists()


def test_post_dry_run_carries_the_list_name_as_title(manifest, monkeypatch):
    Path(priorities.WALL_READY).touch()
    monkeypatch.setenv("DASHBOARD_ENDPOINT_URL", "https://x.test/api/message")
    monkeypatch.setenv("DASHBOARD_TOKEN", "t")
    run("rename", "Weekend jobs")
    run("add", "A")
    out = json.loads(run("post", "--dry-run"))
    assert out["body"]["card"] == "6"
    assert out["body"]["type"] == "priorities"
    assert out["body"]["title"] == "Weekend jobs"
    assert Path(priorities.MESSAGE_FILE).read_text().startswith("<style>")
