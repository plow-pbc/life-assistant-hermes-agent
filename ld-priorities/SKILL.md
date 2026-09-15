---
name: ld-priorities
description: The household to-do list and its wall card — add, finish, rename, and re-rank items from chat ("add X to our to-do list", "X is done", "call the list Weekend jobs", "no, the passport is more urgent"). The assistant owns the order; the list's manifest keeps the rules it has learned. Use whenever the owner or a trusted household member talks about the to-do list, what's next, or what matters most; never on a schedule.
---

# Life Dashboard — Priorities (the to-do list)

One list, one file: `/var/lib/hermes/ld/priorities.json` — its name, its open
items **in rank order**, the ranking rules you have learned, and what got
done. You never edit that file by hand. Every change goes through one CLI, by
absolute path (a chat turn's working directory is not the skill directory):

    /var/lib/hermes/skills/ld-priorities/scripts/priorities.py <command>

Every mutating command prints nothing on success except `add` (which prints
the new item's id) — silence is success, an error is loud (`refusing: ...`,
non-zero exit).

| command | what it does |
|---|---|
| `show` | the manifest as JSON (only the last 5 `done`) — read it FIRST, every turn. Every completed item is kept in `/var/lib/hermes/ld/priorities.json`; read that file when the owner asks about something finished longer ago |
| `add "<text>" [--why "<chip>"]` | append an item (prints its id) |
| `done <id>` / `remove <id>` | finish (kept under `done`) / forget |
| `rename "<name>"` | rename the list — the card's title follows |
| `rule add "<sentence>"` / `rule remove <n>` | the ranking rubric |
| `rank <id> <id> …` | the new order — every open id exactly once, or it refuses |
| `why <id> "<chip>"` | a short reason under an item (`""` clears) |
| `post [--dry-run]` | compose the tile and ship it to the wall as card 6 |

## Every turn that touches the list

1. `show`.
2. Apply what the owner asked: `add`, `done`, `remove`, `rename`, `why`.
3. **Learn.** When the owner corrects an order or states a preference — "the
   passport is more urgent than the gutters", "groceries always go last",
   "anything for the kids' school first" — `rule add` a short sentence in the
   owner's terms BEFORE you re-rank. The rules are the manifest of how to
   rank; a correction that does not become a rule is one you will make again.
   `rule remove` one the owner has since reversed.
4. **Rank.** You own the order. Read `rules` first — they are what the owner
   has told you about how they want things ranked — then use judgment: a date
   you can see coming, something blocking something else, what the owner
   sounded worried about. Emit `rank` with every open id in the order you
   chose. Put a one-line reason under an item with `why` when the reason is
   not obvious ("before Oct 3 trip", "landlord asked twice").
5. `post`.
6. Reply with the top of the list (name, then the first few items, numbered)
   and — on a re-rank — one sentence on what moved and why. Nothing else.

## Post

`post` composes the tile HTML, writes it to the fixed handoff file —
`/var/lib/hermes/ld/priorities-text` — and posts it in one step. The wrapper
below is that same `post` (a retry after a failed send recomposes from the
manifest as it is now); you never need it yourself:

    /var/lib/hermes/skills/ld-priorities/scripts/post_priorities.py

It posts as card 6 with `type: "priorities"`, `title` set to the list's name,
http(s)-allowed, no redirects, and fails loudly on any non-200 response.

If it prints `NO WALL`, the wall is not set up yet: the list still works, say
nothing about the wall. If it prints `NOT DELIVERED`, this wall is reached
through Latch: follow
`/var/lib/hermes/skills/ld-shared/references/latch-delivery.md` — the run is
not done until the Latch `curl` returned 2xx.

Preview without sending: `… priorities.py post --dry-run` — this also prints
`NO WALL` before the wall is set up, since dry-run previews the *send*, not
the compose.

The card shows up to six items; the manifest may hold more. Item text on the
wall is one line: keep items short, put detail in `why`.

**Escaping:** `post` HTML-escapes every item and `why` string itself — you
never need to escape anything yourself when calling `add`/`why`/`rename`.

## What this is not

No schedule: the list changes only when you edit it — `ld-dashboard`'s cron
spec does not register this skill. Not a place for private inbound (an
unanswered email or iMessage is `ld-morning-triage`'s alert, not a to-do). Not
per-person — it is the household's list, and a trusted group member's "add X"
is as good as the owner's.
