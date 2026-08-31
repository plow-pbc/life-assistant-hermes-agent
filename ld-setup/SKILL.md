---
name: ld-setup
description: First-run setup for the life dashboard — interview the owner over chat, write /opt/data/ld/config.json, mint the wall's token, bring the Pi up through Plow Latch on the owner's Mac (texting the owner the lines when there is no Mac), register the producer crons and prove a card. Use when the requested work involves the life dashboard and /opt/data/ld/setup-complete is missing, when its config is missing or refused, when the owner asks to set up or re-set-up their dashboard, or when the wall has never shown a card. Do not use for unrelated calendar or life-assistant questions.
---

# Life Dashboard — Setup

Four phases. Each is gated on the durable artifact it produces, so a phase
that has already landed is skipped and the run resumes where it stopped — a
reset, a rebuilt home or an interrupted chat all pick up from here.

| phase | what it produces | the artifact that skips it |
|---|---|---|
| 1 · interview | `/opt/data/ld/config.json` | the file exists **and** the gate prints nothing |
| 2 · wall token | the dotenv's `DASHBOARD_*` lines, `/opt/data/ld/pi.env`, `/opt/data/ld/dashboard.hdr` | `mint_wall_token.py` prints `already minted: DASHBOARD_ENDPOINT_URL=…`, confirming it already ran (the script is idempotent; run it every time) |
| 3 · Pi bring-up | a running viewer holding this token | with a Mac, `/api/version` through Latch answers with JSON carrying `sha`; without one, `/opt/data/ld/pi-brought-up` exists |
| 4 · crons + proof | the six schedules and one real card | `/opt/data/ld/setup-complete` exists |

`/opt/data/ld/setup-complete` is the one thing that marks the whole run done
— nothing else writes it, and it lands only at the end of Phase 4, after the
card-3 proof. `/opt/data/SOUL.md` checks it (alongside the config gate) before
skipping this skill, so an interruption after Phase 1 still resumes here
rather than stopping at a blank wall. Deleting the marker re-runs setup from
whichever phase's own artifact is missing.

**How this reaches the Pi.** The Pi keeps its own dashboard server —
`/api/message` behind a bearer, port 5174 — on the owner's home network,
and you cannot reach that network. The owner's Mac can, and it runs Plow
Latch, so every step that touches the Pi runs *from the Mac* through the
Latch tools (`plow_run_command`, `plow_write_file`). Argv is shown to the
owner on an approval card and kept in an audit record; there is no stdin and
no env. So a secret never rides argv: the wall's token travels only inside
files you ship whole with `plow_write_file`, and every `curl` reads it from
`~/Plow/ld/dashboard.hdr` with `-H @…`. `plow_run_command` execs its argv
directly rather than through a shell, so anything needing a redirect, a `&&`
or a `~` is written as `["sh","-c","…"]`. Its `network` defaults to false
and every call below that leaves the Mac passes `network=true`; its `timeout`
is in milliseconds and defaults to 10000, and a call that exceeds it comes
back as a job handle to poll with `plow_get_output`. Paths under `~/Plow`
auto-approve.

Cards refresh only while that Mac is awake with Latch running — an accepted
cost. If a Latch call here fails because the Mac is unreachable, say "Mac
unreachable" in chat, stop, and resume when the owner says the Mac is back.
For the *scheduled* card runs after setup, the rule is the producers' own:
`/opt/data/skills/ld-shared/references/latch-delivery.md` § When the Mac is
asleep or Latch is not running.

**Every script's and every Latch call's output is pasted verbatim with its
exit status, and a phase is not done until you have.** The scripts signal
every refusal through their output and a non-zero exit, and a chat turn does
not propagate an exit code. Do not paraphrase, and do not call a phase done
on a non-zero exit.

**Never `cat`, `echo`, or otherwise paste `/opt/data/.env`, `/opt/data/ld/pi.env`,
`/opt/data/ld/dashboard.hdr`, or any line containing `TOKEN` into chat.** The
dotenv carries this agent's own Plow bearer and the wall's token; the scripts
read it for you. The two `ld/` files are read with your file tool for one
purpose only — to become the `content` of a `plow_write_file` call — and
their content goes nowhere else: not into a chat message, not into argv. The
single exception is the no-Mac fallback in Phase 3, which says exactly what
may cross chat and once.

## Phase 1 — Interview, then the config

Skip when `/opt/data/ld/config.json` exists **and** this prints nothing:

    python3 /opt/data/skills/ld-shared/scripts/ld_config_gate.py /opt/data/ld/config.json

(No output is a pass; any text is the list of what is wrong. Its exit code is
always 0 and means nothing — read the output, not the status.) Even when
skipping, you still need `has_mac` (and the optional `ical_url`) for Phases 2
and 3 — ask for those alone if you do not have them from this conversation.
Do NOT re-ask for `pi_address` or `pi_user` here: Phase 2's script recovers
both from the dotenv and refuses by name for whichever it cannot — that
refusal, not this note, is what decides when the owner gets asked.

Otherwise ask, one or two questions per message, in the owner's words:

| answer | ask | goes to |
|---|---|---|
| `owner_name` | what should I call you? | `family.owner.name` |
| `owner_email` | the Google account whose calendar you live by (the email) | `calendar.account`, `calendar.sources[0].calendar_id`, and `calendar_nudge.owner_identities[0]` |
| `extra_calendar_ids` | any shared calendars? — the full id, `…@group.calendar.google.com` | more `calendar.sources[]` |
| `city` | which city is home? | `weather.location`, geocoded to `lat`/`lon` |
| `timezone` | run `echo $TZ`, tell the owner that zone, ask them to confirm it is theirs | `family.timezone` |
| `has_mac` | do you have a Mac with Plow Latch running? (without one, only the calendar tile updates — the weather, sports and message cards need the Mac to ship them — and the Pi has to be set up by hand) | gates `mac_username`; decides the Phase 3 path |
| `mac_username` | your Mac login name (only with a Mac) | `morning_triage.chat_db_path` |
| `pi_address` | the Pi's address on your home network — the IP, or `raspberrypi.local` | Phase 2 (`mint_wall_token.py`) and the Phase 3 `ssh` target; not in the config — after Phase 2 it lives in the dotenv's `DASHBOARD_ENDPOINT_URL`, so a resume never re-asks |
| `pi_user` | the Pi's login user (whatever you set in Raspberry Pi Imager) | the Phase 3 `ssh` target; not in the config — after Phase 2 it lives in the dotenv's `DASHBOARD_PI_USER`, so a resume never re-asks. Letters, digits, `.`, `_`, `-` only — refuse anything else: it lands in `ssh` argv |
| `ical_url` | optional: the wall's own calendar tile reads a feed directly. In Google Calendar → Settings → the calendar → "Secret address in iCal format", copy that URL. Blank is fine — the tile stays empty until you give me one. | Phase 2 (the `ical_url` key of `mint_wall_token.py`'s stdin JSON); not in the config |
| `owner_imessage`, `people`, `digest_length` | optional: your number, household names, how long the Sunday digest should be | `family`, `weekly_digest.length` |
| `teams` | optional: teams to follow, each as ESPN abbreviation + sport + league, e.g. `[{"abbr":"chc","sport":"baseball","league":"mlb"}]` | `sports.followed` |

`ical_url` is a private feed URL — anyone holding it reads that calendar. It
arrives in the owner's own message, which you cannot undo; from there it goes
into exactly one place, the `ical_url` key of Phase 2's stdin JSON. Never repeat it
back in chat and never put it in any other call.

**The timezone is not negotiable here.** If the owner's zone is not `$TZ`,
the script below refuses and says why: the container's zone is `AGENT_TZ`
in the instance dotenv on the host, which only the operator can change.
Tell the owner to ask their operator to set `AGENT_TZ` to their zone and run
`agent-mgr up` again, then come back to you. Do not write a config that
agrees with the container instead — the cards would land at the wrong hour.

Compose ONE JSON object from the answers (keys exactly as the table's first
column; `has_mac` a real boolean, not `"yes"` or `1`; list keys as lists,
`teams` a list of objects shaped as above; `pi_address` and `pi_user` may be
included — the script ignores them. Leave `ical_url` out of the JSON entirely
— its only destination is Phase 2's own stdin JSON) and feed it on
stdin — never on argv — to:

    /opt/data/skills/ld-setup/scripts/write_config.py <<'EOF'
    { ...the answers... }
    EOF

It geocodes the city, judges the result with the shared gate, and writes
`/opt/data/ld/config.json` mode 600. `refusing to write:` means fix what it
names and run it again; `wrote /opt/data/ld/config.json (mode 600); gate: PASS`
means Phase 1 is done. The full shape it writes is
`/opt/data/skills/ld-shared/references/config.example.json`.

## Phase 2 — The wall's token

Idempotent, so there is no dotenv to inspect by hand — always run it. Its
answers ride stdin as ONE JSON object through a quoted heredoc, never argv
and never interpolated into shell text — the same rule as Phase 1, because
an embedded quote in an owner's answer would otherwise execute before the
script's validation (which holds `pi_address` and `pi_user` to the safe
charset Phase 3's `ssh` argv depends on) could see it:

    /opt/data/skills/ld-setup/scripts/mint_wall_token.py <<'EOF'
    {"pi_address": "...", "pi_user": "...", "ical_url": "..."}
    EOF

Leave the `ical_url` key out entirely when the owner gave no feed *this run*
— an absent key keeps whatever feed `pi.env` already carries, and only a
first run with no `pi.env` yet writes it blank (the viewer shows an empty
calendar tile until a later re-run supplies one).

Every key may be left out on a resume — `{}` is a valid stdin object: the
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

- `/opt/data/ld/pi.env` — the Pi's `~/ld-data/.env` (`ICAL_URL=` and
  `DASHBOARD_TOKEN=`). Shipped in Phase 3.
- `/opt/data/ld/dashboard.hdr` — `Authorization: Bearer …`, the header every
  `curl` from the Mac reads. Ship it now, when the owner has a Mac:

      plow_write_file(path="~/Plow/ld/dashboard.hdr", content=<the content of /opt/data/ld/dashboard.hdr, read with your file tool>)

  That call is the one time you handle the token, and its content goes into
  that call and nowhere else — not into chat, not into argv. Paste the
  call's *result* (not its content) verbatim. Phase 2 is done when the
  script exited 0 and, with a Mac, that write succeeded.

Everything after this line that is written `<pi_address>` or `<pi_user>` is a
placeholder bound from the `pi_target=<pi_user>@<pi_address>` line Phase 2's
script printed — the one authoritative, validated ssh target; never bind
either half from memory or a guess. Substitute both before you make the
call, and never send the literal angle brackets to the Mac.

## Phase 3 — Bring the Pi up through Latch

**With a Mac:** when this returns JSON carrying a `sha`, the viewer is up
and the token is the one it checks (a Latch call, so it belongs to this path
only; the no-Mac path has its own gate below) — skip the bring-up, EXCEPT
the ship-and-restart step at the end of this path, which still runs whenever
Phase 2 printed `re-pointed:` or this run supplied `ical_url`: the freshly
rewritten `pi.env` has to reach the Pi or the new feed/address never takes
effect.

    plow_run_command(argv=["sh","-c","curl -fsS -H @$HOME/Plow/ld/dashboard.hdr http://<pi_address>:5174/api/version"], network=true)

**Read the failure before treating it as "viewer not up."** A `curl` exit 22
(an HTTP 4xx) means something *answered* on 5174 — probe the always-open
liveness path to tell the two states apart:

    plow_run_command(argv=["sh","-c","curl -fsS http://<pi_address>:5174/healthz"], network=true)

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

    plow_run_command(argv=["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "<pi_user>@<pi_address>", "true"], network=true)

If that fails, the **one hands-on moment**: tell the owner to run
`ssh-copy-id <pi_user>@<pi_address>` in their own Mac terminal. That is not
something you can do for them — Latch has no stdin and no env to hand a
password through, and its argv goes on the approval card and into the audit
record, so a password there would be a password written down. Once they
confirm it ran, re-probe; do not proceed on a refused probe.

Once the probe succeeds, run each Pi line the same way with `network=true`
and a `timeout` of `600000` (ten minutes — `apt-get` is slow on a Pi; a run
this long comes back as a job handle, so poll it with `plow_get_output`
rather than waiting on the call). `<pi_line_1>` and `<pi_line_2>` are the two
values Phase 2 printed, each dropped whole into one argv element:

    plow_run_command(argv=["ssh", "-o", "BatchMode=yes", "<pi_user>@<pi_address>", "<pi_line_1>"], network=true, timeout=600000)
    plow_run_command(argv=["ssh", "-o", "BatchMode=yes", "<pi_user>@<pi_address>", "<pi_line_2>"], network=true, timeout=600000)

Raspberry Pi OS grants the Imager's primary user passwordless sudo by
default; if `pi_line_1` instead comes back asking for a sudo password, that
assumption was wrong for this Pi — do not try to smuggle one through. Text
`pi_line_1` to the owner and ask them to run it on the Pi themselves, then
continue with `pi_line_2` over Latch as above.

Then ship the Pi's env file — read `/opt/data/ld/pi.env` with your file tool
and make it the `content` (nowhere else), then copy it across and restart
the viewer. The shell text here is static: the owner's two values ride the
trailing argv positions (`$1`/`$2`), so nothing they answered is ever parsed
as shell:

    plow_write_file(path="~/Plow/ld/pi.env", content=<the content of /opt/data/ld/pi.env>)
    plow_run_command(argv=["sh","-c","scp -o BatchMode=yes ~/Plow/ld/pi.env $1@$2:ld-data/.env && ssh -o BatchMode=yes $1@$2 'chmod 600 ld-data/.env && systemctl --user restart life-dashboard-viewer'","sh","<pi_user>","<pi_address>"], network=true, timeout=30000)

Finish with the skip check at the top of this phase: JSON with a `sha` means
the viewer is up and the token is live. Paste it. Any other result — 401
(the token on the Pi is not the one in the header: re-ship `pi.env` and
restart the viewer), connection refused (the viewer is not up yet; wait a
minute and retry, at most a few times), unreachable device (the Mac) — is
reported verbatim, and this phase is not done.

**No Mac (or no Latch):** skip this path when `/opt/data/ld/pi-brought-up`
exists — the file written below on the owner's confirmation. Two exceptions:
if Phase 2 printed `re-pointed:`, this is a *different Pi* — ignore the
marker and run the full fallback below, because the new device has neither
the packages nor its env. If this run only supplied a *new* `ical_url`, ask
the owner to set `ICAL_URL=` in `~/ld-data/.env` on the Pi to the feed URL
they just gave you — do not repeat the URL back (it is a private feed and
they already hold it), and never the token line again — then
`systemctl --user restart life-dashboard-viewer`. Otherwise the
fallback is the direct one, and the token crosses chat once — acknowledged,
not ideal, and the only place in this sheet where that is allowed. Text the
owner, verbatim: (1) `pi_line_1`, (2) `pi_line_2`, and (3) the two lines of
`/opt/data/ld/pi.env` (read with your file tool; this is the one place its
content may be pasted), and say: run the first two on the Pi over ssh or at
its keyboard, then put those two lines in `~/ld-data/.env` on the Pi,
`chmod 600 ~/ld-data/.env`, and run
`systemctl --user restart life-dashboard-viewer`; the screen comes up on its
own within a few minutes. You cannot reach the Pi yourself without a Mac, so
the owner's word that the screen is up is this phase's proof — when they say
it is, write the artifact that keeps a resumed run (or a deleted
setup-complete marker) from texting the token across chat a second time:

    date -u +%FT%TZ > /opt/data/ld/pi-brought-up

## Phase 4 — Crons, and one card

Create-if-missing and safe to re-run, so there is no skip condition of its own
— always run it (Phase 4's artifact is the marker at the end):

    /opt/data/skills/ld-dashboard/scripts/register_crons.py

Paste its output and its exit status; `refusing to register`, `WARNING` or
`PAUSED` means this phase did not finish (see
`/opt/data/skills/ld-dashboard/SKILL.md`).

**Without a Mac, registration is the whole phase** — everything from here to
the owner question is Latch work a no-Mac install cannot do: every pushed
card — weather, sports, messages — stays empty until a Mac with Latch
exists, because delivery always routes through the outbox and nothing ships
it. Do not force a card, do not try to read one back, and do not ask the
owner to look for one — the screen being up was already confirmed when
Phase 3 wrote `pi-brought-up`, so go straight to the marker at the end.

**With a Mac, force the weather card.**
`hermes cron run` takes a job **id**, not a name, and ids are opaque hex, so
look the id up from `/opt/data/cron/jobs.json` (the same file
`register_crons.py` itself trusts, not `hermes cron list`'s human rendering)
first:

    ID=$(python3 -c 'import json,sys
    j=json.load(open("/opt/data/cron/jobs.json"))["jobs"]
    i=next((x["id"] for x in j if x["name"]=="ld-weather"),None)
    sys.exit("refusing: no ld-weather job in /opt/data/cron/jobs.json -- re-run register_crons.py") if i is None else print(i)') && /opt/hermes/bin/hermes cron run "$ID"

The refusal is the whole point of the lookup: no `ld-weather` row means the
registration above did not land, and forcing nothing would look like success.

That run is its own turn: the weather producer composes the tile, and
because the dotenv says `DASHBOARD_DELIVERY=latch` its helper prints
`NOT DELIVERED — ship it through Latch, then paste both outputs:` followed by
the two calls to make. That turn makes exactly those two calls, in that
order, per `/opt/data/skills/ld-shared/references/latch-delivery.md`. Wait
until `/opt/hermes/bin/hermes cron runs` lists that run as finished, then
read the card back the same way the producers write it:

    plow_run_command(argv=["sh","-c","curl -fsS -H @$HOME/Plow/ld/dashboard.hdr 'http://<pi_address>:5174/api/message?card=3'"], network=true)

`{"message": {…"type":"weather"…}}` is the gate: the weather producer's card
is on the Pi. `{"message": null}` means it is not — read the forced run's
output (`/opt/hermes/bin/hermes cron runs`) for what went wrong, fix it, and
force it again.

Then tell the owner: "your wall is live — the weather card should be
showing; is it?" — their answer confirms the screen itself, which is the
one thing the API cannot show you.

Only now — after the owner's confirmation, this phase's for a Mac install,
Phase 3's `pi-brought-up` for a no-Mac one — mark the whole run done; this is
the only thing that writes this file, and nothing before this line should:

    date -u +%FT%TZ > /opt/data/ld/setup-complete
