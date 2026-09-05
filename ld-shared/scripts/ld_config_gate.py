#!/usr/bin/env python3
"""Shared minimal structural gate for the life-dashboard ld-config.

This is the SINGLE shared definition of "installed" for the ld-config. It lives
here, in life-dashboard-skills, alongside the contract it enforces
(references/config.example.json), and is materialized into each consuming seed's
ref/team-skills/ld-shared/scripts/ by that seed's sync-ld-shared.sh. From there
each seed invokes it — at install time (the install-time + pre-cron gate) and at
verify time (the v-ld-config assertion) — as:

    python3 .../ld-shared/scripts/ld_config_gate.py <config.json>

so the structural contract lives in ONE place, single-homed with the contract,
and the two seeds (seed-life-dashboard-agent and seed-life-dashboard-hermes-agent)
can never drift from each other or from install↔verify. It runs ON THE PI, where
jq is deliberately not provisioned but python3 is guaranteed present on Debian —
this replaces the jq filter the seeds used to carry verbatim.

Contract (the original jq gate PLUS the family.timezone invariant, added to align
the "installed" verdict with the shared runtime precondition loadLdConfig — the
jq reference in test_ld_config_gate.py is updated in lockstep so the equivalence
proof still holds):
  - Prints the failing invariant name(s) to stdout, joined by "; ".
  - Empty stdout == PASS. Never prints PII (the calendar ids).
  - Prints exactly "not valid JSON" (and nothing else) when the file does not
    parse as JSON OR when the structure would make the jq filter itself error
    (indexing a non-object, or testing a non-string field) — jq's gate ran with
    `2>/dev/null || echo "not valid JSON"`, collapsing both into that one line.

The owner's NAME is deliberately not among them: it lives on their Plow
account (users.display_name, read from the chat roster and written by the
plugin's plow_name_contact tool),
so a copy in this file would be a second answer to the same question. What
onboarding records here is family.owner.introduced, and the gate does not
require it either -- a household mid-interview is not a broken config.

The eight checks, matching the (updated) jq filter exactly:
  1. family.timezone must contain a non-whitespace char    (jq: (.family.timezone // "") | test("\\S"))
  2. calendar.sources must be a non-empty array            (jq: (type) == "array" and length >= 1)
  3. calendar.account must contain a non-whitespace char   (jq: (.calendar.account // "") | test("\\S"))
  4. no calendar.sources[].calendar_id may be blank        (jq: select(((.calendar_id // "") | test("\\S")) | not))
  5. calendar.sources[].calendar_id values must be unique  (jq: length == (unique | length))
  6. calendar_nudge.owner_identities must be a non-empty list of nonblank
     strings                                               (jq: (type) == "array", length >= 1, each (. // "") | test("\\S"))
  7. calendar_nudge.lookahead_virtual_minutes and
     lookahead_in_person_minutes must be positive numbers  (jq: (type) == "number" and . > 0)
  8. no string value anywhere may be a leftover placeholder (jq: .. | strings | test("^\\[[A-Z][A-Z0-9_]*\\]$"))

Checks 3-5 replace the old per-source account model with the one identity gog
actually needs: calendar.account selects the authenticated account once, while
each calendar_id names a calendar visible to it. Several sources saying
"primary" all resolve to that account's own calendar, silently omitting the
rest, so ids must also be present and unique. Owner participation is a separate
nudge concern: check 6 is its home -- calendar_nudge.owner_identities is the
owner's email identity set (one per connected calendar) that
nudge_candidates.py's owner-participation rule reads; an empty set would
fail that rule on every event, an eternally quiet nudge that looks installed.
"""
import json
import re
import sys

_PLACEHOLDER_RE = re.compile(r"^\[[A-Z][A-Z0-9_]*\]$")
# jq's test("\\S") is PCRE \S (any non-whitespace); Python's \S is the same
# class, and re.search finds it anywhere in the string, matching jq's test().
_NONBLANK_RE = re.compile(r"\S")


class GateError(Exception):
    """A structural shape that would make the jq filter itself error.

    jq ran the gate as `jq -r '...' file 2>/dev/null || echo "not valid JSON"`,
    so a filter-level error (indexing a non-object with `.foo`, or applying
    test() to a non-string) collapsed to the same "not valid JSON" line as a
    JSON parse failure. We raise this for those shapes and map it identically.
    """


def _index(value, key):
    """jq `.key` — null/missing → null; non-object → error (caught as 'not valid JSON')."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(key)
    # jq errors on `.key` applied to a string/number/array/bool.
    raise GateError("cannot index non-object")


def _coalesce(value):
    """jq `value // ""` — null/false become ""; everything else passes through."""
    if value is None or value is False:
        return ""
    return value


def _test_nonblank(value):
    """jq `(value // "") | test("\\S")` — errors (caught) when value is non-string after //."""
    coalesced = _coalesce(value)
    if not isinstance(coalesced, str):
        # jq's test() raises on a number/object/array — collapses to 'not valid JSON'.
        raise GateError("test() on non-string")
    return bool(_NONBLANK_RE.search(coalesced))


def _all_strings(node):
    """jq `.. | strings` — every string reachable by recursive descent."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _all_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _all_strings(v)


def gate(config):
    """Return the "; "-joined failures for a parsed config (empty == pass).

    Raises GateError for shapes the jq filter would have errored on; the caller
    maps that to "not valid JSON".
    """
    failures = []

    # 1. family.timezone non-blank — the shared runtime precondition. ld-runtime.js
    #    loadLdConfig() requires it, and a blank/whitespace tz also crashes
    #    minuteInTz (Intl rejects it), so the install gate must reject configs the
    #    runtime cannot execute.
    tz = _index(_index(config, "family"), "timezone")
    if not _test_nonblank(tz):
        failures.append("family.timezone is blank")

    # 2. calendar.sources is a non-empty array
    sources = _index(_index(config, "calendar"), "sources")
    if not (isinstance(sources, list) and len(sources) >= 1):
        failures.append("calendar.sources is not a non-empty array")

    # 3. gog requires the account that owns/can read --calendars. This is one
    #    identity for the whole merged call, not duplicated on every source.
    account = _index(_index(config, "calendar"), "account")
    if not _test_nonblank(account):
        failures.append("calendar.account is blank")

    # 4. no calendar.sources[].calendar_id is blank, and 5. the ids are unique.
    #    One gog identity reads every source, so the id is a calendar's entire
    #    address: a blank one reads nothing, and duplicates (several "primary")
    #    silently collapse onto the authenticated account's one calendar.
    #    jq's `.calendar.sources[]?` iterates only when sources is an array;
    #    each element's `.calendar_id` errors if the element is not an object
    #    (caught as 'not valid JSON'). jq's `?` suppresses only the `.[]`
    #    iteration error, NOT the downstream index — so we must visit EVERY
    #    element (no early break): a later non-object element still raises
    #    GateError and collapses to "not valid JSON", exactly as jq does. Each
    #    failure is recorded at most once, after the full sweep.
    if isinstance(sources, list):
        ids = []
        blank_id = False
        for src in sources:
            id_ = _coalesce(_index(src, "calendar_id"))
            if not isinstance(id_, str):
                # jq's test() raises on a number/object/array — 'not valid JSON'.
                raise GateError("test() on non-string")
            if not _NONBLANK_RE.search(id_):
                blank_id = True
            ids.append(id_)
        if blank_id:
            failures.append("a calendar.sources[].calendar_id is blank")
        if len(ids) != len(set(ids)):
            failures.append("calendar.sources[].calendar_id values are not unique")

    # 6. calendar_nudge.owner_identities is a non-empty list of nonblank
    #    strings. One gog identity fetches every calendar, so the nudge's
    #    owner-participation rule cannot be derived from the sources (the
    #    per-source account/self keys are gone; this key is identity's home).
    #    The full comprehension (no all()-short-circuit) matches jq, which
    #    evaluates test() on every element -- a blank element followed by a
    #    non-string one must still collapse to "not valid JSON".
    idents = _index(_index(config, "calendar_nudge"), "owner_identities")
    if not (isinstance(idents, list)
            and len(idents) >= 1
            and all([_test_nonblank(i) for i in idents])):
        failures.append(
            "calendar_nudge.owner_identities is not a non-empty list of "
            "nonblank strings")

    # 7. the two nudge lookaheads are positive numbers. nudge_candidates.py
    #    hard-requires both, so a gate-passing config missing either would
    #    fail every scheduled half-hourly run -- installed-looking, never
    #    nudging. bool is excluded: JSON true is jq type "boolean", but
    #    Python bool passes isinstance(int).
    for key in ("lookahead_virtual_minutes", "lookahead_in_person_minutes"):
        value = _index(_index(config, "calendar_nudge"), key)
        if not (isinstance(value, (int, float)) and not isinstance(value, bool)
                and value > 0):
            failures.append(f"calendar_nudge.{key} is not a positive number")

    # 8. no leftover [UPPER_SNAKE] placeholder anywhere
    if any(_PLACEHOLDER_RE.match(s) for s in _all_strings(config)):
        failures.append("an unfilled [UPPER_SNAKE] placeholder remains")

    return "; ".join(failures)


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: ld_config_gate.py <config.json>\n")
        return 2
    try:
        with open(argv[1], encoding="utf-8") as f:
            # parse_constant: BOTH parsers accept the non-standard NaN /
            # Infinity tokens (measured: jq 1.7.1 parses Infinity and calls
            # it > 0), so an Infinity lookahead would pass the gate. Raising
            # here fail-closes them as "not valid JSON" — a deliberate
            # divergence from jq, like the empty-file case.
            config = json.load(
                f, parse_constant=lambda token: (_ for _ in ()).throw(
                    ValueError(f"non-standard JSON constant {token}")))
        failures = gate(config)
    except (OSError, ValueError, GateError):
        # jq's gate emitted "not valid JSON" on any read/parse/filter failure.
        print("not valid JSON")
        return 0
    if failures:
        print(failures)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
