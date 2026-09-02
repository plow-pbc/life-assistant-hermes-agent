"""Fixtures shared across this agent's contract tests."""
import threading

import pytest


@pytest.fixture
def run_concurrently():
    """Run callables from a common barrier; return whatever they raised.

    The barrier is the test, not decoration. Threads started one after another
    usually finish one after another, so a race only shows under real overlap --
    and a race that does not show is one a suite passes every time while the bug
    ships. Both writers here are read-modify-write, and the losses they produce
    are clean files that are quietly wrong: a config missing an answer the owner
    gave, or a Pi and a Mac holding different bearers.

    Failures are collected rather than raised in a worker thread, where pytest
    would never see them; the caller asserts on the returned list.
    """
    def run(*calls):
        start = threading.Barrier(len(calls))
        errors = []

        def wrap(call):
            def body():
                try:
                    start.wait(timeout=5)
                    call()
                except BaseException as exc:    # noqa: BLE001 - returned to the caller
                    errors.append(exc)
            return threading.Thread(target=body)

        threads = [wrap(call) for call in calls]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
            assert not thread.is_alive(), "a writer never finished -- the lock deadlocked"
        return errors

    return run
