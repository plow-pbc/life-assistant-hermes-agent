#!/usr/bin/env python3
"""payment_cap.py — the soft daily ceiling for ld-payments.

The SECONDARY bound only. The real bound on any payment is the owner's
per-payment confirmation (the platform's fail-closed gate releases the banking
credential only after the owner approves that specific payment with the code
sent to their thread — see ../SKILL.md). This cap is a backstop against a
runaway day: the running total of payments the owner has approved today must
stay at or under it. The agent tallies today's already-approved payments from
the owner thread (the authoritative approval record) and asks this script
whether one more fits — the arithmetic lives here, not in the model, so a long
session cannot fumble the sum.

Stateless on purpose: it keeps no ledger and reads no files. There is no
enforcement here — enforcement is the platform gate — only a deterministic
"does this fit under today's remaining budget" answer.

Verdict is the first token of stdout — WITHIN or EXCEEDS — so a caller can read
it without parsing. Both are valid, successful answers, so both exit 0; only
malformed input (a negative or non-numeric amount) exits 2, loudly, because a
cap check that silently mis-parsed its inputs is worse than no check.
"""
from __future__ import annotations

import argparse
import sys

# TODO(hermes-cap-read): v1 hard-codes the cap. Once the Hermes cap-read path is
# wired, read the owner's dashboard-set per-line value
# `Session.daily_payment_cap_usd` (exposed via the plow API) and fall back to
# this constant only when that read is unavailable.
DAILY_PAYMENT_CAP_USD = 200.0


def _cents(dollars: float) -> int:
    """Dollars to whole cents. Integer cents dodge float drift at the cap edge."""
    return round(dollars * 100)


def remaining_cents(spent_today_usd: float) -> int:
    """Cents left under today's cap given what has already been approved today."""
    return _cents(DAILY_PAYMENT_CAP_USD) - _cents(spent_today_usd)


def within_cap(spent_today_usd: float, amount_usd: float) -> bool:
    """True iff approving `amount_usd` keeps today's total at or under the cap."""
    return _cents(amount_usd) <= remaining_cents(spent_today_usd)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Soft daily payment-cap check.")
    p.add_argument("--spent-today", type=float, required=True,
                   help="USD of payments the owner has already approved today")
    p.add_argument("--amount", type=float, required=True,
                   help="USD of the payment being considered now")
    args = p.parse_args(argv)

    # Fail loud: a negative input is upstream drift, not a payment.
    if args.spent_today < 0 or args.amount < 0:
        print("EXIT: --spent-today and --amount must be >= 0", file=sys.stderr)
        return 2

    new_total = args.spent_today + args.amount
    remaining = remaining_cents(args.spent_today) / 100
    if within_cap(args.spent_today, args.amount):
        print(f"WITHIN cap: this ${args.amount:.2f} payment brings today's total "
              f"to ${new_total:.2f}, within the ${DAILY_PAYMENT_CAP_USD:.2f} daily "
              f"cap (${remaining:.2f} was left before it).")
        return 0
    print(f"EXCEEDS cap: this ${args.amount:.2f} payment would bring today's total "
          f"to ${new_total:.2f}, over the ${DAILY_PAYMENT_CAP_USD:.2f} daily cap "
          f"(only ${remaining:.2f} left today). Tell the owner and do not proceed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
