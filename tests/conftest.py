"""Fixtures shared by this repo's own suite under tests/ -- not the vendored ld-
suites elsewhere in the tree, which run as subprocesses precisely so they stay
out of pytest's collection (see test_vendored_suites.py)."""
import threading

import pytest


@pytest.fixture
def run_concurrently():
    """Run each zero-arg callable in its own thread, at the same time, and
    return the list of exceptions any of them raised (empty == all clean).
    Used to prove a read-modify-write serializes under a lock instead of
    racing."""

    def go(*fns):
        errors = []
        lock = threading.Lock()

        def call(fn):
            try:
                fn()
            except BaseException as exc:  # noqa: BLE001 - collected for the caller to assert on
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=call, args=(fn,)) for fn in fns]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return errors

    return go
