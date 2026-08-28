"""tests/test_triage_candidates.py — behavior tests for the triage gather filter.

Feeds the script sqlite3 `-json`-shaped output on stdin and asserts on the
JSON it emits — the contract the SKILL.md's ranking step consumes.
"""
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ld-morning-triage" / "scripts" / "triage_candidates.py"


def run(stdin_text, config, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(config))
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(cfg)],
        input=stdin_text, capture_output=True, text=True,
    )


def msg(chat_id, is_from_me, sent_at, hexbody, handle="+15550001111"):
    """One row the way `sqlite3 -json` frames the fixed query's output."""
    return {"chat_id": chat_id, "is_from_me": is_from_me,
            "handle": "me" if is_from_me else handle,
            "sent_at": sent_at, "hexbody": hexbody}


def sqljson(*rows):
    return json.dumps(list(rows))


def hexutf8(s):
    return s.encode("utf-8").hex().upper()


def typedstream_fixture(s, marker=None):
    """A minimal typedstream blob the way chat.db shapes attributedBody:
    an NSString class marker, five bytes of markers, a length (one byte
    below 0x80, else a 0x81/0x82 marker + little-endian length), then the
    UTF-8 payload."""
    body = s.encode("utf-8")
    if marker is None:
        assert len(body) < 0x80
        length = bytes([len(body)])
    elif marker == 0x81:
        length = b"\x81" + len(body).to_bytes(2, "little")
    elif marker == 0x82:
        length = b"\x82" + len(body).to_bytes(4, "little")
    else:
        raise AssertionError(f"unknown length marker {marker:#x}")
    return (b"\x04\x0bstreamtyped\x81NSString\x01\x94\x84\x01+"
            + length + body).hex().upper()


BASE_CONFIG = {"morning_triage": {"exclude": {"imessage_handles": []}}}


def test_unaddressed_inbound_chat_survives(tmp_path):
    r = run(sqljson(msg(7, 0, 1000, hexutf8("are you coming tonight?"))),
            BASE_CONFIG, tmp_path)
    assert r.returncode == 0
    (cand,) = json.loads(r.stdout)
    assert cand == {"chat_id": 7, "handle": "+15550001111",
                    "sent_at": 1000, "excerpt": "are you coming tonight?"}


def test_replied_chat_is_dropped(tmp_path):
    rows = sqljson(msg(7, 0, 1000, hexutf8("are you coming?")),
                   msg(7, 1, 1001, hexutf8("yes!")))
    r = run(rows, BASE_CONFIG, tmp_path)
    assert json.loads(r.stdout) == []


def test_excluded_handle_is_dropped(tmp_path):
    cfg = {"morning_triage": {"exclude": {"imessage_handles": ["+15550001111"]}}}
    r = run(sqljson(msg(7, 0, 1000, hexutf8("spam"))), cfg, tmp_path)
    assert json.loads(r.stdout) == []


@pytest.mark.parametrize(("marker", "text"), [
    (None, "hola"),
    (0x81, "x" * 300),   # 128+ bytes is routine for real texts — the u16 branch
    (0x82, "hola"),
], ids=["inline", "u16", "u32"])
def test_typedstream_length_markers(tmp_path, marker, text):
    rows = sqljson(msg(7, 0, 1000, typedstream_fixture(text, marker)))
    r = run(rows, BASE_CONFIG, tmp_path)
    (cand,) = json.loads(r.stdout)
    assert cand["excerpt"] == text


def test_the_whole_unanswered_burst_survives_newest_first(tmp_path):
    """An urgent ask followed by a "you there?" nudge: both must reach the
    ranker — reducing the chat to its newest message would hide the ask."""
    rows = sqljson(msg(7, 0, 1000, hexutf8("can you sign the form today?")),
                   msg(7, 0, 1005, hexutf8("you there?")))
    r = run(rows, BASE_CONFIG, tmp_path)
    cands = json.loads(r.stdout)
    assert [c["excerpt"] for c in cands] == \
        ["you there?", "can you sign the form today?"]


def test_only_inbound_after_the_last_reply_counts(tmp_path):
    rows = sqljson(msg(7, 0, 1000, hexutf8("old, already answered")),
                   msg(7, 1, 1001, hexutf8("done")),
                   msg(7, 0, 1005, hexutf8("new ask")))
    r = run(rows, BASE_CONFIG, tmp_path)
    (cand,) = json.loads(r.stdout)
    assert cand["excerpt"] == "new ask"


@pytest.mark.parametrize("hexbody", ["", None], ids=["real", "tolerated"])
def test_attachment_only_inbound_yields_no_candidate(tmp_path, hexbody):
    # sqlite's hex() never returns NULL — hex(coalesce(NULL, NULL)) is the
    # empty string, so -json frames an attachment-only row as "hexbody":"".
    # null is tolerated as well, but "" is the shape the producer emits.
    r = run(sqljson(msg(7, 0, 1000, hexbody)), BASE_CONFIG, tmp_path)
    assert json.loads(r.stdout) == []


def test_empty_gather_is_a_quiet_day(tmp_path):
    # sqlite3 -json emits nothing at all for an empty result set.
    r = run("", BASE_CONFIG, tmp_path)
    assert r.returncode == 0
    assert json.loads(r.stdout) == []


def test_malformed_json_fails_loudly(tmp_path):
    r = run("7|0|+15550001111|1000|00\n", BASE_CONFIG, tmp_path)
    assert r.returncode == 2
    assert r.stdout == ""


def test_unknown_is_from_me_fails_loudly(tmp_path):
    """An unexpected is_from_me is the likeliest symptom of upstream query
    drift; silently reading it as outbound would suppress the chat."""
    r = run(sqljson(msg(7, 2, 1000, hexutf8("hi"))), BASE_CONFIG, tmp_path)
    assert r.returncode == 2
    assert r.stdout == ""


def test_missing_config_key_fails_loudly(tmp_path):
    r = run("", {"morning_triage": {}}, tmp_path)
    assert r.returncode != 0


def test_the_skill_md_sql_agrees_with_the_parser(tmp_path):
    """The SKILL.md's fixed SQL literal and this script restate one framing
    contract (the -json column set), and nothing else pins them together — a
    column renamed in a SKILL.md edit would otherwise fail at 07:05 on the
    Mac (exit 2, no alert), not in CI. So run the actual literal against a
    chat.db-shaped fixture, frame the rows the way `sqlite3 -json` does, and
    feed them straight to the script: agreement is only possible if the
    aliases still match, the window still selects, and the tapback/group-event
    predicates still hold."""
    text = (REPO / "ld-morning-triage" / "SKILL.md").read_text()
    (sql,) = re.findall(r'"(select .*?)"\]', text)

    db = sqlite3.connect(":memory:")
    db.executescript("""
        create table message (ROWID integer primary key, is_from_me int,
            handle_id int, date int, text text, attributedBody blob,
            associated_message_type int default 0, item_type int default 0);
        create table chat_message_join (chat_id int, message_id int);
        create table handle (ROWID integer primary key, id text);
    """)
    now_apple = int((time.time() - 978307200) * 1_000_000_000)
    db.execute("insert into handle values (1, '+15550001111')")
    db.execute("insert into message (ROWID, is_from_me, handle_id, date, text)"
               " values (1, 0, 1, ?, 'ping?')", (now_apple,))
    # Later rows the SQL must exclude: an inbound tapback and a group event —
    # either surviving would displace 'ping?' as the chat's latest message.
    db.execute("insert into message (ROWID, is_from_me, handle_id, date, text,"
               " associated_message_type) values (2, 0, 1, ?, 'Loved x', 2000)",
               (now_apple + 1,))
    db.execute("insert into message (ROWID, is_from_me, handle_id, date, text,"
               " item_type) values (3, 0, 1, ?, null, 1)", (now_apple + 2,))
    # A second chat answered by an attachment-only reply (text AND
    # attributedBody NULL): the outbound row must survive the SQL — it is the
    # proof a reply happened — and its empty hexbody (hex(NULL) is '') must
    # survive the framing, so the chat produces no candidate.
    db.execute("insert into message (ROWID, is_from_me, handle_id, date, text)"
               " values (4, 0, 1, ?, 'seen this?')", (now_apple,))
    db.execute("insert into message (ROWID, is_from_me, handle_id, date)"
               " values (5, 1, null, ?)", (now_apple + 3,))
    db.execute("insert into chat_message_join values (7, 1), (7, 2), (7, 3),"
               " (8, 4), (8, 5)")

    cur = db.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = json.dumps([dict(zip(cols, r)) for r in cur])
    r = run(rows, BASE_CONFIG, tmp_path)
    assert r.returncode == 0, r.stderr
    (cand,) = json.loads(r.stdout)
    assert cand["chat_id"] == 7
    assert cand["handle"] == "+15550001111"
    assert cand["excerpt"] == "ping?"
