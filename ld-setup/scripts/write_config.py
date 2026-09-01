#!/usr/bin/env python3
"""write_config.py -- the ld-setup interview's answers, written as /opt/data/ld/config.json.

TWO modes, one file, because they must not disagree about what a valid config
is. Both read ONE JSON object on stdin (the agent composes it from the owner's
replies; nothing reaches argv), both judge the result by the shared gate --
ld_config_gate.gate() imported, not restated -- BEFORE it is written, and both
write mode 600 because family.owner and the calendar ids are a person's data.

  (default)  the first-run interview. Stdin is the ANSWER set (owner_name,
             owner_email, city, ...) and the whole config is built from it.

  --patch    a later change. Stdin is a PARTIAL CONFIG -- the same shape
             config.example.json describes, carrying only what changes -- and
             it is deep-merged onto the live file. This is what makes "we
             moved to Denver" or "add my partner's calendar" one turn instead
             of a re-run of the whole interview that silently resets every
             answer the owner is not restating.

             Lists REPLACE rather than append: sports.followed and
             calendar.sources are sets the owner states in full ("follow the
             Cubs and the Bears"), and a patch that could only grow them would
             have no way to drop one. Nested objects merge key by key, so
             {"weather": {"location": "Denver"}} keeps lat/lon's siblings.

The timezone is checked against the container's TZ in both modes rather than
left to register_crons.py (which also refuses): the owner is the one answering,
and the fix is AGENT_TZ in the instance dotenv on the HOST, which only the
operator can edit -- so the refusal names it for the owner to relay.

A patch also re-registers the crons. A setting that was blank is why a producer
has no job, so the change that fills it is exactly the moment its job should
appear -- and register_crons.py is idempotent, so a patch that changed nothing
scheduling-shaped is a no-op there. The first-run path does NOT: Phase 4 of
SKILL.md owns that registration and proves a card after it.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
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
REGISTER_CRONS = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "..", "..", "ld-dashboard", "scripts", "register_crons.py")
# The whole top level, closed on purpose. A patch is composed by a model from a
# sentence, and a misspelled section ({"wether": ...}) would otherwise merge in
# as dead config that the gate has no opinion about -- the owner is told the
# change landed while the card keeps showing the old city.
SECTIONS = ("family", "calendar", "weekly_digest", "morning_triage",
            "calendar_nudge", "weather", "sports")
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
        "calendar": {"account": answers["owner_email"], "sources": sources},
        "weekly_digest": {"length": answers.get("digest_length") or "", "long_lead": []},
        "morning_triage": {"chat_db_path": chat_db, "ranking_instructions": "",
                           "exclude": {"imessage_handles": []}},
        "calendar_nudge": {"lookahead_virtual_minutes": 30, "lookahead_in_person_minutes": 60,
                           "owner_identities": [answers["owner_email"]]},
        "weather": {"location": answers["city"], "lat": lat, "lon": lon},
        "sports": {"followed": list(answers.get("teams") or [])},
    }


def deep_merge(current, patch):
    """`patch` over `current`, key by key; a non-dict value replaces."""
    merged = dict(current)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def apply_patch(patch, current, env, geocoder=None):
    """The live config with `patch` merged in, or SystemExit naming the refusal."""
    if not isinstance(patch, dict):
        raise SystemExit("refusing to patch: the patch is not a JSON object")
    unknown = [k for k in patch if k not in SECTIONS]
    if unknown:
        raise SystemExit(
            f"refusing to patch: unknown config section(s): {', '.join(sorted(unknown))} "
            f"-- the config has only: {', '.join(SECTIONS)}")
    if not isinstance(current, dict):
        raise SystemExit(
            "refusing to patch: there is no config to patch yet -- run the "
            "interview (this script with no --patch) first")

    merged = deep_merge(copy.deepcopy(current), patch)

    # Same refusal as build(), because it is the same mistake: the zone is the
    # host's AGENT_TZ, and a config that disagrees with the container puts
    # every card at the wrong local hour without failing anything.
    container = (env.get("TZ") or "").strip()
    zone = merged.get("family", {}).get("timezone")
    if zone != container:
        raise SystemExit(
            f"refusing to patch: the config would say {zone!r} but this container "
            f"runs in {container!r}. The zone is AGENT_TZ in the instance dotenv on "
            "the host -- tell the owner to ask the operator to change it.")

    # A location without its coordinates is the one patch that fails silently:
    # the card's title changes to the new city and the forecast stays the old
    # one's. Geocode it here rather than asking a model for a lat/lon.
    weather = patch.get("weather") or {}
    if "location" in weather and not {"lat", "lon"} <= set(weather):
        lat, lon = (geocoder or geocode)(merged["weather"]["location"])
        merged["weather"]["lat"], merged["weather"]["lon"] = lat, lon

    return merged


def main(argv=None, env=None, stdin=None, config_path=CONFIG):
    # No real CLI args are ever expected (stdin carries the answers) -- default
    # to [] rather than argparse's usual sys.argv fallback, which would pick up
    # a caller's own argv (e.g. pytest's) when main() is invoked as a library.
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--patch", action="store_true",
        help="stdin is a partial config to merge onto the live one, not the answer set")
    args = parser.parse_args(argv or [])
    env = os.environ if env is None else env
    payload = json.load(sys.stdin if stdin is None else stdin)
    if args.patch:
        try:
            with open(config_path, encoding="utf-8") as f:
                current = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(
                f"refusing to patch: could not read {config_path}: {exc}") from None
        config = apply_patch(payload, current, env)
    else:
        config = build(payload, env)
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
    if not args.patch:
        return 0

    # Registration is reported, never swallowed: a patch that filled the last
    # missing field for a producer has not actually turned that producer on
    # until its job exists, and the chat turn does not propagate an exit code.
    proc = subprocess.run([sys.executable, os.path.realpath(REGISTER_CRONS)],
                          capture_output=True, text=True)
    print((proc.stdout + proc.stderr).strip()
          or "register_crons.py said nothing")
    if proc.returncode != 0:
        print("the change was saved, but schedule registration failed")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
