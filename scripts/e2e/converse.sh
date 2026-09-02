#!/usr/bin/env bash
# Walk one scripted conversation, one message per turn, waiting for a REAL
# reply before the next goes out.
#
# Pacing is not politeness here. Hermes treats a message that arrives while a
# turn is running as a redirect of that turn, not a new one -- so a script that
# sends on a timer collapses the whole conversation into a single turn that
# answers everything at the end, does all the config work correctly, and leaves
# a transcript with no conversation in it. Measured: five sends became one
# 425-second turn, 49 api calls, one summary message.
#
# turn.sh is what makes the pacing honest: it waits for the agent to stop
# talking rather than for its first message. An earlier version of this script
# waited on await-reply.sh and then slept a flat 12 seconds for "the rest of
# them" -- which is a guess, too long for most turns and too short for the ones
# that matter.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

timeout="${TURN_TIMEOUT:-900}"
for message in "$@"; do
  printf '  owner: %s\n' "$message"
  # `|| status=$?` and not a bare call: lib.sh sets -e, so a non-zero turn.sh
  # would otherwise abort this script before the case below could tell the two
  # failures apart, and the caller would see turn.sh's exit code with none of
  # the explanation.
  status=0
  "$E2E_DIR/turn.sh" "$message" "$timeout" || status=$?
  case $status in
    0) ;;
    2) echo "  !! turn had not finished after ${timeout}s -- stopping before the" >&2
       echo "     next message lands mid-turn and redirects it" >&2
       exit 1;;
    *) echo "  !! no reply within ${timeout}s -- stopping" >&2
       exit 1;;
  esac
done
echo "conversation complete"
