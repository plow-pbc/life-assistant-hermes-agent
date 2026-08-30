#!/usr/bin/env python3
"""payment_cap.py — the advisory daily guideline for ld-payments.

This is a secondary, best-effort check. The owner still decides every payment
through the platform approval flow described in ../SKILL.md. The agent tallies
today's already-approved payments from the owner thread and asks this script
whether one more fits the configured guideline — the arithmetic lives here, not
in the model, so a long session cannot fumble the sum.

Stateless on purpose: it keeps no ledger. It reads the current assistant's cap
from Plow on each invocation, then provides a deterministic "does this fit
under today's remaining guideline" answer. It is not an atomic platform limit:
overlapping turns can observe the same tally.

Verdict is the first token of stdout — WITHIN or EXCEEDS — so a caller can read
it without parsing. Both are valid, successful answers, so both exit 0; only
malformed input (a negative or non-numeric amount) exits 2, loudly, because a
cap check that silently mis-parsed its inputs is worse than no check.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.request

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "ld-shared", "scripts"),
)
from bearer_http import open_no_redirect  # noqa: E402
from runtime_env import DOTENV, dotenv_values  # noqa: E402

PLOW_API_ORIGIN = "https://api.plow.co"


def _preference_credentials() -> tuple[str, str]:
    """Resolve the same agent identity activation installed for this runtime."""
    dotenv = dotenv_values(os.environ.get("PLOW_RUNTIME_ENV_FILE", DOTENV))
    token = (os.environ.get("DOMO_MCP_TOKEN")
             or dotenv.get("DOMO_MCP_TOKEN", "")).strip()
    if not token:
        raise ValueError("DOMO_MCP_TOKEN is empty")
    return PLOW_API_ORIGIN, token


def read_daily_cap_usd() -> float | None:
    """Read this assistant's owner-managed cap without exposing its token."""
    api_url, token = _preference_credentials()
    request = urllib.request.Request(
        f"{api_url}/v1/api-keys/current/preferences",
        headers={"Authorization": f"Bearer {token}"},
    )
    with open_no_redirect(request, timeout=10) as response:
        payload = json.load(response)
    raw = payload["daily_payment_cap_usd"]
    if raw is None:
        return None
    cap = float(raw)
    if not math.isfinite(cap) or cap < 0:
        raise ValueError("daily payment cap is not finite and non-negative")
    return cap


def _cents(dollars: float) -> int:
    """Dollars to whole cents. Integer cents dodge float drift at the cap edge."""
    return round(dollars * 100)


def remaining_cents(spent_today_usd: float, daily_cap_usd: float) -> int:
    """Cents left under today's cap given what has already been approved today."""
    return _cents(daily_cap_usd) - _cents(spent_today_usd)


def within_cap(spent_today_usd: float, amount_usd: float, daily_cap_usd: float) -> bool:
    """True iff approving `amount_usd` keeps today's total at or under the cap."""
    return _cents(amount_usd) <= remaining_cents(spent_today_usd, daily_cap_usd)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Soft daily payment-cap check.")
    p.add_argument("--spent-today", type=float, required=True,
                   help="USD of payments the owner has already approved today")
    p.add_argument("--amount", type=float, required=True,
                   help="USD of the payment being considered now")
    args = p.parse_args(argv)

    # Fail loud: a negative or non-finite input is upstream drift, not a
    # payment. argparse's float() accepts "nan"/"inf", which slip past a bare
    # `< 0` and would crash `round(inf * 100)` with a raw traceback — the exact
    # uncontrolled exit this guard promises not to have.
    if (not math.isfinite(args.spent_today) or not math.isfinite(args.amount)
            or args.spent_today < 0 or args.amount < 0):
        print("EXIT: --spent-today and --amount must be finite and >= 0",
              file=sys.stderr)
        return 2

    try:
        daily_cap_usd = read_daily_cap_usd()
    except (KeyError, OSError, TypeError, ValueError) as exc:
        print(
            "EXIT: could not read the current daily payment cap from Plow "
            f"({type(exc).__name__})",
            file=sys.stderr,
        )
        return 2

    new_total = args.spent_today + args.amount
    if daily_cap_usd is None:
        print(
            f"WITHIN guideline: this ${args.amount:.2f} payment brings today's "
            f"total to ${new_total:.2f}; this assistant has no daily cap configured."
        )
        return 0

    remaining = remaining_cents(args.spent_today, daily_cap_usd) / 100
    if within_cap(args.spent_today, args.amount, daily_cap_usd):
        print(f"WITHIN cap: this ${args.amount:.2f} payment brings today's total "
              f"to ${new_total:.2f}, within the ${daily_cap_usd:.2f} daily "
              f"cap (${remaining:.2f} was left before it).")
        return 0
    print(f"EXCEEDS cap: this ${args.amount:.2f} payment would bring today's total "
          f"to ${new_total:.2f}, over the ${daily_cap_usd:.2f} daily cap "
          f"(only ${remaining:.2f} left today). Tell the owner and do not proceed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
