"""Shared urllib transport for bearer requests that must never redirect."""
from __future__ import annotations

import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def open_no_redirect(request: urllib.request.Request, *, timeout: int):
    """Open one request while refusing every redirect before forwarding auth."""
    return urllib.request.build_opener(NoRedirect).open(request, timeout=timeout)
