# Latch delivery — when a producer prints `NOT DELIVERED`

The Pi keeps its own server (`POST /api/message` behind `DASHBOARD_TOKEN`,
port 5174, latest post per card) on the household LAN, and this agent cannot
reach that LAN. The owner's Mac can, and latch reaches the Mac — so when
`/var/lib/hermes/ld/.env` says `DASHBOARD_DELIVERY=latch`, `post_to_kiosk.py` does not
POST. It writes the exact wire body to `/var/lib/hermes/ld/outbox/card-<n>.json`
(mode 600) and prints this block, with `<n>` and `<pi>` filled in:

    NOT DELIVERED — ship it through latch, then paste both outputs:
    1. latch file put --host <mac> --plow-chat <chat> /var/lib/hermes/ld/outbox/card-<n>.json '~/Plow/ld/card-<n>.json'
    2. latch exec --host <mac> --plow-chat <chat> --network -- sh -c 'curl -fsS -H @$HOME/Plow/ld/dashboard.hdr -H "Content-Type: application/json" --data-binary @$HOME/Plow/ld/card-<n>.json http://<pi>:5174/api/message'

Run those two commands, in that order and nothing else, each exactly as
printed with two substitutions: `<mac>` is the owner's Mac — in `latch
status`, the online host with `messages` enabled — and `<chat>` is this
chat's uid, as the latch section gives it (a scheduled turn has none, so
there it is `$PLOW_HOME_CHANNEL`). `--network` is required for the
LAN request. The token never appears in the block and never should: it is in
`~/Plow/ld/dashboard.hdr` on the Mac, written once by `ld-wall-setup`, and
`-H @…` reads it there.

**The run is not done until step 2's curl returned 2xx.** Paste both outputs
verbatim. A successful `curl -fsS` exits 0 and prints the Pi's `{"ok":true}`
as its `stdout`; any other status is a non-zero `exit_code` with `curl: (22)
…` in its `stderr` — say so and stop; do not pretend the card landed.

If latch refuses either call (exit 4), stand down: tell the owner what was
refused, and do not retry it in a loop.

## When the Mac is asleep or latch cannot reach it

There is no Mac online with `messages` enabled, or either call fails with
`host_offline` (exit 3). Do not retry in a loop, do not try another host, and
do not queue the card: report in chat, in these words — "Mac unreachable,
card not delivered; next scheduled run retries" — and end the run. Cards
refresh only while the owner's Mac is awake and reachable; that is an
accepted cost of keeping the store on the Pi, and the next scheduled run
recomposes and re-delivers on its own.
