from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from signature_double_guard import (
    allowlist_key_for_node,
    has_positional_method_receiver,
    read_allowlist,
    resolve_target_string,
    signature_mismatch,
)


_ALLOWLIST = Path(__file__).parent / "data" / "signature_double_allowlist.txt"
_ALLOWED_SIGNATURE_MISMATCHES = read_allowlist(_ALLOWLIST)


@pytest.fixture(autouse=True)
def monkeypatch_doubles_accept_production_signature(monkeypatch, request):
    """Reject test doubles that cannot accept calls valid for the patched function."""
    original_setattr = monkeypatch.setattr
    setter_signature = inspect.signature(original_setattr)
    allowed = _ALLOWED_SIGNATURE_MISMATCHES
    config = request.config
    config._signature_double_guard_skips = getattr(
        config, "_signature_double_guard_skips", 0
    )

    def guarded_setattr(*args: Any, **kwargs: Any) -> Any:
        bound = setter_signature.bind(*args, **kwargs)
        target = bound.arguments["target"]
        name = bound.arguments.get("name")
        replacement = bound.arguments.get("value")
        try:
            if isinstance(target, str) and "value" not in bound.arguments:
                replacement = name
                target, name, production = resolve_target_string(target)
                positional_receiver = has_positional_method_receiver(target, name)
            else:
                production = getattr(target, name)
                positional_receiver = has_positional_method_receiver(target, name)
        except (AttributeError, ImportError, LookupError, ModuleNotFoundError):
            return original_setattr(*args, **kwargs)

        mismatch, skipped = signature_mismatch(
            production,
            replacement,
            positional_receiver=positional_receiver,
        )
        if skipped:
            config._signature_double_guard_skips += 1
        elif mismatch:
            key = allowlist_key_for_node(request.node.nodeid, production)
            if key not in allowed:
                raise AssertionError(
                    f"test double for {key} is incompatible with the production signature: "
                    f"{mismatch}"
                )
        return original_setattr(*args, **kwargs)

    monkeypatch.setattr = guarded_setattr
    yield


def pytest_sessionfinish(session, exitstatus):
    skipped = getattr(session.config, "_signature_double_guard_skips", 0)
    session.config.pluginmanager.get_plugin("terminalreporter").write_line(
        f"signature double guard: {skipped} unavailable signature lookup(s) skipped"
    )
