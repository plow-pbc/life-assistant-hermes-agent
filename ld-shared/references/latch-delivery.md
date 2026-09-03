# Latch delivery — when a producer prints `NOT DELIVERED`

The Pi keeps its own server (`POST /api/message` behind `DASHBOARD_TOKEN`,
port 5174, latest post per card) on the household LAN, and this agent cannot
reach that LAN. The owner's Mac can, and it runs Plow Latch — so when
`/var/lib/hermes/ld/.env` says `DASHBOARD_DELIVERY=latch`, `post_to_kiosk.py` does not
POST. It writes the exact wire body to `/var/lib/hermes/ld/outbox/card-<n>.json`
(mode 600) and prints this block, with `<n>`, `<pi>` and the JSON filled in:

    NOT DELIVERED — ship it through Latch, then paste both outputs:
    1. mcp__plow__plow_write_file  path=~/Plow/ld/card-<n>.json  content=<the JSON below>
    2. mcp__plow__plow_run_command argv=["sh","-c","curl -fsS -H @$HOME/Plow/ld/dashboard.hdr -H 'Content-Type: application/json' --data-binary @$HOME/Plow/ld/card-<n>.json http://<pi>:5174/api/message"] network=true
    <json>

Make exactly those two calls, in that order: the JSON verbatim as the
`content` of step 1, the argv verbatim in step 2. Paths under `~/Plow`
auto-approve on the Mac; `network=true` is required for the LAN request. The
token never appears in the block and never should: it is in
`~/Plow/ld/dashboard.hdr` on the Mac, written once by `ld-wall-setup`, and
`-H @…` reads it there.

**The run is not done until step 2 returned 2xx.** Paste both outputs
verbatim. A successful `curl -fsS` prints the Pi's `{"ok":true}`; any other
status prints `curl: (22) …` and the call reports a non-zero exit — say so
and stop; do not pretend the card landed.

## When the Mac is asleep or Latch is not running

Step 1 or step 2 fails with the relay's unreachable-device error. Do not
retry in a loop and do not queue the card: report in chat, in these words —
"Mac unreachable, card not delivered; next scheduled run retries" — and end
the run. Cards refresh only while the owner's Mac is awake with Latch
running; that is an accepted cost of keeping the store on the Pi, and the
next scheduled run recomposes and re-delivers on its own.
