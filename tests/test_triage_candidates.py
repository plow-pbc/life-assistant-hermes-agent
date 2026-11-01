"""tests/test_triage_candidates.py — behavior tests for the triage gather filter.

Feeds the script a `latch messages search` answer as a gather file and asserts
on the JSON it emits — the contract the SKILL.md's ranking step consumes. The
row shape is latch's (`at`, `chat.id`, `from.{handle,me}`, `text`), measured
on a live answer; the values are synthesized.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ld-morning-triage" / "scripts" / "triage_candidates.py"


def run(content, config, tmp_path):
    """Invoke the filter the way the cron does: the gather as a file
    argument (the sandbox allows only plain-argv commands)."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(config))
    gather = tmp_path / "gather"
    gather.write_text(content)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(cfg), str(gather)],
        capture_output=True, text=True,
    )
    # The raw corpus must not outlive the run, success or failure — asserted
    # here so every row pins it.
    assert not gather.exists()
    return r


def msg(chat_id, me, sent_at, text, handle="+15550001111"):
    """One message the way latch frames it (unix seconds in, offset ISO out)."""
    at = datetime.fromtimestamp(sent_at, timezone.utc).astimezone().isoformat()
    return {"id": sent_at, "at": at, "attachments": 0, "service": "iMessage",
            "chat": {"id": chat_id, "kind": "direct", "name": None},
            "from": {"handle": "me" if me else handle, "me": bool(me)},
            "text": text}


def answer(*rows):
    """latch's answer: newest first, as `messages search` orders it."""
    rows = sorted(rows, key=lambda m: m["id"], reverse=True)
    return json.dumps({"messages": rows, "count": len(rows),
                       "incomplete": None, "next": None})


BASE_CONFIG = {"morning_triage": {"exclude": {"imessage_handles": []}}}


@pytest.mark.parametrize("content", [
    "",
    json.dumps({"count": 0}),
    json.dumps({"messages": None}),
    json.dumps([]),
    "7|0|+15550001111|1000|00\n",
    answer({**msg(7, 0, 1000, "hi"), "from": {"handle": "+1555", "me": 0}}),
    answer({**msg(7, 0, 1000, "hi"), "at": "2026-08-27T12:00:00"}),
], ids=["latch-failed", "missing-messages", "null-messages", "not-an-object",
        "not-json", "me-not-a-boolean", "no-offset"])
def test_a_broken_gather_fails_loudly_not_quietly(tmp_path, content):
    """Every broken shape must exit 2: a failed gather read as a quiet day
    would silently leave yesterday's alert up with no sign anything broke.
    An empty file is the latch call failing — latch always answers on success.
    A `me` that is not a boolean is the likeliest symptom of upstream drift;
    reading it as outbound would suppress the chat."""
    r = run(content, BASE_CONFIG, tmp_path)
    assert r.returncode == 2
    assert r.stdout == ""


def test_unaddressed_inbound_chat_survives(tmp_path):
    r = run(answer(msg(7, 0, 1000, "are you coming tonight?")),
            BASE_CONFIG, tmp_path)
    assert r.returncode == 0
    (cand,) = json.loads(r.stdout)
    assert cand == {"chat_id": 7, "handle": "+15550001111",
                    "sent_at": 1000, "excerpt": "are you coming tonight?"}


def test_excluded_handle_is_dropped(tmp_path):
    cfg = {"morning_triage": {"exclude": {"imessage_handles": ["+15550001111"]}}}
    r = run(answer(msg(7, 0, 1000, "spam")), cfg, tmp_path)
    assert json.loads(r.stdout) == []


def test_the_whole_unanswered_burst_survives_newest_first(tmp_path):
    """An urgent ask followed by a "you there?" nudge: both must reach the
    ranker — reducing the chat to its newest message would hide the ask."""
    rows = answer(msg(7, 0, 1000, "can you sign the form today?"),
                  msg(7, 0, 1005, "you there?"))
    r = run(rows, BASE_CONFIG, tmp_path)
    cands = json.loads(r.stdout)
    assert [c["excerpt"] for c in cands] == \
        ["you there?", "can you sign the form today?"]


def test_only_inbound_after_the_last_reply_counts(tmp_path):
    rows = answer(msg(7, 0, 1000, "old, already answered"),
                  msg(7, 1, 1001, "done"),
                  msg(7, 0, 1005, "new ask"))
    r = run(rows, BASE_CONFIG, tmp_path)
    (cand,) = json.loads(r.stdout)
    assert cand["excerpt"] == "new ask"


@pytest.mark.parametrize("rows", [
    pytest.param([msg(7, 0, 1000, "are you coming?"), msg(7, 1, 1001, "yes!")], id="text-reply"),
    pytest.param([msg(7, 0, 1000, "seen this?"), msg(7, 1, 1001, "")], id="attachment-reply"),
    pytest.param([msg(7, 0, 1000, "")], id="attachment-inbound"),
    pytest.param([msg(7, 0, 1000, None)], id="unreadable-inbound"),
    pytest.param([], id="quiet-window"),
])
def test_no_candidate_cases(tmp_path, rows):
    r = run(answer(*rows), BASE_CONFIG, tmp_path)
    assert r.returncode == 0
    assert json.loads(r.stdout) == []


def test_a_window_past_one_page_fails_rather_than_reads_quiet(tmp_path):
    """500 newer messages with nothing unanswered must not hide an older ask."""
    page = json.loads(answer(msg(7, 0, 1000, "yes")))
    r = run(json.dumps({**page, "next": "cursor"}), BASE_CONFIG, tmp_path)
    assert r.returncode == 2
    assert r.stdout == ""


def test_missing_config_key_fails_loudly(tmp_path):
    r = run(answer(), {"morning_triage": {}}, tmp_path)
    assert r.returncode != 0
