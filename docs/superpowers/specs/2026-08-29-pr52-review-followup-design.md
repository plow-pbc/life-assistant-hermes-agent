# PR #52 Review Follow-up Design

## Goal

Resolve the two findings left open by the final Knightwatch review on
`life-assistant-hermes-agent` PR #52 without changing runtime behavior or
expanding the trusted-lines feature.

## Changes

1. Revise the README account-boundary explanation so it distinguishes the
   owner's private, credential-bound conversation from participation in an
   owner-approved trusted group. The trusted-group section remains the
   canonical description of shared tool access.
2. Remove repeated `"owner@example.test"` calendar-account literals from the
   config-gate fixtures by deriving valid calendar shapes from `VALID`. Cases
   that intentionally omit or blank the account remain explicit.

## Scope Fence

This follow-up changes only documentation wording and test-fixture construction.
It does not change the config schema, gate behavior, runtime prompts, trust
policy, deployment mechanism, or dashboard.

## Verification and Delivery

Run the repository's canonical `just test` gate. Open one focused follow-on PR
referencing PR #52, then use the `$babysit-pr` loop until Knightwatch reviews the
current head with no unresolved within-scope findings and all required checks
pass. After merge, restore the `life` agent on `wakeup` and verify that
`https://api.plow.co/app/dashboard` still serves the trusted-lines dashboard.
