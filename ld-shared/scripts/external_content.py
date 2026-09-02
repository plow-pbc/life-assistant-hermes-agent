"""gog's untrusted-content fence, in one place.

gog 0.36 (openclaw/gogcli internal/outfmt/untrusted.go) wraps every free-text
field it fetched from Google as

    <<<EXTERNAL_UNTRUSTED_CONTENT id="x">>>
    Source: google_api
    ---
    <value>
    <<<END_EXTERNAL_UNTRUSTED_CONTENT id="x">>>

Three consumers met that independently and each wrote the pattern out again:
the nudge, the kiosk's calendar strip, and the calendar listing. The grammar
belongs to gog, so a change there is one edit and not a hunt -- and a copy that
drifts is a marker reaching a display, which is what the `Source: google_api
---` on a live card was (2026-08-28). Hence one module.

The open marker is never matched alone. It carries the metadata line and the
`---` rule with it, and stripping the `<<<...>>>` by itself leaves
`Source: google_api ---` on the surface -- the card that proved it.

TWO operations, because the callers want different things and neither is the
other's special case:

  * `strip_markers` removes fences from anywhere in a string. Free text can
    carry several, and what the caller wants is the prose without them.
  * `unwrap_external` unwraps a value that is EXACTLY one fence, and returns
    anything else untouched. A calendar's display name is one field with one
    name in it, and the fencing across a listing is not uniform -- a fenced
    `summary` sits beside a bare `summaryOverride` -- so "all of it or none of
    it" is the only reading that normalises the mix consistently.

Neither is a safety boundary. The text inside the fence is the same
attacker-controlled text it was outside; the markers labelled that, and taking
them off takes off the label and not the risk.
"""
from __future__ import annotations

import re

# One grammar, both operations. `Source: google_api` is matched literally
# because that is what every observed fence carries -- a fence this does not
# recognise is left visible, which is the honest failure.
_OPEN = r'<<<EXTERNAL_UNTRUSTED_CONTENT id="[^"]*">>>\nSource: google_api\n---\n'
_CLOSE = r'<<<END_EXTERNAL_UNTRUSTED_CONTENT id="[^"]*">>>'

MARKERS = re.compile(_OPEN + "|" + _CLOSE)
# `\A`/`\Z` are the whole point: one entire fence, not a fence found inside
# something longer.
_WHOLE = re.compile(r"\A" + _OPEN + r"(?P<body>.*)\n" + _CLOSE + r"\Z", re.DOTALL)


def strip_markers(text):
    """`text` with every fence marker removed. Non-strings come back "" ."""
    if not isinstance(text, str):
        return ""
    return MARKERS.sub("", text)


def unwrap_external(text):
    """The content of a value that is exactly one fence, else `text` itself.

    `.*` is greedy under DOTALL, so a body carrying its own end-marker keeps
    it: the outermost fence is the real one, and the forgery stays visible as
    text rather than truncating the value at somebody else's say-so.
    """
    if not isinstance(text, str):
        return text
    match = _WHOLE.match(text)
    return match.group("body") if match else text
