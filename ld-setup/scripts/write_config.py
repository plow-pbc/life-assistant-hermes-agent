#!/usr/bin/env python3
"""write_config.py -- the ld-setup interview's answers, written as /opt/data/ld/config.json.

Answers arrive as ONE JSON object on stdin (the agent composes it from the
owner's replies; nothing reaches argv). The result is the shape
ld-shared/references/config.example.json describes, judged by the shared gate
-- ld_config_gate.gate() imported, not restated -- BEFORE it is written, and
written mode 600 because family.owner and the calendar ids are a person's data.

The timezone is checked against the container's TZ here rather than left to
register_crons.py (which also refuses): the owner is the one answering, and
the fix is AGENT_TZ in the instance dotenv on the HOST, which only the
operator can edit -- so the refusal names it for the owner to relay.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)
from ld_config_gate import GateError, gate  # noqa: E402

CONFIG = "/opt/data/ld/config.json"
# Keyless, like NWS and ESPN. count=1: the first match is the one the owner
# meant often enough, and a wrong one shows as the wrong city name on the card.
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search?count=1&name="
REQUIRED = ("owner_name", "owner_email", "city", "timezone", "has_mac")


def geocode(city):
    """city -> (lat, lon), or a refusal the agent can relay."""
    try:
        with urllib.request.urlopen(GEOCODE_URL + urllib.parse.quote(city), timeout=30) as resp:
            results = json.load(resp).get("results") or []
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise SystemExit(f"refusing to write: could not look up {city!r}: {exc}") from None
    if not results:
        raise SystemExit(f"refusing to write: no place matches {city!r} -- ask the owner for a nearby city")
    return results[0]["latitude"], results[0]["longitude"]


def build(answers, env, geocoder=None):
    """The config for a set of answers, or SystemExit naming what is wrong."""
    # has_mac gates the Messages db path, so a truthy non-bool ("no", 0) would
    # decide it silently -- it has to arrive as a real boolean or not at all.
    missing = [k for k in REQUIRED if answers.get(k) in (None, "")]
    if "has_mac" not in missing and not isinstance(answers["has_mac"], bool):
        missing.append("has_mac")
    if answers.get("has_mac") is True and not (answers.get("mac_username") or "").strip():
        missing.append("mac_username")
    if missing:
        raise SystemExit(f"refusing to write: missing required answer(s): {', '.join(missing)}")
    container = (env.get("TZ") or "").strip()
    if answers["timezone"] != container:
        raise SystemExit(
            f"refusing to write: the owner says {answers['timezone']!r} but this container "
            f"runs in {container!r}. The zone is AGENT_TZ in the instance dotenv on the host "
            "-- tell the owner to ask the operator to change it, then run setup again."
        )
    lat, lon = (geocoder or geocode)(answers["city"])
    sources = [{"calendar_id": answers["owner_email"], "name": "Personal"}]
    sources += [{"calendar_id": c, "name": c} for c in (answers.get("extra_calendar_ids") or [])]
    chat_db = (f"/Users/{answers['mac_username']}/Library/Messages/chat.db"
               if answers["has_mac"] and answers.get("mac_username") else "")
    return {
        "family": {
            "owner": {"name": answers["owner_name"], "imessage": answers.get("owner_imessage") or ""},
            "people": list(answers.get("people") or []),
            "timezone": answers["timezone"],
        },
        "calendar": {"sources": sources},
        "weekly_digest": {"length": answers.get("digest_length") or "", "long_lead": []},
        "morning_triage": {"chat_db_path": chat_db, "ranking_instructions": "",
                           "exclude": {"imessage_handles": []}},
        "calendar_nudge": {"lookahead_virtual_minutes": 30, "lookahead_in_person_minutes": 60,
                           "owner_identities": [answers["owner_email"]]},
        "weather": {"location": answers["city"], "lat": lat, "lon": lon},
        "sports": {"followed": list(answers.get("teams") or [])},
    }


def main(argv=None, env=None, stdin=None, config_path=CONFIG):
    # No real CLI args are ever expected (stdin carries the answers) -- default
    # to [] rather than argparse's usual sys.argv fallback, which would pick up
    # a caller's own argv (e.g. pytest's) when main() is invoked as a library.
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv or [])
    env = os.environ if env is None else env
    config = build(json.load(sys.stdin if stdin is None else stdin), env)
    try:
        verdict = gate(config)
    except GateError as exc:
        verdict = f"not valid JSON ({exc})"
    if verdict:
        raise SystemExit(f"refusing to write: the gate says: {verdict}")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    # O_CREAT's mode only applies to a NEW file; a rewrite of an existing,
    # looser-permissioned file needs fchmod BEFORE the PII-bearing write, not
    # after -- otherwise the content is briefly readable at the old mode.
    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    print(f"wrote {config_path} (mode 600); gate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
