#!/usr/bin/env bash
# One whole turn: text the agent, then wait for it to STOP talking.
#
# `send.sh` then `await-reply.sh` waits for the FIRST outbound message, and a
# reply is routinely several. Sending the next line then puts it into a turn
# that is still running, and Hermes treats a message arriving mid-turn as a
# REDIRECT of that turn rather than a new one -- so a scripted conversation
# collapses into a single turn that answers everything at the end and leaves a
# transcript with no conversation in it. Measured: five sends became one
# 425-second turn, 49 api calls, one summary message.
#
# Waiting for the typing indicator to go inactive does NOT fix this. The twin
# clears typing on every outbound message it stores (`_add_outbound_message`,
# dtu/linq/linq_twin/store.py), so typing is already false the instant the first
# of three messages lands -- the same instant await-reply.sh returns. It is also
# unusable alone as a stop signal: the indicator has a 90s TTL, so a turn that
# dies mid-flight leaves it true and the wait hangs for a minute and a half.
#
# So: a quiet window. The turn is over when nothing new has arrived for
# TURN_SETTLE seconds AND the agent is not typing. Typing is the right SECOND
# half of that test -- it holds the window open across a long tool call that
# would otherwise look like silence -- but never the first.
#
# send.sh and await-reply.sh stay as they are. await-reply.sh is still the right
# tool for "did anything come back at all"; this composes them rather than
# replacing either.
#
# usage: turn.sh <text> [timeout]
#   TURN_SETTLE   seconds of quiet that end a turn (default 6)
#   TURN_TIMEOUT  default overall timeout (default 300)
# exit 0 the turn completed | 1 nothing ever arrived | 2 still emitting at the
# timeout -- a turn that ran and did not finish is not the same failure as one
# that never started, and a caller walking a conversation must not treat them
# alike.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
require TWIN_HOST_BASE TWIN_THREAD LINE_PHONE MEMBER_PHONE
[ $# -ge 1 ] || { echo "usage: turn.sh <text> [timeout]" >&2; exit 2; }

text="$1"
timeout="${2:-${TURN_TIMEOUT:-300}}"
settle="${TURN_SETTLE:-6}"

before="$(outbound_count)"
start="$(date +%s)"
"$E2E_DIR/send.sh" "$text" >/dev/null
# send.sh leaves a baseline for an await-reply.sh that is not coming here. Left
# behind it would be picked up by the NEXT await-reply.sh, which would then
# measure against a count from before this turn and return instantly.
rm -f "$BASELINE_FILE"

count="$before"
last_change="$start"
while :; do
  read -r now typing <<<"$(chat_state)"
  if [ "$now" -gt "$count" ]; then
    count="$now"
    last_change="$(date +%s)"
  fi
  elapsed=$(( $(date +%s) - start ))
  quiet=$(( $(date +%s) - last_change ))

  if [ "$count" -gt "$before" ] && [ "$quiet" -ge "$settle" ] && [ "$typing" = 0 ]; then
    echo "turn done in ${elapsed}s -- $(( count - before )) message(s)"
    exit 0
  fi
  if [ "$elapsed" -ge "$timeout" ]; then
    if [ "$count" -gt "$before" ]; then
      echo "turn still going at ${timeout}s -- $(( count - before )) message(s) so far" >&2
      exit 2
    fi
    echo "no reply within ${timeout}s" >&2
    exit 1
  fi
  sleep 2
done
