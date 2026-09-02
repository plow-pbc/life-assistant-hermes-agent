#!/usr/bin/env bash
# Mint this loop's agent credential and its chat, and write scripts/e2e/.env.
#
# This is the real activation flow -- the same one `agent-mgr activate` drives
# and the same one an owner completes from their phone -- run against the local
# stack, where the twin plays the phone network so nobody has to text anything.
# It hands back BOTH halves the agent needs: the scoped bearer and the chat it
# is granted, so no credential is minted by hand and no row is written directly.
#
# The $100 welcome credit rides along: a user created on this path is created
# with one (webhook.py, WELCOME_CREDIT_USD), and without a positive balance
# every model call comes back 402 Insufficient balance. Nothing to seed.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

API="${PLOW_API_HOST_BASE:-https://api.plow.orb.local}"
TWIN="${TWIN_HOST_BASE:-https://dtu-linq.plow.orb.local}"

# The member phone plays the owner's handset. It must be OUTSIDE the twin's
# managed pool (+15550000001..6) and must not already hold a thread with
# another account on the line /activate picks, or provisioning dies with
# CrossOwnerCollisionError and the redeem stays "pending" forever. A random
# suffix is the cheap way to stay clear of the leftovers in a long-lived dev DB.
MEMBER="${1:-+1555765$(printf '%04d' $((RANDOM % 10000)))}"

activation="$(curl -fsS -X POST -H "Content-Type: application/json" \
  -d '{"name":"e2e-onboarding-v2","provision_chat":true}' "$API/v1/auth/activate")"
code="$(printf '%s' "$activation" | python3 -c 'import sys,json;print(json.load(sys.stdin)["display_code"])')"
secret="$(printf '%s' "$activation" | python3 -c 'import sys,json;print(json.load(sys.stdin)["activation_secret"])')"
line="$(printf '%s' "$activation" | python3 -c 'import sys,json;print(json.load(sys.stdin)["send_to"])')"
echo "activation on $line; the handset is $MEMBER"

# The code goes in through the twin, never at Plow: POST /channels/linq/event
# is HMAC-verified and only the twin holds the secret.
curl -fsS -X POST -H "Content-Type: application/json" \
  -d "$(python3 -c 'import json,sys;print(json.dumps({"to_phone":sys.argv[1],"remote_phone":sys.argv[2],"text":sys.argv[3]}))' "$line" "$MEMBER" "$code")" \
  "$TWIN/ui/inbound" >/dev/null

redeem=""
for _ in $(seq 1 15); do
  sleep 2
  redeem="$(curl -fsS -X POST -H "Content-Type: application/json" \
    -d "{\"activation_secret\":\"$secret\"}" "$API/v1/auth/activate/redeem")"
  case "$redeem" in *'"verified"'*) break;; esac
done
case "$redeem" in
  *'"verified"'*) ;;
  *) echo "activation never completed (last: $redeem)." >&2
     echo "check: docker logs plow-api-1 | grep CrossOwnerCollision -- then retry with another handset:" >&2
     echo "  scripts/e2e/activate.sh +15557650123" >&2
     exit 1;;
esac

python3 - "$redeem" "$API" "$TWIN" "$line" "$MEMBER" "$E2E_DIR/.env" <<'PY'
import json, os, sys
redeem, api, twin, line, member, dest = sys.argv[1:7]
d = json.loads(redeem)
token, chat = d["token"], d["chat"]
mine = {
    "PLOW_API_HOST_BASE": api,
    "TWIN_HOST_BASE": twin,
    "PLOW_API_BASE": api,
    "PLOW_AGENT_TOKEN": token,
    "HERMES_CUSTOM_PLOW_API_KEY": token,
    "PLOW_HOME_CHANNEL": chat["uid"],
    "TWIN_THREAD": chat["provider_key"],
    "LINE_PHONE": line,
    "MEMBER_PHONE": member,
    "TZ": "America/Los_Angeles",
}
# MERGE, never clobber. This file is also where a person hands the loop things
# no activation can regenerate -- a Latch device URL and its token, say -- and
# an earlier version of this script rewrote the whole file, so re-running it
# silently destroyed them with nothing to restore from. Keys this script owns
# are replaced; every other line survives, comments and order included.
kept, seen = [], set()
if os.path.exists(dest):
    for raw in open(dest):
        stripped = raw.strip()
        key = stripped.split("=", 1)[0] if "=" in stripped and not stripped.startswith("#") else None
        if key in mine:
            seen.add(key)
            kept.append(f"{key}={mine[key]}\n")
        else:
            kept.append(raw if raw.endswith("\n") else raw + "\n")
kept += [f"{k}={v}\n" for k, v in mine.items() if k not in seen]
with open(dest, "w") as f:
    f.writelines(kept)
print(f"wrote {dest}: chat {chat['uid']} / twin thread {chat['provider_key']}")
extra = sorted(k for k in (l.split("=", 1)[0].strip() for l in kept if "=" in l and not l.startswith("#")) if k not in mine)
if extra:
    print("kept, untouched: " + ", ".join(extra))
PY
chmod 600 "$E2E_DIR/.env"
