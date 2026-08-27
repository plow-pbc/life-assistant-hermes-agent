# Kiosk wire protocol + tile contract

The single source of truth for **what every ld- producer posts to the kiosk**,
on any platform. Both the Plow agent seed (deterministic JS runners + Python
wrappers) and the Hermes agent seed (LLM producers + Python wrappers) MUST emit
exactly this. The `seed-life-dashboard-viewer` kiosk is the consumer; this
contract is what keeps producers and viewer in lockstep.

## The wire request

Every producer POSTs ONE message to the household's Pi message API:

    POST <DASHBOARD_ENDPOINT_URL>          # the full .../api/message URL, verbatim
    Authorization: Bearer <DASHBOARD_TOKEN>
    Content-Type: application/json

    { "card": "<1-5>", "type": "<type>", "text": "<body>", "title": "<optional>" }

- `card`, `type`, `text` are REQUIRED. `title` is OPTIONAL.
- The store is **latest-post-per-card-wins**: re-posting a card replaces it.
- `title` is the card eyebrow. Omit it to default the eyebrow to `type`; send
  `""` to HIDE the eyebrow (the producer owns the full card height); send a
  string to override it.
- `text` is either a short plain-text line (alert / affirmation / digest) or a
  self-contained HTML tile (weather / sports — see below).
- The bearer flows from a fixed secret source, never argv; redirects are
  refused so a 30x can't forward the Authorization header. The shared
  `scripts/post_to_kiosk.py` helper enforces both — producers post through it.

## Card map

| card | type | producer | body |
|---|---|---|---|
| 1 | `alert` | ld-morning-triage (and ld-calendar-nudge reminders) | plain text, ≤115 chars |
| 2 | `affirmation` | ld-morning-updates | plain text, ≤115 chars |
| 3 | `weather` | ld-weather | self-contained HTML tile |
| 4 | `digest` | ld-weekly-digest | plain text, ≤115 chars |
| 5 | `sports` | ld-sports | self-contained HTML tile |

Card 1 is shared: a calendar nudge and the morning triage alert both land in
the alert slot; latest-per-card means the newest of the two shows.

## Plain-text cards (1, 2, 4)

`text` is a short paraphrased line, **≤115 chars** to match the kiosk's visible
budget. Producers paraphrase private mail/iMessage/Slack content — they never
quote it verbatim, and `--dry-run` always redacts the body to
`<redacted, N chars>` so agent-visible stdout stays non-sensitive.

## HTML tile cards (3 weather, 5 sports)

`text` is a **self-contained HTML fragment** the viewer renders verbatim
(`dangerouslySetInnerHTML`). The tile ships its OWN `<style>`, so the viewer
holds zero per-card CSS — it is a dumb HTML sink. This makes an HTML-capable
`seed-life-dashboard-viewer` (the generic box-renderer, viewer PR #40) a
required runtime; against an older viewer the card shows literal tags.

The tile `<style>` references ONLY the viewer's shared theme tokens (`--ink`,
`--muted`, `--faint`, `--hair`, the `--ff-*` fonts, the `--cap-*` caption type
tokens, `--live-red`, accent inks) — that token set is the one contract between
tile and theme. A producer MUST NOT invent new global CSS the viewer would have
to carry.

Whatever generates the tile (Plow's `scheduled/compose.js`, or a Hermes LLM
producer following its SKILL.md) MUST emit the markup + `<style>` below. This
file is canonical; a tile change lands here and propagates to both platforms.

### Weather tile (card 3)

A big current temp + condition, with location and the day's H/L beneath. Text
fields (location, condition) are HTML-escaped. A blank location renders an
empty meta slot (no stray separator).

```html
<style>
.weather{display:flex;flex-direction:column;gap:0.4rem;width:100%;min-height:0}
.weather-now{display:flex;align-items:baseline;gap:0.75rem}
.weather-temp{font-family:var(--ff-display);font-weight:400;font-size:2.4em;letter-spacing:-0.04em;line-height:0.82;color:var(--ink);font-variant-numeric:tabular-nums}
.weather-cond{font-family:var(--ff-body);font-weight:300;font-size:0.85em;color:var(--ink)}
.weather-meta{display:flex;justify-content:space-between;font-family:var(--ff-mono);font-weight:var(--cap-weight);font-size:var(--cap-size);letter-spacing:var(--cap-tracking);text-transform:uppercase;color:var(--faint)}
</style>
<div class="weather"><div class="weather-now"><span class="weather-temp">72°</span><span class="weather-cond">Sunny</span></div><div class="weather-meta"><span>Mountain View</span><span>H77 · L55</span></div></div>
```

### Sports tile (card 5)

A stacked list of up to 3 game rows (Apple-Sports look): away (left) · center ·
home (right); loser greyed; `is-live` warms the background when a shown game is
live; empty window → "No upcoming games" (still posted, so the card refreshes
rather than going stale). Per-team monogram colors are set inline via `--p` /
`--s`. Text fields (team abbrs, status, logo URLs) are HTML-escaped.

```html
<style>
.sp-list{flex:1;min-height:0;display:flex;flex-direction:column;justify-content:center}
.sp-list.is-live{background:linear-gradient(180deg,#fffdfa 0%,#fdf3ec 100%);border-radius:16px}
.sp-empty{text-align:center;color:var(--muted);font-size:var(--t-card)}
.sp-game{display:grid;grid-template-columns:14px 38px 30px 1fr 30px 38px 14px;align-items:center;column-gap:6px;padding:12px 0}
.sp-game + .sp-game{border-top:1px solid var(--hair)}
.sp-star{color:var(--accent-ink,var(--clay-ink));font-size:12px;text-align:center;line-height:1}
.sp-logo{width:38px;height:38px;position:relative;display:flex;align-items:center;justify-content:center}
.sp-logo img{width:100%;height:100%;object-fit:contain;display:block}
.sp-mono{display:flex;align-items:center;justify-content:center;width:38px;height:38px;border-radius:50%;background:var(--p,var(--muted));color:var(--s,#fff);font-family:var(--ff-mono);font-weight:500;font-size:12px;letter-spacing:0.02em}
.sp-sc{font-family:var(--ff-body);font-weight:700;font-size:24px;line-height:1;font-variant-numeric:tabular-nums;color:var(--ink)}
.sp-sc.a{text-align:right}
.sp-sc.h{text-align:left}
.sp-sc.lose{color:var(--faint);font-weight:500}
.sp-ctr{display:flex;flex-direction:column;align-items:center;justify-content:center;line-height:1.1;min-width:0}
.sp-time{font-family:var(--ff-body);font-weight:600;font-size:18px;color:var(--ink);white-space:nowrap}
.sp-day{font-family:var(--ff-mono);font-weight:500;font-size:11px;letter-spacing:0.06em;text-transform:uppercase;color:var(--faint)}
.sp-per{display:flex;align-items:center;gap:5px;font-family:var(--ff-mono);font-weight:500;font-size:13px;letter-spacing:0.03em;color:var(--accent-ink,var(--clay-ink));white-space:nowrap}
.sp-livedot{width:7px;height:7px;border-radius:50%;background:var(--live-red);display:inline-block}
.sp-fin{font-family:var(--ff-mono);font-weight:500;font-size:13px;letter-spacing:0.08em;text-transform:uppercase;color:var(--muted)}
</style>
```

The row markup (`.sp-game` → away `.sp-logo`/`.sp-sc` · `.sp-ctr` · home) is
produced by the platform's composer; the grid + class contract above is what
the viewer's theme tokens style. Keep the column grid (`14px 38px 30px 1fr 30px
38px 14px`) and class names stable — they ARE the contract.
