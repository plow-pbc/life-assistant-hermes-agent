# Parked: the executable state seam

`onboarding_state.py` and its tests, built for round 6's P4 and parked here
rather than merged. The decision seam works — its live runs make the right
call on every state, including the resumes an enumerated table kept missing —
but it increased the failure that matters most to an owner.

**Why it is parked.** Three narration leaks across its verify runs, one of them
prefixed to the very first message a new owner sees:

    This is opener territory — ask name, no intro yet.
    Hey — good to meet you!

    Latch is configured. Making the calendar listing call now.

    write_now needs name + weather.location drafted now.

The pre-seam heads leaked none. The likeliest cause is that naming the decision
gave the model a machinery vocabulary to think aloud in (`ask`, `write_now`,
`intro_due`, `latch: configured`), and a prompt-level ban on saying it holds no
better than the prompt-level ban on `clarify` did — which is the argument for
the seam, turned around. A structural suppressor would settle it;
`display.interim_assistant_messages` is not one (tried in an earlier round, it
deleted the real message).

**What is good here and should survive.** The rules are executable and tested:
key order, one deferral and its lapse, the introduction due only on the turn
the name is learned, calendars askable only where a relay is configured. The
tests are a table of STATES rather than turn shapes — every resume that used to
fall between rows is a row — plus two properties: nothing is both written and
deferred, and every answer in hand is written this turn or deferred onto a turn
that is certain to come.

**Transcripts** (in the kitchen's notes/runs/):

- `seam-fresh-7e91865` — fresh, four turns, clean
- `seam-resume-7e91865` — {name, city} resume, clean
- `seam-stub-final` — stub relay; correct decisions, leaked narration
- `stub-calendars-latchkey` — after the mcp__latch__ fix; two leaks, correct
  message content, calendar pick not reached

**What shipped instead** is on `onboarding-v2`: everything from round 6 that is
not the seam — no owner text in any shell, the identities union, the tool named
as the image registers it, `clarify` disabled by config, the verifier footer
off, and the one-deferral rule in prose.
