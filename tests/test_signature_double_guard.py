from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from signature_double_guard import (
    read_allowlist,
    scan_test_double_mismatches,
    signature_mismatch,
)


_TESTS = Path(__file__).parent
_ALLOWLIST = _TESTS / "data" / "signature_double_allowlist.txt"


def _assert_allowlist_is_exact(findings, entries):
    finding_keys = set(findings)
    allowed_keys = set(entries)
    if finding_keys != allowed_keys:
        missing = sorted(finding_keys - allowed_keys)
        stale = sorted(allowed_keys - finding_keys)
        raise AssertionError(f"allowlist mismatch; missing={missing}, stale={stale}")


def _signature_for_all_calls(left, right=0, *, scale=1):
    return (left + right) * scale


def test_guard_catches_a_narrowed_lambda_mutation():
    narrowed_mutant = lambda left: left

    mismatch, skipped = signature_mismatch(_signature_for_all_calls, narrowed_mutant)

    assert not skipped
    assert mismatch is not None
    assert "right" in mismatch or "scale" in mismatch


def test_guard_accepts_a_variadic_double():
    def broad_double(*args, **kwargs):
        return args, kwargs

    mismatch, skipped = signature_mismatch(_signature_for_all_calls, broad_double)

    assert not skipped
    assert mismatch is None


def test_guard_ignores_builtins():
    mismatch, skipped = signature_mismatch(len, lambda value: value)

    assert not skipped
    assert mismatch is None


def test_class_method_receiver_is_bound_positionally():
    class Worker:
        def run(self, value):
            return value

    mismatch, skipped = signature_mismatch(
        Worker.run,
        lambda _worker, value: value,
        positional_receiver=True,
    )

    assert not skipped
    assert mismatch is None


def test_signature_double_allowlist_only_shrinks():
    findings, _skips = scan_test_double_mismatches(_TESTS)
    entries = read_allowlist(_ALLOWLIST)
    _assert_allowlist_is_exact(findings, entries)
    with pytest.raises(AssertionError, match="stale"):
        _assert_allowlist_is_exact(
            findings,
            {**entries, "tests/__mutation__.py::test_added::module.callable": "mutated addition"},
        )

    repo_root = _TESTS.parent
    relative_allowlist = _ALLOWLIST.relative_to(repo_root).as_posix()
    previous = subprocess.run(
        ["git", "show", f"HEAD^:{relative_allowlist}"],
        cwd=repo_root,
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
        assert set(entries) <= previous_keys, "signature allowlist entries may only shrink"
