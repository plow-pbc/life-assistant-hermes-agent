# Captured formats

Files here are **real output from a real system**, captured once and committed so
the tests pin what the tool actually writes rather than what this repo assumes it
writes. Every value that could carry private content is `<scrubbed>`; the field
*names* are the contract and are verbatim.

| file | captured from | version |
|---|---|---|
| `hermes-cron-jobs.json` | `/var/lib/hermes/cron/jobs.json` on a running agent | Hermes Agent v0.19.0 (2026.7.20), upstream `b4f8c491` |

Re-capture with:

    docker exec <container> cat /var/lib/hermes/cron/jobs.json   # then scrub values

## Measured, not captured

`/var/lib/hermes/cron/` exists before any cron job does — the gateway creates it
at start, not lazily on first save. Checked on two homes that never had a single
job: both carried `cron/` with `executions.db`, `.jobs.lock`, `output/` and
**no `jobs.json`**.

That shape — the directory present, the file absent — is what a fresh instance
looks like to `registered_jobs()`, and it takes the `FileNotFoundError` branch.

**This measurement is load-bearing.** `register_crons.py` refuses to run when
`/var/lib/hermes/cron` is missing, on the grounds that a fresh instance still has it —
which is true only because of what is written above. A future hermes that
created the directory lazily on first save would turn that guard into a hard
abort on exactly the run that has everything to register, so re-measuring is the
trigger to revisit it: if a fresh home ever comes up *without* `cron/`, the
lower of the two pre-create checks has to go.
