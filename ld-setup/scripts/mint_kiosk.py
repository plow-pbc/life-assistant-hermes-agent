#!/usr/bin/env python3
"""mint_kiosk.py -- give this instance a kiosk on Plow, and the owner a way to pair a Pi to it.

POST /v1/kiosks with this instance's OWN Plow bearer (PLOW_AGENT_TOKEN, the
line activation wrote to /opt/data/.env), then append the two names the
producers read -- DASHBOARD_ENDPOINT_URL and DASHBOARD_TOKEN -- to that same
dotenv. No new credential: the bearer the producers post with IS the agent's,
and Plow's /v1/kiosks/<uid>/cards speaks the wire body post_to_kiosk.py
already sends.

These lines are appended AFTER the gateway loaded the dotenv, so the container
env a cron-spawned producer inherits does not have them; post_to_kiosk.py
therefore reads this dotenv itself as its third secret source, which is what
makes the append take effect without a gateway restart.

`--status` asks whether the Pi has paired, what it last deployed, and which
cards the kiosk holds. The pairing code rides in `pi_line_2`, so it lands on
the owner's approval card and in the audit record -- intended: it is
short-lived and single-use, not a credential the way a password is.

Idempotent: a dotenv that already names a kiosk is re-read, not re-POSTed --
a second POST would mint a kiosk the wall was never paired to. Re-minting an
UNPAIRED kiosk's code is safe because Plow returns the same uid for a
bearer-scoped re-mint; `main()` asserts that (refusing by name on a
mismatch) rather than trusting it silently.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)
from runtime_env import DOTENV, dotenv_values  # noqa: E402

DEFAULT_BASE = "https://api.plow.co"
KIOSK_NAME = "Life dashboard"
# apt-get, not apt: apt prints "WARNING: apt does not have a stable CLI
# interface", and the skill reads any WARNING as "this phase did not finish".
# `sudo env ...`, not a bare prefix: sudo's env_reset drops a caller-set
# DEBIAN_FRONTEND before apt-get ever sees it.
APT_LINE = ("sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y"
            " nodejs npm git chromium fonts-noto-color-emoji")
BOOTSTRAP = ("curl -fsSL https://raw.githubusercontent.com/plow-pbc/life-dashboard/main/updater/bootstrap.sh"
             " | sh -s -- --pair {code}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Same reason as post_to_kiosk.py: a followed 3xx forwards the bearer."""

    def redirect_request(self, *_args, **_kwargs):
        return None


def request_json(method, url, token, body=None):
    req = urllib.request.Request(
        url, method=method, data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"error: {method} {url} returned HTTP {exc.code} {exc.reason}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"error: {method} {url} failed: {exc.reason}") from None


def kiosk_uid(values, base):
    """The uid the dotenv already points at, or None."""
    url = values.get("DASHBOARD_ENDPOINT_URL", "").strip()
    m = re.fullmatch(re.escape(base) + r"/v1/kiosks/([\w-]+)/cards", url)
    return m.group(1) if m else None


def append_dotenv(path, pairs):
    # The leading newline is not decoration: a dotenv the gateway or a person
    # last wrote may not end in one, and a bare append would splice the first
    # key onto PLOW_AGENT_TOKEN=... -- taking the instance off its chat, not
    # just off its wall. runtime_env skips the blank line it may leave.
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n" + "".join(f"{k}={v}\n" for k, v in pairs))


PAIRING_CODE_RE = re.compile(r"[A-Za-z0-9-]+")


def _owner_lines(minted):
    code = minted["pairing_code"]
    if not PAIRING_CODE_RE.fullmatch(code):
        raise SystemExit(
            f"refusing: pairing_code {code!r} from the mint response is not [A-Za-z0-9-] -- "
            "it lands in an ssh argv element in the next phase and is not safe to run as-is"
        )
    print(f"pairing code expires {minted['expires_at']}.")
    print(f"pi_line_1={APT_LINE}")
    print(f"pi_line_2={BOOTSTRAP.format(code=code)}")


def resolve_base():
    """The API base this instance mints against -- the gateway's own startup
    environment, or the hardcoded default. Never the dotenv: /opt/data/.env is
    agent-writable at runtime, so trusting a PLOW_API_BASE line there would let
    an injected turn redirect this instance's own account-wide bearer to an
    attacker host.
    """
    return (os.environ.get("PLOW_API_BASE") or DEFAULT_BASE).strip().rstrip("/")


def main(argv=None, dotenv_path=DOTENV):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--status", action="store_true", help="has the Pi paired, and what did it deploy?")
    args = parser.parse_args(argv)

    values = dotenv_values(dotenv_path)
    token = values.get("PLOW_AGENT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            f"refusing: PLOW_AGENT_TOKEN is unset or blank in {dotenv_path} -- activation writes "
            "it; has `agent-mgr activate` completed for this instance?"
        )
    base = resolve_base()
    uid = kiosk_uid(values, base)

    if args.status:
        if not uid:
            raise SystemExit(f"no kiosk yet: {dotenv_path} has no DASHBOARD_ENDPOINT_URL -- run without --status first")
        kiosk = request_json("GET", f"{base}/v1/kiosks/{uid}", token)
        status = kiosk.get("status") or {}
        cards = sorted((kiosk.get("cards") or {}))
        print(f"kiosk {uid}: paired_at={kiosk.get('paired_at')} sha={status.get('sha')} "
              f"deployed_at={status.get('deployed_at')} last_result={status.get('last_result')} "
              f"cards={cards}")
        return 0 if kiosk.get("paired_at") and status.get("sha") else 1

    if uid:
        kiosk = request_json("GET", f"{base}/v1/kiosks/{uid}", token)
        if kiosk.get("paired_at"):
            print(f"kiosk {uid} is already minted and paired ({kiosk['paired_at']}); nothing to do.")
            return 0
        print(f"kiosk {uid} is minted but not paired yet; re-minting its pairing code.")
        minted = request_json("POST", f"{base}/v1/kiosks", token, {"name": KIOSK_NAME})
        if minted["uid"] != uid:
            raise SystemExit(
                f"refusing: re-mint returned kiosk {minted['uid']}, not {uid} in {dotenv_path}"
            )
        _owner_lines(minted)
        return 0

    minted = request_json("POST", f"{base}/v1/kiosks", token, {"name": KIOSK_NAME})
    append_dotenv(dotenv_path, [
        ("DASHBOARD_ENDPOINT_URL", f"{base}/v1/kiosks/{minted['uid']}/cards"),
        ("DASHBOARD_TOKEN", token),
    ])
    print(f"minted kiosk {minted['uid']}; wrote DASHBOARD_ENDPOINT_URL and DASHBOARD_TOKEN to {dotenv_path}.")
    _owner_lines(minted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
