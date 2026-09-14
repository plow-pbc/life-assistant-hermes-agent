#!/usr/bin/env python3
"""atomic_write.py -- publish a file by rename, never by truncate-then-write.

Two writers share this: write_config.py (config.json) and priorities.py
(priorities.json, and the composed tile at priorities-text). Both are
read-modify-write under exclusive_lock, and both would corrupt a reader's
view the same way without this -- a truncate-then-write destroys the file
before the replacement exists, so ENOSPC or a kill in that window leaves an
empty or half-written file. An unreadable config also stands every producer
down at once (the shared gate refuses it), so the failure presents as a wall
that quietly stops updating; an unreadable priorities-text posts a truncated
tile to the kiosk.

So: a fresh file in the same directory (same filesystem, or os.replace is not
atomic), chmod BEFORE the content goes in (config.json and priorities.json
both carry a person's data), fsync so the bytes are durable before the rename
publishes them, then one os.replace. A reader sees the old file or the new
one, never neither.
"""
from __future__ import annotations

import contextlib
import os
import tempfile


def atomic_write(path: str, text: str) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
