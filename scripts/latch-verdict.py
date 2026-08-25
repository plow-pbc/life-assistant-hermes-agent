#!/usr/bin/env python3
"""Decide what a Latch probe's HTTP status + body actually mean.

Split from the probe itself so this can be tested directly. The probe has to run
inside the container — that is the thing which must reach the relay — but the
*verdict* is pure text, and a verdict nobody can test is how a health check ends
up able only to lie.

The HTTP status alone is not the answer. This endpoint is JSON-RPC over MCP
streamable-HTTP: a Mac that is switched off, a Latch that is not running, and a
relay that cannot forward all come back as **HTTP 200 carrying an `error`
object**. Asserting on the status would report "reachable" for a machine nobody
is home at.

Usage: latch-verdict.py <probe_file>   → prints a line, exits 0 on ok

The probe file is the container's combined output: the HTTP status on the first
line, the response body (if any) after it. Splitting here rather than in shell
is deliberate — the no-body case has no newline to split on, and getting that
wrong in shell echoed the status back as the body.
"""
import json
import sys


def split_probe(text: str) -> tuple[str, str]:
    """First line is the status, the rest is the body.

    A transport failure writes no body and therefore no newline, so a shell
    `${x#*\n}` split does not strip and hands the status back as the body —
    turning the operator's one actionable line into "HTTP 000: 000".
    """
    code, sep, body = text.partition("\n")
    return code.strip(), body if sep else ""


def verdict(code: str, raw: str) -> str:
    """Return the success line, or raise SystemExit with the reason it failed."""
    if code == "401":
        raise SystemExit(
            "DOMO_MCP_TOKEN is REVOKED — mint a fresh agent credential from Rowan's Mac"
        )
    if code == "406":
        # The probe sends Accept: application/json, text/event-stream. If the
        # relay still refuses it, the probe was edited rather than the token
        # being wrong — say so, or this sends someone to the wrong machine.
        raise SystemExit(
            "relay refused the Accept header — the probe sends it, so the probe was edited"
        )
    if code == "000":
        raise SystemExit("no answer from api.plow.co — the credential was NOT tested")
    if not code.startswith("2"):
        raise SystemExit("relay returned HTTP %s: %s" % (code, raw[:200]))

    # streamable-HTTP frames the JSON on `data:` lines; a plain JSON body also works.
    payload = "".join(l[6:] for l in raw.splitlines() if l.startswith("data: ")) or raw
    try:
        d = json.loads(payload)
    except ValueError:
        raise SystemExit(
            "relay returned HTTP %s but an unparseable body: %s" % (code, raw[:200])
        )
    if "error" in d:
        e = d["error"] or {}
        raise SystemExit(
            "Rowan's Mac did not answer: %s (code %s) — is Latch running on it?"
            % (e.get("message", "?"), e.get("code", "?"))
        )
    tools = (d.get("result") or {}).get("tools") or []
    if not tools:
        raise SystemExit(
            "relay answered HTTP %s but listed no tools — Latch is exposing nothing" % code
        )
    return "latch reachable: Rowan's Mac answered with %d tools (%s…)" % (
        len(tools),
        ", ".join(t.get("name", "?") for t in tools[:3]),
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(verdict(*split_probe(open(sys.argv[1]).read())))
