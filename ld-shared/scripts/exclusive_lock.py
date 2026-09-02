#!/usr/bin/env python3
"""exclusive_lock.py -- one writer at a time, for the files a turn rewrites.

`write_config.py` merges answers onto `config.json`; `mint_wall_token.py` reads
the dotenv to decide whether a bearer already exists and then writes two private
files. Both are read-modify-write, and two turns can run at once -- an owner
texting while a cron producer works, an answer arriving while a retry is in
flight. Unlocked, the second read happens before the first write lands:
config.json loses an answer the owner already gave, or the Pi and the Mac end up
holding different bearers. In neither case is the result corrupt. It is a clean,
valid file that is quietly wrong, which is the kind nobody traces back.

A SEPARATE lock file, never the file being protected. Both writers publish by
rename (write a temporary, `os.replace` it into place), so a descriptor held on
the target is a lock on a path that stops existing the moment the first writer
finishes -- the second writer would take a fresh lock on the new inode and both
would proceed. `<path>.lock` outlives every rename.

Advisory `flock` is enough because every writer of these files comes through
here. FAIL CLOSED: a write that could not take the lock is the exact loss the
lock exists to prevent, so it refuses rather than proceeding. The directory is
created first -- on a household's FIRST write it does not exist yet, and two
writers racing to create it are two writers with no lock between them.
"""
from __future__ import annotations

import contextlib
import fcntl
import os


@contextlib.contextmanager
def exclusive_lock(path, refusal="refusing"):
    """Hold `<path>.lock` for the body. `refusal` prefixes the error message."""
    lock_path = path + ".lock"
    handle = None
    try:
        parent = os.path.dirname(lock_path)
        if parent:
            os.makedirs(parent, mode=0o700, exist_ok=True)
        handle = open(lock_path, "a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except OSError as exc:
        if handle is not None:
            handle.close()
        raise SystemExit(
            f"{refusal}: could not take the lock on {path} ({exc}). Another "
            "write may be in progress; nothing was changed.") from None
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
