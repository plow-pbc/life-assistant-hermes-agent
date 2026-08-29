#!/usr/bin/env python3
"""Wrapper-contract test for the ld- producers this repo ships.

The shared POST helper (post_to_kiosk.py) and its own tests came from
plow-pbc/life-dashboard-skills and now live in-tree as ld-shared/ — there is no
sync step, so this runs against the checkout as-is. It verifies the part each
producer owns: its thin wrapper sets the right CARD / BODY_TYPE on the shared
module at import, per the pinned producer→card mapping the viewer renders
(1=alert, 2=affirmation, 3=weather, 4=digest, 5=sports). On Hermes every
producer — including weather and sports — runs as an LLM cron job that calls a
Python wrapper.

Six rows, one per producer wrapper: weather and sports (public feeds),
morning-triage (the Mac's iMessage DB through Latch), and the three calendar
producers (morning-updates, weekly-digest, calendar-nudge) on Latch's vendored
gog.

Each wrapper runs in a fresh interpreter: an in-process import would find
post_to_kiosk already in sys.modules and mask a broken relative sys.path in the
wrapper. A subprocess makes the wrapper's import path actually load-bearing.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
passed = failed = 0


def check(label, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"PASS - {label}")
    else:
        failed += 1
        print(f"FAIL - {label}")


SNIPPET = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('wrapper', sys.argv[1])\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    "import post_to_kiosk\n"
    "print(post_to_kiosk.CARD)\n"
    "print(post_to_kiosk.BODY_TYPE)\n"
)

WRAPPERS = (
    ("ld-morning-triage/scripts/post_alert.py", "1", "alert"),
    ("ld-calendar-nudge/scripts/post_nudge.py", "1", "alert"),
    ("ld-morning-updates/scripts/post_message.py", "2", "affirmation"),
    ("ld-weather/scripts/post_weather.py", "3", "weather"),
    ("ld-weekly-digest/scripts/post_digest.py", "4", "digest"),
    ("ld-sports/scripts/post_sports.py", "5", "sports"),
)


def main():
    shared = REPO_ROOT / "ld-shared" / "scripts" / "post_to_kiosk.py"
    check("ld-shared/scripts/post_to_kiosk.py is in the checkout", shared.exists())
    if not shared.exists():
        print(f"\n{passed} passed, {failed} failed")
        sys.exit(1)

    for rel_path, expected_card, expected_type in WRAPPERS:
        wrapper = REPO_ROOT / rel_path
        check(f"{rel_path} wrapper exists", wrapper.exists())
        if not wrapper.exists():
            continue
        proc = subprocess.run(
            [sys.executable, "-c", SNIPPET, str(wrapper)], capture_output=True, text=True
        )
        check(f"{rel_path} imports cleanly via its own sys.path", proc.returncode == 0)
        if proc.returncode != 0:
            print(f"  stderr: {proc.stderr.strip()}")
            continue
        card, body_type = proc.stdout.strip().split("\n")
        check(f"{rel_path} sets CARD={expected_card!r}", card == expected_card)
        check(f"{rel_path} sets BODY_TYPE={expected_type!r}", body_type == expected_type)

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
