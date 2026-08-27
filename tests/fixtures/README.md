# Captured formats

Files here are **real output from a real system**, captured once and committed so
the tests pin what the tool actually writes rather than what this repo assumes it
writes. Every value that could carry private content is `<scrubbed>`; the field
*names* are the contract and are verbatim.

| file | captured from | version |
|---|---|---|
| `hermes-cron-jobs.json` | `/opt/data/cron/jobs.json` in the live `str` agent on `wakeup`, 2026-08-27 | Hermes Agent v0.19.0 (2026.7.20), upstream `b4f8c491` |

Re-capture with:

    docker exec <container> cat /opt/data/cron/jobs.json   # then scrub values

## Measured, not captured

`/opt/data/cron/` exists before any cron job does — the gateway creates it at
start, not lazily on first save. Checked on two homes that never had a single
job: the retired `~/.hermes-life` (stood up 04:50, torn down 14:45, never
activated) and `~/.hermes-sam-property`, both carrying `cron/` with
`executions.db`, `.jobs.lock`, `output/` and **no `jobs.json`**.

That shape — the directory present, the file absent — is what a fresh instance
looks like to `registered_jobs()`, and it takes the `FileNotFoundError` branch.

**This measurement is load-bearing.** `register_crons.py` refuses to run when
`/opt/data/cron` is missing, on the grounds that a fresh instance still has it —
which is true only because of what is written above. A future hermes that
created the directory lazily on first save would turn that guard into a hard
abort on exactly the run that has everything to register, so re-measuring is the
trigger to revisit it: if a fresh home ever comes up *without* `cron/`, the
lower of the two pre-create checks has to go.
