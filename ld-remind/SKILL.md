---
name: ld-remind
description: A one-shot reminder that comes back to the owner as a text at the time they set. Use when the owner asks to be reminded of something at, or by, a time -- "remind me to move the car at 6", "in 20 minutes tell me to take the cake out", "nudge me about the passports Friday morning", a thing plus any expression of time. Also when they move, cancel, or ask about one already set -- "push the dentist one to tomorrow", "never mind the car", "what have I got set?". NOT the household to-do list: an untimed "add this to the list" or "remind me to X" with no time is ld-priorities. NOT recurring routines ("every morning") and NOT calendar events (the nudge handles those). One thing, one moment, delivered once.
---

# Remind me — a single timed nudge, delivered back to the owner

You turn "remind me to X at T" into one message that arrives at T. It is a
**one-shot** job that texts the owner and then it is done: no list, no repeat,
no chasing. A commitment with no time is still yours — **you never invent the
time, you ask for it.** A wrong time is worse than no time.

This is not `ld-priorities`. An untimed task ("add renew the passports to the
list") is the to-do list's. A thing tied to a moment ("remind me Friday at 9")
is yours. When both are true, set the reminder and, if they asked for the
list too, add it there as well.

## The four steps. Steps 1–3 are silent; the owner hears only step 4.

**1 · Take the thing, in their own words.** What must not be forgotten, exactly
as they said it. If the thing itself is unclear, ask about the thing, not the
time.

**2 · Work out the time.** Run the resolver, which reads the household timezone
from the config so a wall-clock time lands where they live, not on the server:

    python3 /var/lib/hermes/skills/ld-remind/scripts/when.py "<what they said about when>"

Add `--tz <zone>` only if they name a different one. It prints one JSON object.

- **exit 0** — resolved. Use its `schedule` and `repeat` verbatim in step 3,
  and its `human` in the confirmation.
- **exit 2** — no time given, unparseable, or already past. Ask the question in
  its `ask` field, in your own voice, and STOP. Do not guess a time.

**3 · Stage the reminder text, then schedule it.** The reminder is the owner's
words coming back, so it is delivered **verbatim** from a file — never composed
into the command, never re-written by a later turn. Write the file with your
file tool at `~/.hermes/scripts/remind-<turn>.sh` (`<turn>` = fresh
`openssl rand -hex 4`, never a copied literal), exactly this shape:

    #!/bin/sh
    cat <<'LD_REMIND_BODY'
    <the reminder, in the owner's words, opening with the thing>
    LD_REMIND_BODY

The quoted `LD_REMIND_BODY` marker means the body is printed as-is: quotes,
punctuation and their spelling all survive untouched. Open with the thing, no
preamble ("Move the car" — never "Here's the reminder you asked for"). Write it
in the owner's language.

Then create the one-shot job (a real terminal command, not chat):

    /opt/hermes/bin/hermes cron create '<schedule>' --script remind-<turn>.sh --no-agent --deliver origin --repeat 1 --name "<a short handle>"

- `--repeat 1` fires it once; `--no-agent` delivers the file's text verbatim
  with no model in the loop; `--deliver origin` sends it back to this
  conversation. All three are required — drop `--deliver origin` and it reaches
  nobody; drop `--repeat 1` and it repeats forever.
- The `<schedule>` is the resolver's, unchanged.

**4 · Confirm — one message, and only one.** The thing and the time from
`human`, in your voice. Nothing about the resolver, the script, the job, or the
timezone: naming the machinery is you describing yourself instead of answering.

    "Got it. I'll remind you to move the car Wed at 6:00 PM."

## Changing or cancelling one already set

Same tool, different action. **Always `list` first, and never guess a job's
name or id.**

    /opt/hermes/bin/hermes cron list

- **Move it** ("push it to Friday", "make it 9") — resolve the new time (step 2)
  and `hermes cron edit <name-or-id> --schedule '<new schedule>'`. The text is
  kept, so the reminder still says what they meant. `edit` only touches what you
  pass.
- **Cancel it** ("never mind", "forget the car one") — `hermes cron remove <name-or-id>`.
- **Ask what's set** ("what have I got?") — `list`, and read the reminders back
  in their words, not the raw rows.

If a name matches more than one job, ask which they mean in their own terms
("the dentist one Tuesday or Thursday?") — never pick one. If it matches none,
say so plainly rather than inventing.

Confirm the same way step 4 does: one message, in their words, no job id, no
action name.

## Bounds

- **One moment per reminder.** No repeats ("every morning") and no lists — a
  routine is a different shape, a list is `ld-priorities`.
- **Never a calendar event.** Meetings with other people are the calendar
  nudge's; this is for things the owner asks you to hold for them.
- **Never invent a time.** Step 2's exit 2 is asked, never filled in.
- **The reminder is theirs.** Deliver their words; do not tidy their spelling,
  translate them, or add a preamble.
