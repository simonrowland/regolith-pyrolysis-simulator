"""The kernel SHA must identify the code running, not the working tree.

Regression, observed 2026-08-28: the SHA was resolved with `git rev-parse HEAD`
on every run, inside a server process that had loaded its code 14 hours earlier.
Every run after the first was stamped with whatever HEAD had advanced to since.

The damage is not a wrong label. Two runs with byte-identical inputs produced
FeO differing by 4%, and their only recorded difference was this field -- which
pointed at a commit range containing nothing but test files. A ledger that
manufactures an innocent explanation for a real divergence is worse than one
that records nothing, because it stops the investigation.
"""

from __future__ import annotations

import simulator.runner as runner


def _fresh_resolver():
    """Clear the per-process memo so each test observes a cold start.

    ``cache_clear`` is accessed defensively ON PURPOSE. Without it, removing
    the memo makes these tests die with AttributeError -- a NameError-class
    failure that would pass as "the test caught it" while proving only that
    the decorator exists. The reverted build has to stay VALID so the failure
    lands on the property being asserted.
    """
    clear = getattr(runner._resolve_kernel_commit_sha, "cache_clear", None)
    if clear is not None:
        clear()
    return runner._resolve_kernel_commit_sha


def test_sha_does_not_follow_a_moving_head(monkeypatch):
    """The whole point: HEAD moves under a live process; the stamp must not."""
    resolve = _fresh_resolver()
    shas = iter(["a" * 40, "b" * 40, "c" * 40])

    class _Result:
        returncode = 0

        def __init__(self, out: str) -> None:
            self.stdout = out

    monkeypatch.setattr(
        runner.subprocess, "run", lambda *a, **k: _Result(next(shas) + "\n")
    )

    first = resolve()
    second = resolve()
    third = resolve()
    assert first == "a" * 40
    assert second == first, "stamp followed HEAD to a commit this process never loaded"
    assert third == first


def test_git_is_consulted_once_per_process(monkeypatch):
    """Anti-vacuity: a resolver that never called git would also pass above."""
    resolve = _fresh_resolver()
    calls = []

    class _Result:
        returncode = 0
        stdout = "d" * 40

    def _run(*args, **kwargs):
        calls.append(args)
        return _Result()

    monkeypatch.setattr(runner.subprocess, "run", _run)
    for _ in range(5):
        resolve()
    assert len(calls) == 1, f"expected one git call per process, got {len(calls)}"


def test_unreachable_git_still_returns_unknown(monkeypatch):
    """Absence stays absence: no SHA is 'unknown', never a fabricated value."""
    resolve = _fresh_resolver()

    def _boom(*args, **kwargs):
        raise OSError("git not found")

    monkeypatch.setattr(runner.subprocess, "run", _boom)
    assert resolve() == "unknown"
