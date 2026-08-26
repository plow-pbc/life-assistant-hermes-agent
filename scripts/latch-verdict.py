#!/usr/bin/env python3
"""Did Rowan's Mac answer with tools? If not, show exactly what came back.

Usage: latch-verdict.py <probe_file>   → prints a line, exits 0 on ok

The probe file is the container's combined output: HTTP status on the first
line, response body after it. Splitting here rather than in shell is deliberate
— a transport failure writes no body and therefore no newline, and getting that
wrong in shell echoed the status back as the body.

This deliberately does NOT classify why a probe failed. An earlier version did,
and every review round found one more legal-but-unhandled shape it was
mislabelling: a two-frame SSE answer reported as "unparseable", valid JSON with
no response frame reported as "unparseable", a doubled `000` reported as HTTP
000000. Four rounds of that on ~50 lines. The failure was structural — a cause
taxonomy has to enumerate the whole input space correctly or it lies, and it
kept lying somewhere new each round.

So: one question, and the raw response as the evidence. A 401 body already says
the token is bad, a 406 body already names the Accept header, and a JSON-RPC
error already carries its own message. Printing them beats paraphrasing them,
and it cannot be wrong about a shape nobody anticipated.
"""
import json
import sys


def split_probe(text: str) -> tuple[str, str]:
    r"""First line is the status, the rest is the body (empty when there is none).

    `partition`, not `split("\n", 1)`: a transport failure writes no body and so
    no newline, where split returns a one-element list and raises on unpack.
    partition yields an empty body instead — which is the whole point, and the
    reason the shell version of this shipped the status back as the body twice.
    """
    code, _, body = text.partition("\n")
    return code.strip(), body


def _payloads(raw: str):
    """Each JSON value in the body: every `data:` frame, else the bare body.

    The space after `data:` is optional, and the server may emit notifications
    before the response — so frames are parsed one at a time rather than joined,
    which is what turned a legal two-frame answer into `{..}{..}`.
    """
    seen = False
    for line in raw.splitlines():
        if line.startswith("data:"):
            seen = True
            try:
                yield json.loads(line[5:].lstrip(" "))
            except ValueError:
                continue
    if not seen:
        try:
            yield json.loads(raw)
        except ValueError:
            return


def verdict(code: str, raw: str) -> str:
    """The success line, or SystemExit carrying the response verbatim.

    Every unwrap here checks its shape rather than its key. This module's whole
    claim is that it cannot be wrong about a body nobody anticipated, and key
    checks do not deliver that: `{"result": "ok"}` crashed on `.get`, and a
    string-valued `tools` reported `len("nope")` — four tools — as a *success*,
    which is worse than crashing. A non-conforming body is simply not an answer.
    """
    for frame in _payloads(raw):
        if not isinstance(frame, dict):
            continue
        result = frame.get("result")
        tools = result.get("tools") if isinstance(result, dict) else None
        if isinstance(tools, list) and tools:
            # str(), because `{"name": null}` and `{"name": 7}` both survive
            # .get() and then blow up inside join() — the last unchecked unwrap
            # on this path, and the same class as the two above it.
            names = [
                str(t.get("name") or "?") for t in tools[:3] if isinstance(t, dict)
            ]
            return "latch reachable: Rowan's Mac answered with %d tools (%s…)" % (
                len(tools),
                ", ".join(names) if names else "unnamed",
            )
    # Whole body, not raw[:600]. The response IS the diagnosis here — that is
    # the entire trade this module makes in place of a cause taxonomy — and a
    # cap silently drops the line that explains the failure exactly when the
    # body is verbose enough to need explaining. Failure bodies are relay errors
    # and proxy pages, not the (unprinted) success payload.
    raise SystemExit(
        "latch did NOT answer with a tool list — HTTP %s. What came back:\n%s"
        % (code, raw if raw.strip() else "(empty body)")
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(verdict(*split_probe(open(sys.argv[1]).read())))
