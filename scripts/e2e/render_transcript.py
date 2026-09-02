#!/usr/bin/env python3
"""Render one e2e run as a self-contained HTML report.

The twin's JSON is the record of what the owner's handset actually received;
this turns it into something a person can read in a browser -- bubbles in the
shape of the app the copy was written for, the attachments actually visible
(a GIF that animates, not a link that might 404 tomorrow), and, folded away
under each owner turn, what that answer did to config.json.

Usage:

    render_transcript.py --run-id T1-fresh                 # fetch from the twin
    render_transcript.py --run-id T1-fresh --thread chat_7
    render_transcript.py --run-id T1-fresh --transcript saved.json

Everything lands under scripts/e2e/runs/<run-id>/ : transcript.json,
attachments/, report.html. Set E2E_RUNS_DIR (or pass --runs-dir) to put the
reports somewhere else.
The HTML embeds every attachment as a data: URI, so the file survives being
copied anywhere, and the twin going away does not take the evidence with it.

Config snapshots are optional and come from --snapshots DIR, holding files
named `after-<N>.json` where N is the 1-based index of the owner message they
were captured after (`docker exec life-agent-e2e cat
/var/lib/hermes/ld/config.json > after-2.json`). A run with no snapshots
renders fine; it just has nothing to fold open.
"""

import argparse
import base64
import difflib
import html
import json
import mimetypes
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

E2E_DIR = Path(__file__).resolve().parent
# Reports land beside the loop by default (scripts/e2e/runs/, gitignored), not
# in anyone's notes directory: a path under one person's home is not a default
# anybody else can use. E2E_RUNS_DIR moves it; --runs-dir wins over both.
DEFAULT_RUNS = Path(os.environ.get("E2E_RUNS_DIR") or (E2E_DIR / "runs"))

# Hermes' own runtime notices. They are the harness talking, not onboarding
# copy, and a reviewer who reads them as the agent's voice draws the wrong
# conclusion about the writing -- so they are rendered as what they are.
NOISE_PREFIXES = ("⏳", "↪", "💡", "⚡")


def load_env():
    """The two non-secret keys this needs, read from the loop's own .env.

    Deliberately narrow: the same file holds the agent bearer and the Latch
    token, and nothing here should be able to put those in an HTML file.
    """
    env = {}
    path = E2E_DIR / ".env"
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in ("TWIN_HOST_BASE", "TWIN_THREAD"):
            env[key] = value.strip().strip("'\"")
    return env


def fetch_json(url):
    with urllib.request.urlopen(url) as resp:
        return json.load(resp)


def fetch_bytes(url):
    with urllib.request.urlopen(url) as resp:
        return resp.read()


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_noise(text):
    stripped = text.lstrip()
    return stripped.startswith(NOISE_PREFIXES)


def download_attachments(chat, twin_base, out_dir):
    """Save every media part's bytes and remember where they landed.

    The URL in the transcript is the twin's in-network origin
    (http://dtu-linq:8091), which does not resolve on the Mac -- only the path
    is reusable, against the host-facing twin. Same reason fetch-attachment.sh
    does it this way.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = {}
    seq = 0
    for msg in chat.get("messages", []):
        for part in msg.get("parts", []):
            if part.get("type") != "media" or not part.get("url"):
                continue
            seq += 1
            path = urllib.parse.urlparse(part["url"]).path
            name = part.get("filename") or Path(path).name or f"attachment-{seq}"
            dest = out_dir / f"{seq:02d}-{name}"
            try:
                data = fetch_bytes(twin_base.rstrip("/") + path)
            except Exception as exc:  # a dead link is evidence too, not a crash
                print(f"  !! could not fetch {path}: {exc}", file=sys.stderr)
                saved[id(part)] = (None, None, str(exc))
                continue
            dest.write_bytes(data)
            mime = part.get("mime_type") or mimetypes.guess_type(name)[0] \
                or "application/octet-stream"
            saved[id(part)] = (dest, mime, None)
            print(f"  saved {dest.name} ({len(data)} bytes, {mime})")
    return saved


def data_uri(path, mime):
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def load_snapshots(snap_dir):
    """{owner-turn-index: parsed config} from after-<N>.json files."""
    snaps = {}
    if not snap_dir:
        return snaps
    for path in sorted(Path(snap_dir).glob("after-*.json")):
        match = re.match(r"after-(\d+)\.json$", path.name)
        if not match:
            continue
        try:
            snaps[int(match.group(1))] = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            print(f"  !! {path.name} is not JSON: {exc}", file=sys.stderr)
    return snaps


def config_diff(before, after):
    a = json.dumps(before, indent=2, sort_keys=True).splitlines() if before is not None else []
    b = json.dumps(after, indent=2, sort_keys=True).splitlines()
    return list(difflib.unified_diff(a, b, "config.json before", "config.json after", lineterm=""))


CSS = """
:root {
  color-scheme: light dark;
  --bg: #f5f5f7; --panel: #fff; --ink: #1d1d1f; --muted: #6e6e73;
  --agent-bg: #e9e9eb; --agent-ink: #1d1d1f;
  --owner-bg: #248bf5; --owner-ink: #fff;
  --noise: #a1a1a6; --rule: #d2d2d7; --add: #1a7f37; --del: #cf222e;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #000; --panel: #1c1c1e; --ink: #f5f5f7; --muted: #8e8e93;
          --agent-bg: #26262a; --agent-ink: #f5f5f7; --noise: #6e6e73;
          --rule: #3a3a3c; --add: #3fb950; --del: #f85149; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
       font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
main { max-width: 720px; margin: 0 auto; padding: 24px 16px 64px; }
header { background: var(--panel); border: 1px solid var(--rule); border-radius: 12px;
         padding: 16px 18px; margin-bottom: 24px; }
h1 { margin: 0 0 6px; font-size: 20px; }
header dl { margin: 0; display: grid; grid-template-columns: max-content 1fr; gap: 2px 14px;
            font-size: 13px; color: var(--muted); }
header dt { font-weight: 600; }
header dd { margin: 0; word-break: break-all; }
.row { display: flex; margin: 10px 0; }
.row.owner { justify-content: flex-end; }
/* width: max-content so a two-word reply stays a two-word bubble, capped at
   the stack so a paragraph fills the column instead of wrapping every line. */
.bubble { width: max-content; max-width: 100%; padding: 9px 14px; border-radius: 20px;
          white-space: pre-wrap; overflow-wrap: anywhere; }
.agent .bubble { background: var(--agent-bg); color: var(--agent-ink); border-bottom-left-radius: 5px; }
.owner .bubble { background: var(--owner-bg); color: var(--owner-ink); border-bottom-right-radius: 5px; }
.meta { font-size: 11px; color: var(--muted); margin: 2px 4px 0; font-variant-numeric: tabular-nums; }
.row.owner .meta { text-align: right; }
.stack { display: flex; flex-direction: column; max-width: 78%; }
.row.owner .stack { align-items: flex-end; }
.latency { color: var(--muted); }
.noise .bubble { background: transparent; color: var(--noise); border: 1px dashed var(--rule);
                 font-size: 13px; font-style: italic; }
.att { margin-top: 6px; }
.att img { max-width: 100%; border-radius: 14px; display: block; border: 1px solid var(--rule); }
.att .cap { font-size: 11px; color: var(--muted); margin-top: 3px; }
.att .missing { font-size: 12px; color: var(--del); }
details.cfg { margin: 12px 0 18px; background: var(--panel); border: 1px solid var(--rule);
              border-radius: 10px; padding: 8px 12px; }
details.cfg summary { cursor: pointer; font-size: 12px; color: var(--muted); }
details.cfg pre { overflow-x: auto; font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
                  margin: 8px 0 4px; }
.d-add { color: var(--add); }
.d-del { color: var(--del); }
.d-hd { color: var(--muted); }
.nodiff { font-size: 12px; color: var(--muted); }
"""


def render(chat, run_id, thread, twin_base, saved, snaps, out_path):
    parts_html = []
    prev_owner_ts = None
    owner_index = 0
    prev_snapshot = None
    prev_ts = None

    for msg in chat.get("messages", []):
        inbound = msg["direction"] == "inbound"
        ts = parse_ts(msg.get("sent_at"))
        texts = [p for p in msg.get("parts", []) if p.get("type") == "text"]
        media = [p for p in msg.get("parts", []) if p.get("type") == "media"]
        noise = bool(texts) and all(is_noise(p.get("value", "")) for p in texts)

        # Latency is the interesting number only on the agent's side, and only
        # against the owner turn that provoked it -- agent-to-agent gaps are
        # just the model still talking.
        latency = ""
        if not inbound and ts and prev_owner_ts:
            latency = f" · {int((ts - prev_owner_ts).total_seconds())}s after owner"
        elif not inbound and ts and prev_ts:
            latency = f" · +{int((ts - prev_ts).total_seconds())}s"

        who = "owner" if inbound else "agent"
        classes = f"row {who}" + (" noise" if noise else "")
        body = []
        for part in texts:
            body.append(f'<div class="bubble">{html.escape(part.get("value", ""))}</div>')
        for part in media:
            dest, mime, err = saved.get(id(part), (None, None, "not downloaded"))
            name = html.escape(part.get("filename") or "attachment")
            if dest is None:
                body.append(
                    f'<div class="bubble att"><div class="missing">[{name} — '
                    f"could not fetch: {html.escape(str(err))}]</div></div>"
                )
                continue
            uri = data_uri(dest, mime)
            size = dest.stat().st_size
            body.append(
                f'<div class="att"><img src="{uri}" alt="{name}">'
                f'<div class="cap">{name} · {mime} · {size:,} bytes</div></div>'
            )
        stamp = html.escape(msg.get("sent_at", ""))
        parts_html.append(
            f'<div class="{classes}"><div class="stack">' + "".join(body)
            + f'<div class="meta">{stamp}<span class="latency">{latency}</span></div>'
            + "</div></div>"
        )

        if inbound:
            prev_owner_ts = ts
            owner_index += 1
            if owner_index in snaps:
                after = snaps[owner_index]
                diff = config_diff(prev_snapshot, after)
                if diff:
                    lines = []
                    for line in diff:
                        cls = ("d-hd" if line.startswith(("---", "+++", "@@"))
                               else "d-add" if line.startswith("+")
                               else "d-del" if line.startswith("-") else "")
                        lines.append(f'<span class="{cls}">{html.escape(line)}</span>')
                    inner = f"<pre>{chr(10).join(lines)}</pre>"
                else:
                    inner = '<div class="nodiff">no change to config.json</div>'
                parts_html.append(
                    f'<details class="cfg"><summary>config.json after owner turn '
                    f"{owner_index}</summary>{inner}</details>"
                )
                prev_snapshot = after
        if ts:
            prev_ts = ts

    counts = {"owner": 0, "agent": 0, "media": 0}
    for msg in chat.get("messages", []):
        counts["owner" if msg["direction"] == "inbound" else "agent"] += 1
        counts["media"] += sum(1 for p in msg.get("parts", []) if p.get("type") == "media")

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(run_id)} — onboarding v2 e2e</title>
<style>{CSS}</style></head><body><main>
<header>
<h1>{html.escape(run_id)}</h1>
<dl>
<dt>thread</dt><dd>{html.escape(thread)}</dd>
<dt>twin</dt><dd>{html.escape(twin_base)}</dd>
<dt>messages</dt><dd>{counts['owner']} owner · {counts['agent']} agent · {counts['media']} attachments</dd>
<dt>rendered</dt><dd>{datetime.now().astimezone().isoformat(timespec='seconds')}</dd>
</dl>
</header>
{''.join(parts_html)}
</main></body></html>
"""
    out_path.write_text(doc)
    return counts


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--thread", help="twin thread id (chat_N); defaults to .env TWIN_THREAD")
    ap.add_argument("--twin", help="twin base URL; defaults to .env TWIN_HOST_BASE")
    ap.add_argument("--transcript", help="render a saved transcript.json instead of fetching")
    ap.add_argument("--snapshots", help="directory of after-<N>.json config snapshots")
    ap.add_argument("--runs-dir", default=str(DEFAULT_RUNS))
    args = ap.parse_args()

    env = load_env()
    twin = args.twin or env.get("TWIN_HOST_BASE")
    thread = args.thread or env.get("TWIN_THREAD")

    out_dir = Path(args.runs_dir) / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.transcript:
        chat = json.loads(Path(args.transcript).read_text())
        thread = thread or chat.get("id", "?")
    else:
        if not twin or not thread:
            sys.exit("need --twin/--thread (or TWIN_HOST_BASE/TWIN_THREAD in scripts/e2e/.env)")
        chat = fetch_json(f"{twin.rstrip('/')}/ui/chats/{thread}")

    (out_dir / "transcript.json").write_text(json.dumps(chat, indent=2))
    print(f"  saved transcript.json ({len(chat.get('messages', []))} messages)")

    saved = download_attachments(chat, twin, out_dir / "attachments") if twin else {}
    snaps = load_snapshots(args.snapshots or (out_dir / "snapshots"
                                              if (out_dir / "snapshots").is_dir() else None))
    report = out_dir / "report.html"
    counts = render(chat, args.run_id, thread or "?", twin or "(offline)", saved, snaps, report)
    print(f"  wrote {report} — {counts['owner']} owner, {counts['agent']} agent, "
          f"{counts['media']} attachments, {len(snaps)} config snapshots")


if __name__ == "__main__":
    main()
