from __future__ import annotations

import functools
import inspect
import subprocess
from functools import wraps
from pathlib import Path
from types import SimpleNamespace

import pytest

from signature_double_guard import (
    _cached_signature_mismatch,
    _cached_signature,
    _signature,
    allowlist_key_for_node,
    check_patch_signature,
    read_allowlist,
    signature_mismatch,
    signature_mismatch_for_signatures,
)

pytest_plugins = ("pytester",)


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


def test_signature_objects_are_cached_by_callable_and_role():
    def production(value):
        return value

    before = _cached_signature.cache_info()
    first = _signature(production)
    second = _signature(production)
    after = _cached_signature.cache_info()

    assert first is second
    assert after.misses == before.misses + 1
    assert after.hits == before.hits + 1


def test_signature_comparison_is_cached_by_signature_pair():
    production = inspect.signature(_all_keyword_calls)
    replacement = inspect.signature(lambda left: left)
    before = _cached_signature_mismatch.cache_info()

    first = signature_mismatch_for_signatures(production, replacement)
    second = signature_mismatch_for_signatures(production, replacement)
    after = _cached_signature_mismatch.cache_info()

    assert first == second
    assert after.misses == before.misses + 1
    assert after.hits == before.hits + 1


def test_collect_mode_nested_session_records_same_target_twice(
    pytester, monkeypatch, tmp_path
):
    collect_file = tmp_path / "collected-allowlist"
    repo_root = Path(__file__).resolve().parents[1]
    tests_dir = repo_root / "tests"
    monkeypatch.setenv("SIGNATURE_DOUBLE_GUARD_COLLECT", "1")
    monkeypatch.setenv("SIGNATURE_DOUBLE_GUARD_COLLECT_FILE", str(collect_file))
    monkeypatch.setenv("SIGNATURE_DOUBLE_GUARD_FAIL_UNLISTED", "0")
    pytester.makeconftest(
        "import importlib.util\n"
        "import sys\n"
        f"tests_dir = {str(tests_dir)!r}\n"
        "sys.path.insert(0, tests_dir)\n"
        "spec = importlib.util.spec_from_file_location(\n"
        "    '_signature_double_guard_plugin',\n"
        "    f'{tests_dir}/conftest.py',\n"
        ")\n"
        "plugin = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = plugin\n"
        "spec.loader.exec_module(plugin)\n"
        "pytest_plugins = (spec.name,)\n"
    )

    pytester.makepyfile(
        target="def operation(left, right=0):\n    return left + right\n",
        test_first=(
            "import target\n"
            "def test_first(monkeypatch):\n"
            "    monkeypatch.setattr(target, 'operation', lambda left: left)\n"
        ),
        test_second=(
            "import target\n"
            "def test_second(monkeypatch):\n"
            "    monkeypatch.setattr(target, 'operation', lambda left: left)\n"
        ),
    )

    result = pytester.runpytest_subprocess(
        "-q", "-n", "2", "-p", "no:cacheprovider", "test_first.py", "test_second.py",
        timeout=30,
    )

    assert result.ret == pytest.ExitCode.OK
    collected_files = list(tmp_path.glob("collected-allowlist.*"))
    entries = [
        entry
        for path in collected_files
        for entry in path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(entries) == 2
    assert any("test_first.py::test_first" in entry for entry in entries)
    assert any("test_second.py::test_second" in entry for entry in entries)


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
        target, "run", approved, nodeid, {}, collected, fail_unlisted=False
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
