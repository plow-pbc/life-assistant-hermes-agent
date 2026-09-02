#!/usr/bin/env python3
"""write_config.py -- the ld-setup interview's answers, written as /opt/data/ld/config.json.

TWO modes, one file, because they must not disagree about what a valid config
is. Both read ONE JSON object on stdin (the agent composes it from the owner's
replies; nothing reaches argv) and both write mode 600, because family.owner
and the calendar ids are a person's data. Both judge the result by the shared
gate -- ld_config_gate.gate() imported, not restated -- before writing, and
differ only in what they excuse.

  --patch    a later change. Stdin is a PARTIAL CONFIG -- the same shape
             config.example.json describes, carrying only what changes -- and
             it is deep-merged onto the live file. This is what makes "we
             moved to Denver" or "add my partner's calendar" one turn instead
             of a re-run of the whole interview that silently resets every
             answer the owner is not restating.

  --draft    onboarding, one answer at a time. Stdin is a PARTIAL CONFIG like
             --patch's, merged the same way onto the live file -- but the file
             need not exist yet, and the shared gate is REPORTED rather than
             enforced.

             The exemption is for ABSENT keys ONLY, and that boundary is
             the mode. The gate demands calendar.account, a non-blank unique
             calendar_id per source, and a non-empty
             calendar_nudge.owner_identities. Onboarding never asks for any of
             them -- the calendar arrives later, through Latch's connectors --
             so a config carrying only the name, the city and the teams can
             never pass, and --patch would refuse every answer as it landed.
             Refusing to record what the owner just said, because of something
             they have not been asked yet, is what this avoids.

             It does NOT excuse a value that was supplied. Every check that
             judges something actually present is enforced exactly as --patch
             enforces it: a blank owner name, a placeholder left in, a
             duplicate calendar id, a lookahead that is not a positive number.
             Waving those through would be worse than refusing them, because
             the draft IS the record of progress -- a bad value written here
             reads on the next turn as a question already answered, and the
             owner is never asked again.

             Nothing downstream is loosened either: the producers still read
             the gate's verdict, a draft config still stands them down, and the
             wall path (SKILL.md Phases 2-4) still runs --patch under the full
             gate.

             Lists REPLACE rather than append: sports.followed and
             calendar.sources are sets the owner states in full ("follow the
             Cubs and the Bears"), and a patch that could only grow them would
             have no way to drop one. Nested objects merge key by key, so
             {"weather": {"location": "Denver"}} keeps lat/lon's siblings.

The timezone is checked against the container's TZ in both modes rather than
left to register_crons.py (which also refuses): the owner is the one answering,
and the fix is AGENT_TZ in the instance dotenv on the HOST, which only the
operator can edit -- so the refusal names it for the owner to relay.

NO mode touches the crons. This script writes one file and nothing else; the
six schedules are register_crons.py's, run from the wall phases of SKILL.md,
which prove a card after them. An earlier version of this paragraph claimed a
patch re-registered them -- it never did, in any released version, and the
claim outlived three readings of the file before anyone ran it. Nothing here is
gated on a producer being configured, so a settings change has no schedule to
add, and re-running the registration would fail an unrelated change on paused
cron state it has no business judging.
"""
from __future__ import annotations

import argparse
import copy
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



# What the gate requires, and a stand-in for each that its own check accepts.
# Only used to answer one question -- "would this config pass if the unasked
# questions had been answered?" -- so the values need to be valid and nothing
# more. Absent keys get filled; a key that is PRESENT is never touched, so a
# supplied value is always judged on its own merits.
_GATE_STANDINS = (
    (("family", "owner", "name"), "unasked"),
    (("family", "timezone"), "UTC"),
    (("calendar", "account"), "unasked@unasked.invalid"),
    (("calendar", "sources"), [{"calendar_id": "unasked@unasked.invalid", "name": "unasked"}]),
    (("calendar_nudge", "owner_identities"), ["unasked@unasked.invalid"]),
    (("calendar_nudge", "lookahead_virtual_minutes"), 30),
    (("calendar_nudge", "lookahead_in_person_minutes"), 60),
)


def fill_unasked(config):
    """A copy of `config` with the gate's required-but-ABSENT keys stood in for.

    Gating this copy asks the one question --draft actually needs answered: is
    anything wrong here something the owner has SAID, rather than something
    they have not been asked? A complaint that survives the fill can only come
    from a supplied value, so it is refused; a complaint the fill removes was
    an unasked question and is merely reported.

    Descends only through dicts. A non-dict where the gate expects an object is
    left exactly as it is -- that shape is itself a supplied value, and the
    gate is the right thing to judge it.
    """
    filled = copy.deepcopy(config)
    for path, standin in _GATE_STANDINS:
        node = filled
        for key in path[:-1]:
            if not isinstance(node, dict):
                break
            node = node.setdefault(key, {})
        else:
            if isinstance(node, dict) and path[-1] not in node:
                node[path[-1]] = standin
    return filled


def deep_merge(current, patch):
    """`patch` over `current`, key by key; a non-dict value replaces."""
    merged = dict(current)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def check_keys(patch, reference, path="", verb="patch"):
    """Refuse any key the template does not have, at any depth.

    Objects inside a list are checked against the template's first object entry
    -- config.example.json carries a representative one for every list that
    holds objects. A template list with no object to compare against is left
    alone rather than guessed at.
    """
    for key, value in patch.items():
        if key not in reference:
            raise SystemExit(
                f"refusing to {verb}: unknown config key {path + str(key)!r} -- "
                "no such key in ld-shared/references/config.example.json")
        expected = reference[key]
        if isinstance(value, dict) and isinstance(expected, dict):
            check_keys(value, expected, f"{path}{key}.")
        elif isinstance(value, list) and isinstance(expected, list):
            shape = next((e for e in expected if isinstance(e, dict)), None)
            if shape is not None:
                for index, entry in enumerate(value):
                    if isinstance(entry, dict):
                        check_keys(entry, shape, f"{path}{key}[{index}].", verb)


def apply_patch(patch, current, env, geocoder=None, gated=True):
    """The live config with `patch` merged in, or SystemExit naming the refusal.

    `gated=False` is --draft: the same merge and the same key check, onto a
    config that may not exist yet. Only the gate is relaxed; see the module
    docstring for why, and main() still prints its verdict.
    """
    verb = "patch" if gated else "draft"
    if not isinstance(patch, dict):
        raise SystemExit(f"refusing to {verb}: the patch is not a JSON object")
    try:
        with open(EXAMPLE, encoding="utf-8") as f:
            reference = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"refusing to {verb}: could not read {EXAMPLE}: {exc}") from None
    check_keys(patch, reference, verb=verb)
    if not isinstance(current, dict):
        if gated:
            raise SystemExit(
                "refusing to patch: there is no config to patch yet -- run the "
                "interview (this script with no --patch) first")
        # A draft is how the config comes into existence during onboarding, so
        # "no config yet" is the first answer, not an error.
        current = {}

    merged = deep_merge(current, patch)

    # Same refusal as build(), because it is the same mistake: the zone is the
    # host's AGENT_TZ, and a config that disagrees with the container puts
    # every card at the wrong local hour without failing anything.
    container = (env.get("TZ") or "").strip()
    zone = merged.get("family", {}).get("timezone")
    # A draft is written before every answer is in, so an absent zone is a
    # question not yet asked rather than a disagreement. One that IS present
    # gets the same refusal as a patch: a wrong zone is not more acceptable
    # for being early, and catching it here is what keeps the owner from
    # answering four more questions against a config that cannot be finished.
    if zone is not None or gated:
        if zone != container:
            raise SystemExit(
                f"refusing to {verb}: the config would say {zone!r} but this container "
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
    parser.add_argument(
        "--draft", action="store_true",
        help="like --patch, but the config need not exist yet and the gate is "
             "reported rather than enforced (onboarding, answer by answer)")
    args = parser.parse_args(argv or [])
    if args.patch and args.draft:
        raise SystemExit("refusing to write: pass --patch or --draft, not both")
    env = os.environ if env is None else env
    try:
        payload = json.load(sys.stdin if stdin is None else stdin,
                            parse_constant=_reject_constant)
    except ValueError as exc:
        raise SystemExit(f"refusing to write: {exc}") from None
    if args.patch or args.draft:
        verb = "draft" if args.draft else "patch"
        try:
            with open(config_path, encoding="utf-8") as f:
                current = json.load(f, parse_constant=_reject_constant)
        except FileNotFoundError:
            # Only a draft may start from nothing -- it is how the config comes
            # into existence, one answer at a time. For --patch a missing file
            # stays the refusal it has always been: that is how a mistyped path
            # or a lost config announces itself instead of quietly becoming a
            # new one.
            if not args.draft:
                raise SystemExit(
                    f"refusing to patch: could not read {config_path}: "
                    "no such file") from None
            current = None
        except (OSError, ValueError) as exc:
            raise SystemExit(
                f"refusing to {verb}: could not read {config_path}: {exc}") from None
        config = apply_patch(payload, current, env, gated=not args.draft)
    else:
        raise SystemExit(
            "refusing to write: pass --draft (onboarding, answer by answer) or "
            "--patch (a later change). There is no whole-config mode: it built "
            "the file from a full answer set, which is a form, and rebuilding "
            "from one silently dropped every preference nobody restated.")
    try:
        verdict = gate(config)
    except GateError as exc:
        verdict = f"not valid JSON ({exc})"
    if args.draft:
        # A draft is excused only for questions not yet asked. Gate the config
        # again with those stood in for: whatever still fails came from a value
        # the owner actually supplied, and that is refused exactly as --patch
        # would refuse it.
        try:
            supplied = gate(fill_unasked(config))
        except GateError as exc:
            supplied = f"not valid JSON ({exc})"
        if supplied:
            raise SystemExit(f"refusing to draft: the gate says: {supplied}")
    elif verdict:
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
    # A draft reports the gate instead of obeying it. The line is not decoration:
    # it is how a turn learns the config is still short of "installed", and the
    # names in it are exactly what the wall path will need later.
    print(f"wrote {config_path} (mode 600); gate: {verdict or 'PASS'}")
    return 0


if __name__ == "__main__":
    # sys.argv[1:] explicitly, never argparse's implicit fallback: main() is
    # also called as a library (the tests), where the implicit fallback would
    # pick up pytest's own argv. Passing [] here instead -- as this line did
    # before --patch existed -- silently drops the flag and sends a partial
    # config through the first-run path, which refuses it as missing answers.
    sys.exit(main(sys.argv[1:]))
