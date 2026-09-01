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
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)
from ld_config_gate import GateError, gate  # noqa: E402

CONFIG = "/opt/data/ld/config.json"
# Every key a patch may name, at every depth, taken from the committed template
# rather than restated here -- one list to keep in step instead of two. A patch
# is composed by a model from a sentence, and a misspelling merges in BESIDE the
# real key rather than failing: {"family":{"owner":{"nme":"Ro"}}} passes the gate
# on the old name and reports success while the name never changes. A top-level
# check only catches the shallow half of that.
EXAMPLE = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "..", "..", "ld-shared", "references", "config.example.json")
# Keyless, like NWS and ESPN. count=1: the first match is the one the owner
# meant often enough, and a wrong one shows as the wrong city name on the card.
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search?count=1&name="
REQUIRED = ("owner_name", "owner_email", "city", "timezone", "has_mac")


def _reject_constant(token):
    """Refuse JSON's non-standard NaN/Infinity tokens, at every door.

    Python's parser accepts them and its writer emits them back, so a patch
    carrying `Infinity` passes the shared gate (`float("inf") > 0` is true) and
    is written out verbatim -- at which point ld_config_gate.py's own reader,
    which already refuses them, calls the live config "not valid JSON" and
    stands every producer down. Refusing at the read keeps the one config this
    writes something every reader of it can parse.
    """
    raise ValueError(f"non-standard JSON constant {token}")


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


def check_keys(patch, reference, path=""):
    """Refuse any key the template does not have, at any depth.

    Objects inside a list are checked against the template's first object entry
    -- config.example.json carries a representative one for every list that
    holds objects. A template list with no object to compare against is left
    alone rather than guessed at.
    """
    for key, value in patch.items():
        if key not in reference:
            raise SystemExit(
                f"refusing to patch: unknown config key {path + str(key)!r} -- "
                "no such key in ld-shared/references/config.example.json")
        expected = reference[key]
        if isinstance(value, dict) and isinstance(expected, dict):
            check_keys(value, expected, f"{path}{key}.")
        elif isinstance(value, list) and isinstance(expected, list):
            shape = next((e for e in expected if isinstance(e, dict)), None)
            if shape is not None:
                for index, entry in enumerate(value):
                    if isinstance(entry, dict):
                        check_keys(entry, shape, f"{path}{key}[{index}].")


def apply_patch(patch, current, env, geocoder=None):
    """The live config with `patch` merged in, or SystemExit naming the refusal."""
    if not isinstance(patch, dict):
        raise SystemExit("refusing to patch: the patch is not a JSON object")
    try:
        with open(EXAMPLE, encoding="utf-8") as f:
            reference = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"refusing to patch: could not read {EXAMPLE}: {exc}") from None
    check_keys(patch, reference)
    if not isinstance(current, dict):
        raise SystemExit(
            "refusing to patch: there is no config to patch yet -- run the "
            "interview (this script with no --patch) first")

    merged = deep_merge(current, patch)

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


def atomic_write(config_path, text):
    """Replace the config in one step, or leave the old one untouched.

    A truncate-then-write destroys the file before the replacement exists, so
    ENOSPC or a kill in that window leaves an empty or half-written config.
    The first-run path could survive that -- the owner had just answered every
    question, so re-running rebuilds it -- but a patch cannot: the file IS the
    only copy of every preference the owner is not currently restating, and
    preserving those is the whole reason --patch exists. An unreadable config
    also stands every producer down at once (the shared gate refuses it), so
    the failure presents as a wall that quietly stops updating.

    So: a fresh file in the same directory (same filesystem, or os.replace is
    not atomic), chmod BEFORE the PII-bearing content goes in, fsync so the
    bytes are durable before the rename publishes them, then one os.replace.
    A reader sees the old config or the new one, never neither.

    Nothing is validated here: the caller serializes with allow_nan=False, and
    json.dumps output is valid JSON by construction otherwise, so re-reading
    the file back would be a second validation contract around a single
    caller.
    """
    directory = os.path.dirname(config_path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{os.path.basename(config_path)}.", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, config_path)
    except BaseException:
        os.unlink(temporary)
        raise


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
    try:
        payload = json.load(sys.stdin if stdin is None else stdin,
                            parse_constant=_reject_constant)
    except ValueError as exc:
        raise SystemExit(f"refusing to write: {exc}") from None
    if args.patch:
        try:
            with open(config_path, encoding="utf-8") as f:
                current = json.load(f, parse_constant=_reject_constant)
        except (OSError, ValueError) as exc:
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
    # allow_nan=False, the writer's half of the rule the two reads enforce:
    # Python emits NaN/Infinity back out as bare tokens, and a config carrying
    # one is refused by ld_config_gate.py's own strict reader -- which stands
    # every producer down at once, silently. Nothing reaches here carrying one
    # today; this keeps that true if a future path forgets.
    try:
        text = json.dumps(config, indent=2, allow_nan=False) + "\n"
    except ValueError as exc:
        raise SystemExit(f"refusing to write: {exc}") from None
    atomic_write(config_path, text)
    print(f"wrote {config_path} (mode 600); gate: PASS")
    return 0


if __name__ == "__main__":
    # sys.argv[1:] explicitly, never argparse's implicit fallback: main() is
    # also called as a library (the tests), where the implicit fallback would
    # pick up pytest's own argv. Passing [] here instead -- as this line did
    # before --patch existed -- silently drops the flag and sends a partial
    # config through the first-run path, which refuses it as missing answers.
    sys.exit(main(sys.argv[1:]))
