#!/usr/bin/env python3
"""exclusive_lock.py — a single-writer lock for an ld- producer with local state.

Every other ld- producer is stateless between runs: compose a tile, POST it,
done. ld-priorities is the first to keep a manifest on disk that a chat turn
reads, mutates and writes back — two turns racing that read-modify-write would
each read the pre-mutation manifest, and the second save() would silently
discard the first's edit. `exclusive_lock()` turns that race into a queue.

An flock(2) on a sibling `<path>.lock` file, held only around the
load/mutate/save section (see priorities.py's `_mutate`), never around the
whole process. The lock file is created once, mode 600, and never removed:
flock does not care whether the path still has content, and removing it would
open a window where a second process creates a fresh lock file and locks THAT
inode while the first still holds the original one.

    with exclusive_lock(MANIFEST, "refusing to write"):
        ...read, mutate, save...
"""
from __future__ import annotations

import contextlib
import fcntl
import os


@contextlib.contextmanager
def exclusive_lock(path: str, refusal: str):
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(lock_path), mode=0o700, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError as exc:
            raise SystemExit(f"{refusal}: {exc}") from exc
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
