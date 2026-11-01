---
name: ld-wall-setup
description: The optional Pi wall — mint its bearer token, bring the Pi up through latch on the owner's Mac (texting the owner the lines when there is no Mac), register the producer crons and prove a card. Use when the owner asks to set up or re-set-up their wall, when its config is missing or refused, or when the wall has never shown a card; never in a group or in a DM from anyone else. Onboarding is ld-setup's and finishes without a wall — an owner who never wants a screen in their kitchen never runs this. Do not use for unrelated calendar or life-assistant questions.
---

# The wall — the Pi dashboard, if the owner wants one

**Run this only in the owner's own one-to-one thread.** Every part of it reaches
that owner's Mac or — on the no-Mac path in Phase 3 — hands over the wall's
bearer token, and a group thread is read by people who are not the owner. Trust
does not lift this: a trusted group may ask for the assistant's normal tools,
and a raw token is excluded from a group either way. If any of it is asked for
anywhere but that thread, do not start; say the owner can start it privately,
and stop.

**This runs only if the owner asked for it.** `ld-setup` finishes onboarding
with an offer — a screen in the kitchen, the build at
`https://github.com/plow-pbc/life-dashboard` — and an owner who never takes it
is fully set up without any of this. Onboarding is finished when
`/var/lib/hermes/ld/config.json` holds their answers; the wall is finished at
`/var/lib/hermes/ld/setup-complete`, and neither implies the other.
`/var/lib/hermes/SOUL.md` checks each for its own half.

Everything under **How this reaches the Pi**, and the two rules after it, is
the wall's; onboarding needs none of it — read on when the owner takes the wall
offer.

**How this reaches the Pi.** The Pi keeps its own dashboard server —
`/api/message` behind a bearer, port 5174 — on the owner's home network,
and you cannot reach that network. The owner's Mac can, and latch reaches the
Mac, so every step that touches the Pi runs *from the Mac*: `latch file put`
and `latch exec`, both with `--host <mac>` — in `latch status`, the online
host with `messages` enabled — and `--plow-chat <chat>`, this chat's uid as
the latch section gives it. A secret never rides argv: the wall's token
travels only inside files `latch file put` ships whole from their path, and
every `curl` reads it from `~/Plow/ld/dashboard.hdr` with `-H @…`.
`latch exec` runs its argv directly rather than through a shell, so anything
needing a redirect, a `&&` or a `~` is written as `sh -c '…'`, single-quoted
so your own shell passes it through untouched. Every call below that leaves
the Mac passes `--network`. If latch refuses a call (exit 4), ask the owner
here, naming exactly what you will run, and retry once they agree.

Cards refresh only while that Mac is awake and reachable — an accepted
cost. If a latch call here answers `host_offline`, or no online host has
`messages` enabled, say "Mac unreachable" in chat, stop, and resume when the
owner says the Mac is back.
For the *scheduled* card runs after setup, the rule is the producers' own:
`/var/lib/hermes/skills/ld-shared/references/latch-delivery.md` § When the Mac is
asleep or latch cannot reach it.

**Every script's and every latch call's output is pasted verbatim with its
exit status, and a phase is not done until you have.** The scripts signal
every refusal through their output and a non-zero exit, and a chat turn does
not propagate an exit code. Do not paraphrase, and do not call a phase done
on a non-zero exit.

**Never `cat`, `echo`, or otherwise paste `/var/lib/hermes/ld/.env`,
`/var/lib/hermes/ld/pi.env`, `/var/lib/hermes/ld/dashboard.hdr`, or any line
containing `TOKEN` into chat.** `ld/.env` is the file you write during setup and
it carries the wall's token; the scripts read it for you. Neither the agent's
own Plow bearer nor its latch credential is in any of these — both are this
process's own, put there by first boot — so do not echo those either. You
never read the two `ld/` files at all: `latch file put` ships each from its
path. The single exception is the no-Mac fallback in Phase 3, which says
exactly what may cross chat and once.

## The wall (optional) — Phases 2 to 4

Everything from here down runs **only** if the owner asked for the wall. Each
phase is gated on the durable artifact it produces, so a phase that has already
landed is skipped and the run resumes where it stopped.

| phase | what it produces | the artifact that skips it |
|---|---|---|
| 2 · wall token | the dotenv's `DASHBOARD_*` lines, `/var/lib/hermes/ld/pi.env`, `/var/lib/hermes/ld/dashboard.hdr` | `mint_wall_token.py` prints `already minted: DASHBOARD_ENDPOINT_URL=…` |
| 3 · Pi bring-up | a running viewer holding this token | with a Mac, `/api/version` through latch answers with JSON carrying `sha`; without one, `/var/lib/hermes/ld/pi-brought-up` exists |
| 4 · crons + proof | the six schedules and one real card | `/var/lib/hermes/ld/setup-complete` exists |

The wall needs a config the shared gate accepts, and onboarding alone cannot
produce one — `calendar.account`, its sources and
`calendar_nudge.owner_identities` come from the owner's calendar, which arrives
through Latch. Check before starting:

    python3 /var/lib/hermes/skills/ld-shared/scripts/ld_config_gate.py /var/lib/hermes/ld/config.json

No output is a pass; any text is the list of what is still missing (its exit
code is always 0 and means nothing — read the output). If it names calendar
keys, run `ld-setup` §5 — the calendars are discovered from the Mac, not
typed. §5 reads a local snapshot a background service refreshes; it does not
call Latch. If that snapshot has no usable choices yet, say the calendars are
not ready and come back after the next background tick — do not report the Mac
as unreachable, which is a different fault and not one this read can observe.
Either way, never ask the owner for an address; the wall needs the Mac anyway,
so there is nothing to gain by guessing one.

You also need `has_mac` for Phases 2 and 3 — ask for that alone. Do NOT ask
for `pi_address` or `pi_user` here: Phase 2's script recovers both from the dotenv and refuses by name for whichever it
cannot, and that refusal, not this note, is what decides when the owner gets
asked.


## Phase 2 — The wall's token

Idempotent, so there is no dotenv to inspect by hand — always run it. Its
answers are staged as ONE JSON object with your file tool and passed by path,
never argv and never composed into shell text — the same rule onboarding's own
drafts follow, because an embedded quote in an owner's answer would otherwise
execute before the script's validation (which holds `pi_address` and `pi_user`
to the safe charset Phase 3's `ssh` argv depends on) could see it.

**`<turn>` is eight random hex characters you GENERATE, fresh each run.** Run:

    openssl rand -hex 4

and use what it printed. Do not invent one by hand, do not reuse the last
run's, and do not copy a hex-looking string out of this sheet or any example —
a copied id is a fixed staging name wearing a random one's clothes, and two
runs that overlap have the second overwrite the first before the first is read.
Nor is it the inbound message's id or a session id: those are text from
outside, and this name ends up on a command line. Written out in full in
`ld-setup`'s `<turn>` note, which governs the drafts this file's staging
follows.

Stage this at `/var/lib/hermes/ld/.wall-<turn>.json`:

    {"pi_address": "...", "pi_user": "..."}

then:

    /var/lib/hermes/skills/ld-wall-setup/scripts/mint_wall_token.py --input /var/lib/hermes/ld/.wall-<turn>.json

Every key may be left out on a resume — `{}` is a valid staged object: the
script recovers `pi_address` from the dotenv's `DASHBOARD_ENDPOINT_URL` and
`pi_user` from `DASHBOARD_PI_USER` (which it persists there). When it
refuses for a missing answer, that answer is one only the owner can supply —
ask them for exactly that and nothing else. **Never invent an ssh login or
address**: on an unattended (cron) turn with no owner reply to draw on,
message the owner the question and stop; a guessed `pi@…` lands the wrong
`ssh-copy-id` instruction on their Mac.

The first run mints the token and appends `DASHBOARD_ENDPOINT_URL`
(`http://<pi_address>:5174/api/message`), `DASHBOARD_TOKEN` and
`DASHBOARD_DELIVERY=latch` to the dotenv (no restart needed —
`post_to_kiosk.py` reads that dotenv itself, because the container env is
the gateway's start-time load and never sees an appended line). A later run
prints `already minted: DASHBOARD_ENDPOINT_URL=…` and appends nothing — it
never re-mints, because the Pi holds the first token; that line is Phase 2's
skip signal. A later run with a *different* address prints `re-pointed:`
instead — the endpoint line converges to the new Pi, the token stays, and
Phase 3 ships `pi.env` to that Pi. Either way it (re)writes two files, mode 600, and prints two
bare lines, `pi_line_1=…` and `pi_line_2=…`, for Phase 3:

- `/var/lib/hermes/ld/pi.env` — the Pi's `~/ld-data/.env` (`DASHBOARD_TOKEN=`).
  Shipped in Phase 3.
- `/var/lib/hermes/ld/dashboard.hdr` — `Authorization: Bearer …`, the header every
  `curl` from the Mac reads. Ship it now, when the owner has a Mac:

      latch file put --host <mac> --plow-chat <chat> /var/lib/hermes/ld/dashboard.hdr '~/Plow/ld/dashboard.hdr'

  latch reads the file itself, so the token never passes through you. Paste
  the call's result verbatim. Phase 2 is done when the script exited 0 and,
  with a Mac, that put succeeded.

Everything after this line that is written `<pi_address>` or `<pi_user>` is a
placeholder bound from the `pi_target=<pi_user>@<pi_address>` line Phase 2's
script printed — the one authoritative, validated ssh target; never bind
either half from memory or a guess. Substitute both before you make the
call, and never send the literal angle brackets to the Mac.

## Phase 3 — Bring the Pi up through latch

**With a Mac:** when this returns JSON carrying a `sha`, the viewer is up
and the token is the one it checks (a latch call, so it belongs to this path
only; the no-Mac path has its own gate below) — skip the bring-up, EXCEPT
the ship-and-restart step at the end of this path, which still runs whenever
Phase 2 printed `re-pointed:`: the freshly rewritten `pi.env` has to reach
the Pi or the new address never takes effect.

    latch exec --host <mac> --plow-chat <chat> --network -- sh -c 'curl -fsS -H @$HOME/Plow/ld/dashboard.hdr http://<pi_address>:5174/api/version'

**Read the failure before treating it as "viewer not up."** A curl `exit_code` 22
(an HTTP 4xx) means something *answered* on 5174 — probe the always-open
liveness path to tell the two states apart:

    latch exec --host <mac> --plow-chat <chat> --network -- curl -fsS http://<pi_address>:5174/healthz

`ok` here plus a 403/404 above = the viewer is alive and serving the wall
but runs a build that predates the updater contract (no `/api/version`, or no
remote reads). The remedy is re-running `pi_line_2` (the updater bootstrap)
over ssh in the bring-up below — not `ssh-copy-id`, not a re-mint, and not a
report that the wall is down. `ok` plus a 401 is a *current* viewer whose
token is not the one in the header — that is the 401 recovery at the end of
this phase (re-ship `pi.env` and restart the viewer), not a bootstrap. Only
a connection failure on both (exit 7 / timeout) means the viewer is actually
not up.

Otherwise — still on the with-a-Mac path — probe key auth first — no password should ever need to
cross chat. `BatchMode=yes` on every `ssh`/`scp` so a key-auth regression
fails fast instead of hanging on a password prompt with no tty;
`StrictHostKeyChecking=accept-new` because the first connection has no host
key yet and there is no tty to answer the prompt on.

    latch exec --host <mac> --plow-chat <chat> --network -- ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new <pi_user>@<pi_address> true

If that fails, the **one hands-on moment**: tell the owner to run
`ssh-copy-id <pi_user>@<pi_address>` in their own Mac terminal. That is not
something you can do for them — the Pi's password would have to cross chat to
reach you, and nothing in this sheet lets one. Once they confirm it ran,
re-probe; do not proceed on a refused probe.

Once the probe succeeds, run each Pi line the same way, with `--timeout 600`
(ten minutes, latch's ceiling — `apt-get` is slow on a Pi; the call just
waits). `<pi_line_1>` and `<pi_line_2>` are the two values Phase 2 printed,
each dropped whole between the single quotes:

    latch exec --host <mac> --plow-chat <chat> --network --timeout 600 -- ssh -o BatchMode=yes <pi_user>@<pi_address> '<pi_line_1>'
    latch exec --host <mac> --plow-chat <chat> --network --timeout 600 -- ssh -o BatchMode=yes <pi_user>@<pi_address> '<pi_line_2>'

Raspberry Pi OS grants the Imager's primary user passwordless sudo by
default; if `pi_line_1` instead comes back asking for a sudo password, that
assumption was wrong for this Pi — do not try to smuggle one through. Text
`pi_line_1` to the owner and ask them to run it on the Pi themselves, then
continue with `pi_line_2` through latch as above.

Then ship the Pi's env file to the Mac, copy it across and restart the
viewer. The shell text here is static: the owner's two values ride the
trailing argv positions (`$1`/`$2`), so nothing they answered is ever parsed
as shell:

    latch file put --host <mac> --plow-chat <chat> /var/lib/hermes/ld/pi.env '~/Plow/ld/pi.env'
    latch exec --host <mac> --plow-chat <chat> --network --timeout 30 -- sh -c 'scp -o BatchMode=yes ~/Plow/ld/pi.env "$1@$2:ld-data/.env" && ssh -o BatchMode=yes "$1@$2" "chmod 600 ld-data/.env && systemctl --user restart life-dashboard-viewer"' sh <pi_user> <pi_address>

Finish with the skip check at the top of this phase: JSON with a `sha` means
the viewer is up and the token is live. Paste it. Any other result — 401
(the token on the Pi is not the one in the header: re-ship `pi.env` and
restart the viewer), connection refused (the viewer is not up yet; wait a
minute and retry, at most a few times), `host_offline` (the Mac) — is
reported verbatim, and this phase is not done.

**No Mac (or no Latch):** skip this path when `/var/lib/hermes/ld/pi-brought-up`
exists — the file written below on the owner's confirmation. One exception:
if Phase 2 printed `re-pointed:`, this is a *different Pi* — ignore the
marker and run the full fallback below, because the new device has neither
the packages nor its env. Otherwise the
fallback is the direct one, and the token crosses chat once — acknowledged,
not ideal, and the only place in this sheet where that is allowed. It is
allowed *in the owner's own one-to-one thread and nowhere else*, and never in
a group. This is
the wall's bearer token: whoever holds it can write to the screen in that
household's kitchen. Every participant in a group can read it, forever, out of
their own message history — and unlike a leaked password there is nothing to
rotate without re-minting and re-shipping the Pi. If this phase is reached
anywhere but that thread, say the lines have to go to the owner directly, stop,
and continue when they message you alone.

There, text the owner, verbatim: (1) `pi_line_1`, (2) `pi_line_2`, and (3) the line in
`/var/lib/hermes/ld/pi.env` (read with your file tool; this is the one place its
content may be pasted), and say: run the first two on the Pi over ssh or at
its keyboard, then put that line in `~/ld-data/.env` on the Pi,
`chmod 600 ~/ld-data/.env`, and run
`systemctl --user restart life-dashboard-viewer`; the screen comes up on its
own within a few minutes. You cannot reach the Pi yourself without a Mac, so
the owner's word that the screen is up is this phase's proof — when they say
it is, write the artifact that keeps a resumed run (or a deleted
setup-complete marker) from texting the token across chat a second time:

    date -u +%FT%TZ > /var/lib/hermes/ld/pi-brought-up

## Phase 4 — Crons, then every card

Create-if-missing and safe to re-run, so there is no skip condition of its own
— always run it (Phase 4's artifact is the marker at the end):

    /var/lib/hermes/skills/ld-dashboard/scripts/register_crons.py

Paste its output and its exit status; `refusing to register`, `WARNING` or
`PAUSED` is a failed registration to report and fix (see
`/var/lib/hermes/skills/ld-dashboard/SKILL.md`) — but never a reason to leave
the wall empty. The fill below does not depend on a single schedule existing,
so run it either way, and tell the owner plainly if their scheduled refreshes
are not yet registered.

**Without a Mac, registration is the whole phase** — everything from here to
the owner question is latch work a no-Mac install cannot do: every pushed
card — weather, sports, messages — stays empty until a Mac latch reaches
exists, because delivery always routes through the outbox and nothing ships
it. Do not run a producer, do not try to read a card back, and do not ask the
owner to look for one — the screen being up was already confirmed when
Phase 3 wrote `pi-brought-up`, so go straight to the marker at the end.

**With a Mac, fill every card now.** A wall that says "the cards arrive at
6 a.m." is a wall the owner reads as broken, and it hides a producer that
cannot post at all until the next morning. So setup does not wait for a
schedule: it runs each card producer once, right now, whatever the hour, and
does not finish until all four cards are on the Pi. The schedules registered
above are for refreshing them.

Run these four, one at a time, each by its own sheet — that sheet is the
whole procedure, this phase only orders them:

| card | producer | sheet |
|---|---|---|
| 1 · alert | `ld-morning-triage` | `/var/lib/hermes/skills/ld-morning-triage/SKILL.md` |
| 2 · affirmation | `ld-morning-updates` | `/var/lib/hermes/skills/ld-morning-updates/SKILL.md` |
| 3 · weather | `ld-weather` | `/var/lib/hermes/skills/ld-weather/SKILL.md` |
| 5 · sports | `ld-sports` | `/var/lib/hermes/skills/ld-sports/SKILL.md` |

The hour is not a reason to skip one. A triage run at 3 p.m. reads the same
inbox a 7 a.m. run would; a sports tile with no game today says so. One sheet
has a branch that posts no card, and setup overrides it, because a fresh wall
has no earlier card to leave up: a quiet triage (both gathers empty) writes
exactly `No alert today.` to its handoff file and posts it, so card 1 carries
the quiet-day state. Each producer's helper ends
with `NOT DELIVERED — ship it through latch, then paste both outputs:` because
the dotenv says `DASHBOARD_DELIVERY=latch`; run those two commands, in that
order, per `/var/lib/hermes/skills/ld-shared/references/latch-delivery.md`.
The triage rows also text the owner (their `--deliver` leg in
`register_crons.py`); that is the chat leg working, not a mistake.

Then read every card back in one call, the same way the producers write them:

    latch exec --host <mac> --plow-chat <chat> --network -- sh -c 'for c in 1 2 3 5; do printf "card %s: " $c; curl -fsS -H @$HOME/Plow/ld/dashboard.hdr "http://<pi_address>:5174/api/message?card=$c" | python3 -c "import json,sys; d=sys.stdin.read(); m=json.loads(d)[\"message\"] if d else None; print(m[\"type\"] if m else \"EMPTY\")"; done'

Four lines reading `alert`, `affirmation`, `weather`, `sports`, in
that order, is the gate. A failed fetch prints `EMPTY` too (curl's own error
line says why). Any `EMPTY` names the producer that did not land:
read what its run printed, fix it, run that one again, and read back again.
Do not go on to the marker with an empty card, and do not describe a card as
"coming later" — every card the wall has is on it before this phase ends.

**Then run the calendar strip once.** The strip
(`ld-shared/scripts/calendar_feed.py`) runs unattended with no model in it and
ships through latch like every card. Running it here puts real events on the
wall now rather than at the next tick, and a refusal surfaces while the owner
is here to hear it.

Its schedule is the `life-calendar-feed` service, supervised beside the gateway
and ticking every five minutes from boot. Confirm it is there, then run the
producer once as the gateway user:

    s6-svstat /run/service/life-calendar-feed

    /opt/hermes/.venv/bin/python3 /var/lib/hermes/skills/ld-shared/scripts/calendar_feed.py

Paste its output. A line naming a count and `shipped through the Mac` is the
pass. `calendar feed not configured: …` means an earlier phase did not finish
— fix that first. `calendar feed failed: …` names latch's error code; a
`gatekeeper_denied` is a refusal to take up with the owner, as above. The strip on the wall is real events from this household's
own calendars, so it is also the proof, exactly as the weather card is.

Then tell the owner: "your wall is live — all four cards should be showing;
are they?" — their answer confirms the screen itself, which is the
one thing the API cannot show you.

Only now — after the owner's confirmation, this phase's for a Mac install,
Phase 3's `pi-brought-up` for a no-Mac one — mark the wall done; this is the
only thing that writes this file, and nothing before this line should. It says
nothing about onboarding, which may have finished long before the owner ever
wanted a screen:

    date -u +%FT%TZ > /var/lib/hermes/ld/setup-complete

