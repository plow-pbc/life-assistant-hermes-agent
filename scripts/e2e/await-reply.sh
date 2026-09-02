#!/usr/bin/env bash
# Block until the agent's next outbound message lands, then print how long it
# took. Counting messages rather than grepping the rendered transcript: a reply
# is often several lines and may carry a media part, so a line-shaped test
# either fires early or never fires at all.
#
# The baseline comes from send.sh, which recorded it BEFORE it posted. Taking
# it here instead loses every reply faster than this script's own startup: the
# count would already include that reply, the loop would sit waiting for a
# SECOND one that is never coming, and a turn that worked would be reported as
# a timeout after the full wait.
#
# usage: await-reply.sh [timeout-seconds] [--since N]
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
require TWIN_HOST_BASE TWIN_THREAD

timeout=180
before=""
while [ $# -gt 0 ]; do
  case "$1" in
    --since) before="$2"; shift 2;;
    *) timeout="$1"; shift;;
  esac
done

if [ -z "$before" ]; then
  if [ -f "$BASELINE_FILE" ]; then
    before="$(cat "$BASELINE_FILE")"
    # Consumed, not merely read. A leftover baseline is worse than none: the
    # next wait would return instantly on the previous wait's own reply.
    rm -f "$BASELINE_FILE"
  else
    echo "no baseline from send.sh -- counting from now, so a reply that has" >&2
    echo "already arrived will not be seen. Pass --since N to be exact." >&2
    before="$(outbound_count)"
  fi
fi

start="$(date +%s)"
# Checked before the first sleep, so a reply that landed while send.sh was
# still returning is reported at once instead of costing a poll interval.
while :; do
  if [ "$(outbound_count)" -gt "$before" ]; then
    echo "reply in $(( $(date +%s) - start ))s"
    exit 0
  fi
  [ "$(( $(date +%s) - start ))" -lt "$timeout" ] || break
  sleep 2
done
echo "no reply within ${timeout}s (outbound still at the $before this send started from)" >&2
exit 1
