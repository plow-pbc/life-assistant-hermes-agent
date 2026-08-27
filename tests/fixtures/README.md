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
