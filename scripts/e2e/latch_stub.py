#!/usr/bin/env python3
"""A fake Plow Latch relay, so calendar selection can be tested without a Mac.

ld-setup's calendar step runs one command on the owner's machine through Latch:

    plow_run_command(argv=["gog", "calendar", "calendars", "--json", "--results-only"])

Everything downstream of that -- the pick message, calendar_list.py, what
write_config.py stores -- was unreachable in the loop, because the far end of a
real relay is somebody's actual Mac. This speaks the same protocol and answers
that one command with a synthetic listing, so the whole path can be exercised
with nothing at risk.

It is a stub, not a simulator. It runs the argv it was given nowhere; it has no
credential, no Mac, and no way to reach one.

    scripts/e2e/run-agent.sh --latch-stub          # normal listing
    STUB_MODE=offline scripts/e2e/run-agent.sh --latch-stub
    STUB_MODE=large   scripts/e2e/run-agent.sh --latch-stub

Protocol: MCP over streamable HTTP -- JSON-RPC POSTed to one URL, which is what
`mcp_servers.latch` in Hermes' config.yaml means. initialize / tools/list /
tools/call, answered as application/json.

The listing is built to be awkward on purpose, because every one of these has a
way to be parsed wrongly and quietly:

  * a preamble line before the JSON, so the output is not valid JSON
  * exactly one `primary`, the signal the account is derived from -- and the
    owner-role rows all name that same dataOwner, so the consumer's fallback
    branch agrees with its first one instead of contradicting it
  * three distinct dataOwner values, so deriving the account from dataOwner
    picks whichever calendar was read last
  * a summaryOverride that differs from summary, which is what the owner sees
  * two ICS imports, whose ids do not look like the group-calendar form
  * one calendar named with a newline and a shell metacharacter, which must
    reach the owner as text and never a shell or the config
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROTOCOL_VERSION = "2025-06-18"
# The server KEY. The tool it exposes is `plow_run_command` -- a different
# string, and the pair is what the skills spell out as
# mcp__latch__plow_run_command. Must match what the base seed and
# runtime/config.yaml register, or the skills name a tool nothing serves.
SERVER_NAME = "latch"

# gog prints this before the array on a working call. Kept because it is the
# reason calendar_list.py cannot simply json.loads() the output -- a stub that
# returned clean JSON would let a parser that gets this wrong pass.
PREAMBLE = "Note: Using direct access token (expires in ~1 hour; no auto-refresh)\n"

ACCOUNT = "mary@example.com"

# The refusal a real relay gives for anything outside its two allowed surfaces,
# verbatim -- ld-setup reads this string back to the owner.
AUTH_REFUSAL = "this Mac reaches only Gmail and Calendar through plow-gog"

DISCOVERY_ARGV = ["gog", "calendar", "calendars", "--json", "--results-only"]


def _calendar(cid, summary, *, access="reader", owner=ACCOUNT, primary=False,
              override=None, selected=True, tz="America/Los_Angeles",
              description=""):
    """One entry with the full field set a real listing carries.

    `primary` is present only on the primary calendar, which is what Google
    does -- a stub that wrote `"primary": false` everywhere would hide a
    consumer that tests for presence rather than truth.
    """
    entry = {
        "accessRole": access,
        "backgroundColor": "#9fe1e7",
        "colorId": "14",
        "conferenceProperties": {"allowedConferenceSolutionTypes": ["hangoutsMeet"]},
        "dataOwner": owner,
        "defaultReminders": [{"method": "popup", "minutes": 10}],
        "description": description,
        # crc32, not hash(): str hashing is salted per process, so etags
        # would differ between runs and a fixture could never pin one.
        "etag": '"%d"' % zlib.crc32(cid.encode()),
        "externalContent": False,
        "foregroundColor": "#000000",
        "id": cid,
        "kind": "calendar#calendarListEntry",
        "notificationSettings": {
            "notifications": [
                {"type": "eventCreation", "method": "email"},
                {"type": "eventChange", "method": "email"},
            ]
        },
        "selected": selected,
        "summary": summary,
        "timeZone": tz,
    }
    if primary:
        entry["primary"] = True
    if override is not None:
        entry["summaryOverride"] = override
    return entry


# Nine calendars: one primary (the account), one summaryOverride, an owner/reader
# mix, three distinct dataOwner values, two ICS imports, and one hostile name.
CALENDARS = [
    _calendar(ACCOUNT, ACCOUNT, access="owner", owner=ACCOUNT, primary=True,
              description="The account's own calendar"),
    _calendar("fam9d2c@group.calendar.google.com", "Family Calendar",
              access="owner", owner=ACCOUNT, override="Ours",
              description="Shared with the household"),
    # The whole reason `display` is documented as untrusted. A newline splits it
    # across lines wherever it is printed, and `; rm -rf /` is a shell command if
    # anything ever interpolates a calendar name into one. It must arrive at the
    # owner as text and never reach a shell or config.json.
    _calendar("hostile41b@group.calendar.google.com", "Family\nJSON\n; rm -rf /",
              access="reader", owner="neighbour@example.net",
              description="Named to prove names stay data"),
    _calendar("school77a@group.calendar.google.com", "School Term Dates",
              access="reader", owner="office@school.example.org"),
    _calendar("soccer3f1@group.calendar.google.com", "Soccer",
              access="owner", owner=ACCOUNT),
    _calendar("neighbour@example.net", "Dan Rivera",
              access="reader", owner="neighbour@example.net"),
    # ICS subscriptions. Their ids are the import form, not the group form, and
    # a filter that matches only `@group.calendar.google.com` drops them.
    _calendar("f4a1c0de3b@import.calendar.google.com", "US Holidays",
              access="reader", owner=ACCOUNT, selected=False),
    _calendar("8b7d2e91aa@import.calendar.google.com", "Trash & Recycling",
              access="reader", owner="office@school.example.org"),
    _calendar("birthdays@group.v.calendar.google.com", "Birthdays",
              access="reader", owner=ACCOUNT, selected=False),
]

TOOL = {
    "name": "plow_run_command",
    "description": "Run one command on the paired Mac and return its result.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "argv": {"type": "array", "items": {"type": "string"},
                     "description": "Argv, exec'd directly -- not through a shell."},
            "network": {"type": "boolean",
                        "description": "Allow the command to leave the Mac."},
            "timeout": {"type": "integer",
                        "description": "Milliseconds; over it, a job handle comes back."},
        },
        "required": ["argv"],
    },
}


def listing_text(mode):
    """The command's stdout: gog's preamble, then the JSON array.

    `large` pads to something the RUNTIME will persist. The relay never returns
    a path -- Hermes decides an oversized tool result goes to
    /tmp/hermes-results/call_<id>.txt and hands the model that instead. A stub
    that returned the persisted envelope itself would be inventing a shape no
    relay produces, and would pass a consumer that could not read a real one. So
    this returns genuinely large output and lets the real persistence run.
    """
    calendars = CALENDARS
    if mode == "large":
        calendars = []
        for i in range(600):
            base = dict(CALENDARS[i % len(CALENDARS)])
            if not base.get("primary"):
                base["id"] = f"bulk{i:04d}@group.calendar.google.com"
                base["summary"] = f"Bulk Calendar {i:04d}"
                base["description"] = "padding. " * 40
                base.pop("summaryOverride", None)
                calendars.append(base)
        calendars.insert(0, CALENDARS[0])
    return PREAMBLE + json.dumps(calendars, indent=2)


def run_command(arguments, mode):
    """The Latch envelope for one plow_run_command call."""
    argv = arguments.get("argv")
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        return {"isError": True, "content": [{"type": "text",
                "text": "argv must be a list of strings"}]}

    if argv == DISCOVERY_ARGV:
        output = listing_text(mode)
        envelope = {
            "exit_code": 0,
            "handle": "stub-0001",
            "output": output,
            "output_length": len(output),
            "status": "completed",
        }
        return {"content": [{"type": "text", "text": json.dumps(envelope)}]}

    # `gog auth ...` is the one wrong turn ld-setup is most likely to take, and
    # the real relay's refusal is a specific sentence the skill quotes back.
    if argv[:2] == ["gog", "auth"] or (argv and argv[0] == "gog" and "auth" in argv[:3]):
        return {"isError": True,
                "content": [{"type": "text", "text": json.dumps({"error": AUTH_REFUSAL})}]}

    return {"isError": True, "content": [{"type": "text", "text": json.dumps(
        {"error": "the stub relay runs only the calendar listing; "
                  f"refused: {' '.join(argv)[:200]}"})}]}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "latch-stub"

    # Quiet by default; --verbose puts one line per call on stderr, which is how
    # you see whether the agent ever called the tool at all.
    def log_message(self, fmt, *args):
        if self.server.verbose:
            sys.stderr.write("latch-stub: " + fmt % args + "\n")

    def _reply(self, status, payload, ctype="application/json"):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Mcp-Session-Id", "latch-stub-session")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        if not self.server.token:
            return True
        return self.headers.get("Authorization") == f"Bearer {self.server.token}"

    def do_GET(self):
        # A GET on the MCP endpoint opens the optional server->client SSE
        # stream. Nothing here pushes, so decline rather than hold a socket.
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)

        # Offline first, before auth and before parsing: an unpaired Mac
        # answers this way whatever you send it.
        if self.server.mode == "offline":
            self._reply(503, {"detail": f"{self.server.device} is not connected"})
            return

        if not self._authorized():
            self._reply(401, {"detail": "bad bearer"})
            return

        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            self._reply(400, {"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": "parse error"}})
            return

        method = request.get("method")
        rid = request.get("id")

        # Notifications carry no id and get no body.
        if rid is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                # serverInfo.name must match the config key, or the projected
                # tool names stop matching what the ld-* skills call.
                "serverInfo": {"name": SERVER_NAME, "version": "0.1.0-stub"},
            }
        elif method == "tools/list":
            result = {"tools": [TOOL]}
        elif method == "tools/call":
            params = request.get("params") or {}
            if params.get("name") != TOOL["name"]:
                self._reply(200, {"jsonrpc": "2.0", "id": rid, "error": {
                    "code": -32602, "message": f"unknown tool {params.get('name')!r}"}})
                return
            result = run_command(params.get("arguments") or {}, self.server.mode)
        elif method == "ping":
            result = {}
        else:
            self._reply(200, {"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32601, "message": f"method not found: {method}"}})
            return

        self._reply(200, {"jsonrpc": "2.0", "id": rid, "result": result})


def serve(host, port, *, mode, token, device, verbose, port_file=None):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.mode = mode
    httpd.token = token
    httpd.device = device
    httpd.verbose = verbose
    chosen = httpd.server_address[1]
    if port_file:
        with open(port_file, "w") as f:
            f.write(str(chosen))
    print(f"latch-stub: {host}:{chosen} mode={mode}", file=sys.stderr, flush=True)
    return httpd, chosen


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    # 0.0.0.0 by default because the point is to be reachable from the container
    # through host.docker.internal, which a loopback bind is not. The bearer is
    # what keeps it from being an open endpoint on the LAN.
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=0, help="0 picks a free one")
    ap.add_argument("--port-file", help="write the chosen port here")
    ap.add_argument("--token", default=os.environ.get("STUB_TOKEN", ""),
                    help="require this bearer; empty allows any")
    ap.add_argument("--mode", default=os.environ.get("STUB_MODE", "normal"),
                    choices=["normal", "offline", "large"])
    ap.add_argument("--device", default="plucas-mbp.local (2)",
                    help="device name in the offline message")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    httpd, _ = serve(args.host, args.port, mode=args.mode, token=args.token,
                     device=args.device, verbose=args.verbose,
                     port_file=args.port_file)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
