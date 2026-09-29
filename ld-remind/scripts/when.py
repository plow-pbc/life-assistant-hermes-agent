#!/usr/bin/env python3
"""Turn what the owner said about *when* into a one-shot `hermes cron` delay.

The reminder skill schedules with `hermes cron create '<N>m' "..." --repeat 1
--deliver origin`, which fires once N minutes from now. This resolver is the
one thing that decides N: it reads the household timezone (so "tomorrow at 9"
is 9 o'clock where they live, not on the server's clock), computes the target
instant, and prints the delay in whole minutes.

It never invents a deadline. A phrase with no time, a time it cannot parse, or
a moment already past all exit 2 with a question to ask -- a wrong time is
worse than asking. Standard library only: no network, no install.

Success (exit 0), one JSON object:
    {"schedule": "<N>m", "repeat": 1, "delay_minutes": N,
     "instant": "<ISO with offset>", "human": "<readable, household zone>"}
Ask (exit 2):
    {"ask": "<question to put to the owner, in your own words>"}

Usage:
    when.py "<what they said about when>" --tz America/Los_Angeles
    when.py "tomorrow at 9" --tz America/Los_Angeles --now 2026-09-29T19:00:00-07:00
The household zone is read from /var/lib/hermes/ld/config.json (family.timezone)
when --tz is omitted.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

CONFIG = "/var/lib/hermes/ld/config.json"
DAYPARTS = {"morning": 9, "afternoon": 14, "evening": 18, "night": 20,
            "tonight": 20, "noon": 12, "midday": 12, "midnight": 0}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday"]
UNITS = {"minute": 1, "min": 1, "m": 1, "hour": 60, "hr": 60, "h": 60,
         "day": 1440, "d": 1440, "week": 10080, "wk": 10080, "w": 10080}


def ask(question: str) -> None:
    print(json.dumps({"ask": question}))
    sys.exit(2)


def household_zone(explicit: str | None) -> ZoneInfo:
    if explicit:
        try:
            return ZoneInfo(explicit)
        except Exception:
            ask("I don't recognize that timezone. Where are you, and when should I remind you?")
    try:
        cfg = json.loads(Path(CONFIG).read_text())
        return ZoneInfo(str(cfg["family"]["timezone"]))
    except Exception:
        # No zone means a wall-clock time would land on the server's clock,
        # which is nobody's. Relative delays are still safe (handled first).
        return None  # type: ignore[return-value]


def parse_clock(text: str) -> tuple[int, int] | None:
    """First clock time in the text -> (hour, minute) 24h, or None."""
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
    if m:
        hour = int(m.group(1)) % 12
        if m.group(3) == "pm":
            hour += 12
        return hour, int(m.group(2) or 0)
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)  # 24h "17:30"
    if m:
        return int(m.group(1)), int(m.group(2))
    for word, hour in DAYPARTS.items():
        if word in text:
            return hour, 0
    m = re.search(r"\bat\s+(\d{1,2})\b", text)  # bare "at 9" -> assume am/pm by daylight? ask
    if m:
        hour = int(m.group(1))
        if hour <= 12:
            return None  # ambiguous am/pm without a daypart: caller asks
        return hour, 0
    return None


def resolve(text: str, now: dt.datetime, zone: ZoneInfo | None):
    t = text.strip().lower()
    if not t:
        ask("When should I remind you?")
    if re.search(r"\b(yesterday|ago|last (night|week|month|year))\b", t):
        ask("That sounds like a time in the past. When should I remind you?")

    # 1) Relative delay -- zone-independent, always safe.
    m = re.search(r"\bin\s+(a|an|\d+)\s*(minutes?|mins?|hours?|hrs?|days?|weeks?|m|h|d|w)\b", t) \
        or re.search(r"^(\d+)\s*(minutes?|mins?|hours?|hrs?|days?|weeks?|m|h|d|w)$", t)
    if m:
        n = 1 if m.group(1) in ("a", "an") else int(m.group(1))
        unit = m.group(2).rstrip("s")
        mins = n * UNITS.get(unit, UNITS.get(unit[0], 0))
        if mins <= 0:
            ask("How long from now should I remind you?")
        target = now + dt.timedelta(minutes=mins)
        return emit(target, now, zone, relative=True)

    if re.search(r"\bhalf an hour\b", t):
        return emit(now + dt.timedelta(minutes=30), now, zone, relative=True)
    if re.search(r"\ban hour\b", t) and "in" not in t:
        return emit(now + dt.timedelta(hours=1), now, zone, relative=True)

    # Everything below is a wall-clock time, so it needs the household zone.
    if zone is None:
        ask("What city are you in? I need it to get the time right.")
    local_now = now.astimezone(zone)

    clock = parse_clock(t)
    day = None  # a date (year, month, day) in the household zone

    # 2) tomorrow / today / tonight
    if "tomorrow" in t:
        day = (local_now + dt.timedelta(days=1)).date()
    elif "today" in t or "tonight" in t or "this " in t:
        day = local_now.date()

    # 3) weekday
    if day is None:
        for i, name in enumerate(WEEKDAYS):
            if name in t:
                ahead = (i - local_now.weekday()) % 7
                if ahead == 0 or "next" in t:
                    ahead = ahead or 7
                    if "next" in t and (i - local_now.weekday()) % 7 != 0:
                        ahead = (i - local_now.weekday()) % 7 + 7
                day = (local_now + dt.timedelta(days=ahead)).date()
                break

    # 4) explicit month + day, e.g. "oct 14", "october 14"
    if day is None:
        months = ["january", "february", "march", "april", "may", "june",
                  "july", "august", "september", "october", "november", "december"]
        m = re.search(r"\b(" + "|".join(mo[:3] for mo in months) + r")[a-z]*\.?\s+(\d{1,2})\b", t)
        if m:
            mon = [mo[:3] for mo in months].index(m.group(1)) + 1
            dom = int(m.group(2))
            year = local_now.year + (1 if mon < local_now.month else 0)
            try:
                day = dt.date(year, mon, dom)
            except ValueError:
                ask("That date doesn't look right. What day should I remind you?")

    if day is None and clock is None:
        ask("When should I remind you? A day and time, or something like 'in 2 hours', works.")
    if clock is None:
        ask("What time that day should I remind you?")

    hour, minute = clock
    if day is None:  # a time but no day: today if still ahead, else tomorrow
        day = local_now.date()
        cand = dt.datetime.combine(day, dt.time(hour, minute), tzinfo=zone)
        if cand <= local_now:
            day = (local_now + dt.timedelta(days=1)).date()

    target = dt.datetime.combine(day, dt.time(hour, minute), tzinfo=zone)
    return emit(target, now, zone, relative=False)


def emit(target: dt.datetime, now: dt.datetime, zone: ZoneInfo | None, relative: bool):
    delay = target - now
    minutes = int(delay.total_seconds() // 60)
    if minutes < 1:
        ask("That time has already passed. When should I remind you?")
    shown = target.astimezone(zone) if zone else target
    human = shown.strftime("%a %b %-d at %-I:%M %p") if not relative or zone else \
        shown.strftime("%Y-%m-%d %H:%M %Z")
    print(json.dumps({
        "schedule": f"{minutes}m",
        "repeat": 1,
        "delay_minutes": minutes,
        "instant": target.isoformat(),
        "human": human,
    }))
    sys.exit(0)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phrase")
    ap.add_argument("--tz")
    ap.add_argument("--now", help="aware ISO override, for tests")
    args = ap.parse_args(argv)
    zone = household_zone(args.tz)
    if args.now:
        now = dt.datetime.fromisoformat(args.now)
    else:
        now = dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    resolve(args.phrase, now, zone)


if __name__ == "__main__":
    main()
