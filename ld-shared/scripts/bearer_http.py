"""One bearer JSON call, shared by every skill that reads the Plow API.

The redirect refusal is the reason this is a module and not three lines in each
caller: a bearer header forwarded to wherever a redirect points hands the
instance's credential to somewhere the API did not authenticate.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

TIMEOUT = 30


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def open_no_redirect(request: urllib.request.Request, *, timeout: int):
    """Open one request while refusing every redirect before forwarding auth."""
    return urllib.request.build_opener(NoRedirect).open(request, timeout=timeout)


def require(name):
    """Refuse by name -- a blank credential must say which one is blank."""
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"error: {name} is not set")
    return value


def request_json(method, base, path, token, label, body=None):
    """One bearer JSON call that never follows a redirect; exits loudly, by
    label, on any failure, so a failed call never reads like an empty answer."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url=f"{base.rstrip('/')}{path}", method=method, data=data, headers=headers)
    try:
        with open_no_redirect(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        sys.exit(f"error: {label} returned HTTP {exc.code} {exc.reason}")
    except urllib.error.URLError as exc:
        sys.exit(f"error: {label} failed: {exc.reason}")


def get_json(base, path, token, label):
    return request_json("GET", base, path, token, label)
