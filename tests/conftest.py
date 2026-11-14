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
_COLLECTED_SIGNATURE_MISMATCHES: dict[str, str] = {}


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
    if reporter is not None:
        reporter.write_line(
            "signature double guard: "
            f"{_SIGNATURE_LOOKUP_SKIPS} unavailable signature lookup(s) skipped"
        )
        if _COLLECT_SIGNATURES:
            reporter.write_line("signature double guard collected allowlist entries:")
            for key, reason in sorted(_COLLECTED_SIGNATURE_MISMATCHES.items()):
                reporter.write_line(f"{key} # {reason}")
