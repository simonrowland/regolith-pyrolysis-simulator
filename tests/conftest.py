from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any

import pytest
from _pytest.monkeypatch import MonkeyPatch

from signature_double_guard import (
    check_patch_signature,
    read_allowlist,
    resolve_target_string,
)


_ALLOWLIST = Path(__file__).parent / "data" / "signature_double_allowlist.txt"
_ALLOWED_SIGNATURE_MISMATCHES = read_allowlist(_ALLOWLIST)
_ORIGINAL_SETATTR = MonkeyPatch.setattr
_SETATTR_SIGNATURE = inspect.signature(_ORIGINAL_SETATTR)
_CURRENT_NODEID: str | None = None
_SIGNATURE_LOOKUP_SKIPS = 0
_COLLECT_SIGNATURES = os.environ.get("SIGNATURE_DOUBLE_GUARD_COLLECT") == "1"
_COLLECT_FILE = os.environ.get("SIGNATURE_DOUBLE_GUARD_COLLECT_FILE")
_COLLECTED_SIGNATURE_MISMATCHES: dict[str, str] = {}
_FAIL_UNLISTED_VALUE = os.environ.get("SIGNATURE_DOUBLE_GUARD_FAIL_UNLISTED")
if _FAIL_UNLISTED_VALUE is None:
    _FAIL_UNLISTED = not _COLLECT_SIGNATURES
elif _FAIL_UNLISTED_VALUE in {"0", "1"}:
    _FAIL_UNLISTED = _FAIL_UNLISTED_VALUE == "1"
else:
    raise RuntimeError("SIGNATURE_DOUBLE_GUARD_FAIL_UNLISTED must be 0 or 1")

if _COLLECT_SIGNATURES and not _COLLECT_FILE:
    raise RuntimeError(
        "SIGNATURE_DOUBLE_GUARD_COLLECT_FILE is required when collecting signatures"
    )


def _guarded_setattr(self: MonkeyPatch, *args: Any, **kwargs: Any) -> Any:
    global _SIGNATURE_LOOKUP_SKIPS
    bound = _SETATTR_SIGNATURE.bind(self, *args, **kwargs)
    if _CURRENT_NODEID is not None:
        target = bound.arguments["target"]
        name = bound.arguments.get("name")
        replacement = bound.arguments.get("value")
        try:
            if isinstance(target, str) and "value" not in bound.arguments:
                replacement = name
                target, name = resolve_target_string(target)
            elif not isinstance(name, str):
                target = None
            if target is not None:
                skipped = check_patch_signature(
                    target,
                    name,
                    replacement,
                    _CURRENT_NODEID,
                    _ALLOWED_SIGNATURE_MISMATCHES,
                    _COLLECTED_SIGNATURE_MISMATCHES
                    if _COLLECT_SIGNATURES
                    else None,
                    _FAIL_UNLISTED,
                )
                if skipped:
                    _SIGNATURE_LOOKUP_SKIPS += 1
        except (AttributeError, ImportError, LookupError, ModuleNotFoundError):
            pass
    return _ORIGINAL_SETATTR(self, *args, **kwargs)


# Patching the class covers the injected fixture, context() objects, and fresh
# MonkeyPatch instances with the same production-signature rule.
MonkeyPatch.setattr = _guarded_setattr


@pytest.fixture(autouse=True)
def signature_guard_test_context(request):
    global _CURRENT_NODEID
    previous = _CURRENT_NODEID
    _CURRENT_NODEID = request.node.nodeid
    try:
        yield
    finally:
        _CURRENT_NODEID = previous


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if _COLLECT_SIGNATURES:
        collected_path = Path(f"{_COLLECT_FILE}.{os.getpid()}")
        collected_path.parent.mkdir(parents=True, exist_ok=True)
        with collected_path.open("a", encoding="utf-8") as collected_file:
            for key, reason in sorted(_COLLECTED_SIGNATURE_MISMATCHES.items()):
                collected_file.write(f"{key} # {reason}\n")
    if reporter is not None:
        reporter.write_line(
            "signature double guard: "
            f"{_SIGNATURE_LOOKUP_SKIPS} unavailable signature lookup(s) skipped"
        )
        if _COLLECT_SIGNATURES:
            reporter.write_line(
                "signature double guard collected "
                f"{len(_COLLECTED_SIGNATURE_MISMATCHES)} entries to "
                f"{_COLLECT_FILE}.{os.getpid()}"
            )
