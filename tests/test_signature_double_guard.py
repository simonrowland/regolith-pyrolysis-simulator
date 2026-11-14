from __future__ import annotations

import functools
import subprocess
from functools import wraps
from pathlib import Path
from types import SimpleNamespace

import pytest
from _pytest.monkeypatch import MonkeyPatch

from signature_double_guard import (
    allowlist_key_for_node,
    check_patch_signature,
    read_allowlist,
    signature_mismatch,
)


_TESTS = Path(__file__).parent
_ALLOWLIST = _TESTS / "data" / "signature_double_allowlist.txt"


def _base_allowlist_key(key: str) -> str:
    base, separator, signature = key.rpartition("::")
    return base if separator and signature.startswith("[") else key


def _assert_allowlist_only_shrinks(entries, previous_keys):
    pinned_parent = any(
        key.rpartition("::")[2].startswith("[") for key in previous_keys
    )
    if pinned_parent:
        remaining = set(entries) <= previous_keys
    else:
        remaining = {
            _base_allowlist_key(key) for key in entries
        } <= previous_keys
    if not remaining:
        raise AssertionError("signature allowlist entries may only shrink")


def _all_keyword_calls(left, right=0, *, scale=1):
    return (left + right) * scale


def test_guard_catches_a_narrowed_lambda_mutation():
    mismatch, skipped = signature_mismatch(
        _all_keyword_calls, lambda left: left
    )

    assert not skipped
    assert mismatch is not None
    assert "right" in mismatch or "scale" in mismatch


def test_guard_accepts_kwargs_doubles():
    def broad_double(*args, **kwargs):
        return args, kwargs

    mismatch, skipped = signature_mismatch(_all_keyword_calls, broad_double)

    assert not skipped
    assert mismatch is None


def test_guard_ignores_builtins():
    mismatch, skipped = signature_mismatch(len, lambda value: value)

    assert not skipped
    assert mismatch is None


def test_instance_patch_uses_the_bound_production_signature(monkeypatch):
    class Worker:
        def run(self, value):
            return value

    worker = Worker()
    monkeypatch.setattr(worker, "run", lambda value: value)

    assert worker.run("ok") == "ok"


def test_guard_covers_fixture_context_and_fresh_monkeypatch_instances(monkeypatch):
    class Worker:
        def run(self, left, right=0):
            return left + right

    worker = Worker()
    narrowed = lambda left: left

    with pytest.raises(AssertionError, match="incompatible"):
        monkeypatch.setattr(worker, "run", narrowed)

    with monkeypatch.context() as scoped:
        with pytest.raises(AssertionError, match="incompatible"):
            scoped.setattr(worker, "run", narrowed)

    fresh = pytest.MonkeyPatch()
    try:
        with pytest.raises(AssertionError, match="incompatible"):
            fresh.setattr(worker, "run", narrowed)
    finally:
        fresh.undo()


def test_wrapped_replacement_uses_its_own_signature(monkeypatch):
    def production(left, right=0):
        return left + right

    @wraps(production)
    def narrowed(left):
        return left

    target = SimpleNamespace(run=production)
    with pytest.raises(AssertionError, match="incompatible"):
        monkeypatch.setattr(target, "run", narrowed)


def test_classmethod_replacement_uses_descriptor_binding(monkeypatch):
    class Worker:
        @classmethod
        def run(cls, left, right=0):
            return left + right

    replacement = classmethod(lambda cls, left: left)

    with pytest.raises(AssertionError, match="incompatible"):
        monkeypatch.setattr(Worker, "run", replacement)


def test_partial_production_callable_is_checked(monkeypatch):
    def production(left, right=0):
        return left + right

    target = SimpleNamespace(run=functools.partial(production, 1))

    with pytest.raises(AssertionError, match="incompatible"):
        monkeypatch.setattr(target, "run", lambda: 0)


def test_variadic_keyword_collision_is_checked(monkeypatch):
    def production(*args, **kwargs):
        return args, kwargs

    def collision(value=0, *args, **kwargs):
        return value, args, kwargs

    target = SimpleNamespace(run=production)

    with pytest.raises(AssertionError, match="multiple values"):
        monkeypatch.setattr(target, "run", collision)


def test_waiver_covers_only_its_exact_replacement_signature():
    def production(left, right=0):
        return left + right

    approved = lambda left: left
    narrowed_again = lambda: 0
    target = SimpleNamespace(run=production)
    nodeid = "tests/test_signature_double_guard.py::test_waiver_probe"
    approved_key = allowlist_key_for_node(nodeid, production, approved)
    allowed = {approved_key: "existing mismatch"}
    collected = {}

    assert not check_patch_signature(
        target, "run", approved, nodeid, {}, collected
    )
    assert approved_key in collected
    assert not check_patch_signature(
        target, "run", approved, nodeid, allowed
    )
    assert allowlist_key_for_node(nodeid, production, narrowed_again) != approved_key
    with pytest.raises(AssertionError, match="incompatible"):
        check_patch_signature(target, "run", narrowed_again, nodeid, allowed)


def test_signature_double_allowlist_only_shrinks():
    entries = read_allowlist(_ALLOWLIST)
    relative_allowlist = _ALLOWLIST.relative_to(_TESTS.parent).as_posix()
    previous = subprocess.run(
        ["git", "show", f"HEAD^:{relative_allowlist}"],
        cwd=_TESTS.parent,
        check=False,
        capture_output=True,
        text=True,
    )
    if previous.returncode == 0:
        previous_keys = {
            line.partition(" # ")[0]
            for line in previous.stdout.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        _assert_allowlist_only_shrinks(entries, previous_keys)
        with pytest.raises(AssertionError, match="only shrink"):
            _assert_allowlist_only_shrinks(
                {
                    **entries,
                    "tests/__mutation__.py::test_added::module.callable::[]": "mutation",
                },
                previous_keys,
            )
