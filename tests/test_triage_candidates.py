"""tests/test_triage_candidates.py — behavior tests for the triage gather filter.

Feeds the script real-shaped sqlite output on stdin and asserts on the JSON
it emits — the contract the SKILL.md's ranking step consumes.
"""
import json
import subprocess
import sys
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


def typedstream_fixture(s):
    """A minimal typedstream blob the way chat.db shapes attributedBody:
    an NSString class marker, five bytes of markers, a one-byte length,
    then the UTF-8 payload."""
    body = s.encode("utf-8")
    assert len(body) < 0x80
    return (b"\x04\x0bstreamtyped\x81NSString\x01\x94\x84\x01+"
            + bytes([len(body)]) + body).hex().upper()


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


def test_missing_config_key_fails_loudly(tmp_path):
    r = run("", {"morning_triage": {}}, tmp_path)
    assert r.returncode != 0
