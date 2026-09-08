---
name: ld-shared
description: The helper library every ld-* producer imports — the kiosk POST helper, the ld-config gate, the wire protocol and the calendar strip's feed. Not a task; nothing here is invoked on its own. Read the references when a producer's SKILL.md points at one.
---

# ld-shared — the producers' shared helpers

Every producer's scripts reach this directory as a sibling —
`../../ld-shared/scripts` off their own realpath — so it has to land beside
them in `$HERMES_HOME/skills`. The base's boot reconcile copies a bundled
directory into the home only when it carries a `SKILL.md`; this file is what
makes `ld-shared` one of them. Without it the producers seed and this does
not, and every run fails on the import.

- `scripts/post_to_kiosk.py` — the POST helper (`references/kiosk-protocol.md`)
- `scripts/ld_config_gate.py` — the single definition of a valid `ld/config.json`
- `scripts/calendar_feed.py` — the kiosk's calendar strip, no model in it
- `references/latch-delivery.md` — how a card reaches the Mac over Latch
