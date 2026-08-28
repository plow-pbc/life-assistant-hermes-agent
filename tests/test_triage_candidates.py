"""tests/test_triage_candidates.py — behavior tests for the triage gather filter.

Feeds the script real-shaped sqlite output on stdin and asserts on the JSON
it emits — the contract the SKILL.md's ranking step consumes.
"""
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ld-morning-triage" / "scripts" / "triage_candidates.py"


def run(stdin_text, config, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(config))
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(cfg)],
        input=stdin_text, capture_output=True, text=True,
    )


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
    rows = f"7|0|+15550001111|1000|{hexutf8('are you coming tonight?')}\n"
    r = run(rows, BASE_CONFIG, tmp_path)
    assert r.returncode == 0
    (cand,) = json.loads(r.stdout)
    assert cand == {"chat_id": 7, "handle": "+15550001111",
                    "sent_at": 1000, "excerpt": "are you coming tonight?"}


def test_replied_chat_is_dropped(tmp_path):
    rows = (f"7|0|+15550001111|1000|{hexutf8('are you coming?')}\n"
            f"7|1|me|1001|{hexutf8('yes!')}\n")
    r = run(rows, BASE_CONFIG, tmp_path)
    assert json.loads(r.stdout) == []


def test_excluded_handle_is_dropped(tmp_path):
    cfg = {"morning_triage": {"exclude": {"imessage_handles": ["+15550001111"]}}}
    rows = f"7|0|+15550001111|1000|{hexutf8('spam')}\n"
    r = run(rows, cfg, tmp_path)
    assert json.loads(r.stdout) == []


def test_typedstream_body_is_decoded(tmp_path):
    rows = f"7|0|+15550001111|1000|{typedstream_fixture('hola')}\n"
    r = run(rows, BASE_CONFIG, tmp_path)
    (cand,) = json.loads(r.stdout)
    assert cand["excerpt"] == "hola"


def test_long_typedstream_body_uses_the_two_byte_length(tmp_path):
    text = "x" * 300  # 128+ bytes is routine for real texts — the 0x81 branch
    rows = f"7|0|+15550001111|1000|{typedstream_fixture(text, marker=0x81)}\n"
    r = run(rows, BASE_CONFIG, tmp_path)
    (cand,) = json.loads(r.stdout)
    assert cand["excerpt"] == text


def test_four_byte_typedstream_length_is_decoded(tmp_path):
    rows = f"7|0|+15550001111|1000|{typedstream_fixture('hola', marker=0x82)}\n"
    r = run(rows, BASE_CONFIG, tmp_path)
    (cand,) = json.loads(r.stdout)
    assert cand["excerpt"] == "hola"


def test_newest_inbound_message_wins_per_chat(tmp_path):
    rows = (f"7|0|+15550001111|1000|{hexutf8('first')}\n"
            f"7|0|+15550001111|1005|{hexutf8('second')}\n")
    r = run(rows, BASE_CONFIG, tmp_path)
    (cand,) = json.loads(r.stdout)
    assert cand["excerpt"] == "second" and cand["sent_at"] == 1005


def test_malformed_row_fails_loudly(tmp_path):
    r = run("not|enough|fields\n", BASE_CONFIG, tmp_path)
    assert r.returncode == 2
    assert r.stdout == ""


def test_unknown_is_from_me_fails_loudly(tmp_path):
    """An unexpected is_from_me is the likeliest symptom of upstream query
    drift; silently reading it as outbound would suppress the chat."""
    r = run(f"7|2|+15550001111|1000|{hexutf8('hi')}\n", BASE_CONFIG, tmp_path)
    assert r.returncode == 2
    assert r.stdout == ""


def test_missing_config_key_fails_loudly(tmp_path):
    r = run("", {"morning_triage": {}}, tmp_path)
    assert r.returncode != 0


def test_the_skill_md_sql_agrees_with_the_parser(tmp_path):
    """The SKILL.md's fixed SQL literal and this script restate one framing
    contract (`chat_id|is_from_me|handle|ts|hexbody`), and nothing else pins
    them together — a column reordered in a SKILL.md edit would otherwise
    fail at 07:05 on the Mac (exit 2, no alert), not in CI. So run the actual
    literal against a chat.db-shaped fixture and feed its rows straight to
    the script: agreement is only possible if the framing still matches, the
    window still selects, and the tapback/group-event predicates still hold."""
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
    # proof a reply happened — and its empty hexbody field must survive the
    # framing, so the chat produces no candidate.
    db.execute("insert into message (ROWID, is_from_me, handle_id, date, text)"
               " values (4, 0, 1, ?, 'seen this?')", (now_apple,))
    db.execute("insert into message (ROWID, is_from_me, handle_id, date)"
               " values (5, 1, null, ?)", (now_apple + 3,))
    db.execute("insert into chat_message_join values (7, 1), (7, 2), (7, 3),"
               " (8, 4), (8, 5)")

    rows = "".join(
        "|".join("" if v is None else str(v) for v in r) + "\n"
        for r in db.execute(sql)
    )
    r = run(rows, BASE_CONFIG, tmp_path)
    assert r.returncode == 0, r.stderr
    (cand,) = json.loads(r.stdout)
    assert cand["chat_id"] == 7
    assert cand["handle"] == "+15550001111"
    assert cand["excerpt"] == "ping?"
