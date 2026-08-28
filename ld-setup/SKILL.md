---
name: ld-setup
description: First-run setup for the life dashboard — interview the owner over chat, write /opt/data/ld/config.json, mint this agent's kiosk on Plow and bring the Pi up through Latch over the owner's LAN (texting the owner the two lines when there is no Mac), register the producer crons and prove a card. Use when /opt/data/ld/config.json is missing or the ld-config gate refuses it, when the owner asks to set up or re-set-up their dashboard, or when the wall has never shown a card.
---

# Life Dashboard — Setup

Four phases. Each is gated on the artifact it produces, so a phase that has
already landed is skipped and the run resumes where it stopped — a reset, a
rebuilt home or an interrupted chat all pick up from here.

**Every script's output is pasted verbatim with its exit status, and a phase
is not done until you have.** The scripts signal every refusal through their
output and a non-zero exit, and a chat turn does not propagate an exit code.
Do not paraphrase, and do not call a phase done on a non-zero exit. The one
exception is `mint_kiosk.py --status`, whose exit 1 means "not yet paired" --
a poll result, not a failure.

**Never `cat`, `echo`, or otherwise paste `/opt/data/.env` or any line
containing `TOKEN` into chat.** The dotenv carries this agent's own Plow
bearer and the kiosk's dashboard token; the scripts in this skill read it for
you and never need you to.

## Phase 1 — Interview, then the config

Skip when `/opt/data/ld/config.json` exists **and** this prints nothing:

    python3 /opt/data/skills/ld-shared/scripts/ld_config_gate.py /opt/data/ld/config.json

(An empty line is a pass; any text is the list of what is wrong; its exit
code is always 0 and means nothing.)

Otherwise ask, one or two questions per message, in the owner's words:

| answer | ask | goes to |
|---|---|---|
| `owner_name` | what should I call you? | `family.owner.name` |
| `owner_email` | the Google account whose calendar you live by (the email) | `calendar.sources[0].calendar_id` **and** `calendar_nudge.owner_identities[0]` |
| `extra_calendar_ids` | any shared calendars? — the full id, `…@group.calendar.google.com` | more `calendar.sources[]` |
| `city` | which city is home? | `weather.location`, geocoded to `lat`/`lon` |
| `timezone` | run `echo $TZ`, tell the owner that zone, ask them to confirm it is theirs | `family.timezone` |
| `has_mac` | do you have a Mac? (Plow Latch runs there; without it the calendar and message cards stay empty — the weather and sports cards do not need it) | gates `mac_username` |
| `mac_username` | your Mac login name (only with a Mac) | `morning_triage.chat_db_path` |
| `owner_imessage`, `people`, `digest_length` | optional: your number, household names, how long the Sunday digest should be | `family`, `weekly_digest.length` |
| `teams` | optional: teams to follow, each as ESPN abbreviation + sport + league, e.g. `[{"abbr":"chc","sport":"baseball","league":"mlb"}]` | `sports.followed` |

**The timezone is not negotiable here.** If the owner's zone is not `$TZ`,
the script below refuses and says why: the container's zone is `AGENT_TZ`
in the instance dotenv on the host, which only the operator can change.
Tell the owner to ask their operator to set `AGENT_TZ` to their zone and run
`agent-mgr up` again, then come back to you. Do not write a config that
agrees with the container instead — the cards would land at the wrong hour.

Compose ONE JSON object from the answers (keys exactly as the table's first
column; `has_mac` a boolean; list keys as lists, `teams` a list of objects
shaped as above) and feed it on stdin — never on argv — to:

    /opt/data/skills/ld-setup/scripts/write_config.py <<'EOF'
    { ...the answers... }
    EOF

It geocodes the city, judges the result with the shared gate, and writes
`/opt/data/ld/config.json` mode 600. `refusing to write:` means fix what it
names and run it again; `gate: PASS` means phase 1 is done. The full shape it
writes is `/opt/data/skills/ld-shared/references/config.example.json`.

## Phase 2 — The kiosk

Idempotent, so there is no dotenv to inspect by hand — just run it:

    /opt/data/skills/ld-setup/scripts/mint_kiosk.py

It mints this agent's kiosk on Plow with its own token, writes
`DASHBOARD_ENDPOINT_URL` and `DASHBOARD_TOKEN` into the dotenv (no restart
needed — `post_to_kiosk.py` reads that dotenv itself as its third secret
source, because the container env is the gateway's start-time load and never
sees an appended line), and prints the pairing
code's expiry followed by two lines, `pi_line_1=…` (the `apt-get install`) and
`pi_line_2=…` (the `curl … --pair <code>`) — bare, one per line, nothing
shell-wrapped around the value, so the value drops straight into an `ssh`
argv element in the next phase. The code expires in 30 minutes — if it has,
run the script again and it re-mints one. Re-running never mints a second
kiosk.

If its output says the kiosk is **already minted and paired**, the Pi is
already up — skip Phase 2b entirely and go to Phase 3. Otherwise Phase 2b
runs next.

## Phase 2b — bring the Pi up through Latch

The agent is in the cloud and cannot reach the Pi's LAN address; the
owner's Mac, on the same LAN, can — so when the owner has one, this phase
runs the two lines *on the Pi, from the Mac*, over Plow Latch, rather than
asking the owner to type them.

Ask, alongside (or right after) the Phase 1 interview:

| answer | ask | used for |
|---|---|---|
| `pi_address` | the Pi's address on your home network — the IP, or `raspberrypi.local` | the `ssh` target |
| `pi_user` | the Pi's login user (whatever you set in Raspberry Pi Imager) | the `ssh` target |
| — | the Phase 1 `has_mac` answer (do not re-ask); confirm Plow Latch is running on it | which path below runs |

**With a Mac:** probe key auth first — no password should ever need to
cross chat.

    plow_run_command(argv=["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                            "<pi_user>@<pi_address>", "true"], network=True)

If that fails, the **one hands-on moment**: tell the owner to run
`ssh-copy-id <pi_user>@<pi_address>` in their own Mac terminal. The password
has to be typed there and nowhere else — no stdin/env, and argv is on the
approval card. Once they confirm it ran, re-probe; do not proceed on a
refused probe.

Once the probe succeeds, run each Pi line the same way, `-o BatchMode=yes` so
a key-auth regression fails fast instead of hanging on a password prompt with
no tty, `network=True`, and a `timeout` of `600000` (ten minutes — `apt-get` is
slow on a Pi; a run this long comes back as a job handle, so poll it with
`plow_get_output` rather than waiting on the call):

    plow_run_command(argv=["ssh", "-o", "BatchMode=yes", "<pi_user>@<pi_address>", "<pi_line_1>"], network=True, timeout=600000)
    plow_run_command(argv=["ssh", "-o", "BatchMode=yes", "<pi_user>@<pi_address>", "<pi_line_2>"], network=True, timeout=600000)

Paste each command's output verbatim, same as every other script in this
skill. Raspberry Pi OS grants the Imager's primary user passwordless sudo
by default; if `pi_line_1` instead comes back asking for a sudo password,
that assumption was wrong for this Pi — do not try to smuggle one through.
Text `pi_line_1` to the owner and ask them to run it on the Pi themselves,
then continue with `pi_line_2` over Latch as above.

**No Mac (or no Latch):** the fallback is the direct one — text the owner
both `pi_line_1` and `pi_line_2`, verbatim, and say: run the first on the
Pi, then the second; the screen comes up on its own within a few minutes.

Either way, finish by polling, at the owner's pace (ask them to tell you
when the Pi is done; check every few minutes at most):

    /opt/data/skills/ld-setup/scripts/mint_kiosk.py --status

Exit 0 — `paired_at` and `sha` both set — is the Pi paired and running the
viewer. Exit 1 is "not yet": tell the owner (or, on the Latch path, check
the command output above for what went wrong) and wait. Bound the wait: after
about 30 minutes the pairing code has expired, so run `mint_kiosk.py` (no
flags) for a fresh one and redo `pi_line_2` rather than polling a dead code.

## Phase 3 — Crons, and one card

Create-if-missing and safe to re-run, so there is no skip condition — always
run it:

    /opt/data/skills/ld-dashboard/scripts/register_crons.py

Paste its output; `refusing to register`, `WARNING` or `PAUSED` means this
phase did not finish (see `/opt/data/skills/ld-dashboard/SKILL.md`). Then
force the weather card. `hermes cron run` takes a job id, not a name, and ids
are opaque hex, so look the id up from `/opt/data/cron/jobs.json` (the same
file `register_crons.py` itself trusts, not `hermes cron list`'s human
rendering) first:

    ID=$(python3 -c 'import json,sys;j=next((j["id"] for j in json.load(open("/opt/data/cron/jobs.json"))["jobs"] if j["name"]=="ld-weather"),None);sys.exit("no ld-weather job in /opt/data/cron/jobs.json -- re-run register_crons.py") if j is None else print(j)')
    /opt/hermes/bin/hermes cron run "$ID"

and run `/opt/data/skills/ld-setup/scripts/mint_kiosk.py --status` once more.
Its `cards=` list is the gate: **`'3'` present** means the weather producer's
card is on the kiosk. Then tell the owner: "your wall is live — the weather
card should be showing; is it?" — their answer confirms the screen itself,
which is the one thing the route cannot show you.
