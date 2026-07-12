"""Canonical naming for the analytical (builtin) melt backend.

Trust-architecture vocabulary names the analytical, non-real-engine chemistry
model ``internal-analytical``. The 0.6 corpus migration makes that name the
serialization identity while retaining the old spellings as read-side aliases.

This module deliberately has no heavy dependencies so it can be imported from
the EvalSpec cache-key path without pulling in ``simulator.core``.
"""

from __future__ import annotations


ANALYTICAL_BACKEND_SERIALIZATION_TOKEN = "internal-analytical"
ANALYTICAL_BACKEND_DISPLAY_NAME = ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
LEGACY_ANALYTICAL_BACKEND_SERIALIZATION_TOKEN = "stub"
LEGACY_ANALYTICAL_BACKEND_DIAGNOSTIC_TOKEN = "diagnostic_stub"
ANALYTICAL_BACKEND_ALIASES = frozenset(
    {
        LEGACY_ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        "internal_analytical",
        LEGACY_ANALYTICAL_BACKEND_DIAGNOSTIC_TOKEN,
    }
)

ANALYTICAL_BACKEND_CLASS_DISPLAY_NAME = "InternalAnalyticalBackend"
ANALYTICAL_BACKEND_QUALIFIED_CLASS_NAME = (
    "simulator.melt_backend.base.InternalAnalyticalBackend"
)
LEGACY_ANALYTICAL_BACKEND_CLASS_DISPLAY_NAMES = frozenset({"StubBackend"})
LEGACY_ANALYTICAL_FIDELITY_DIAGNOSTIC_ENV = "FIDELITY_DIAGNOSTIC_STUB_HIGH"


def canonical_backend_name(backend_name: str | None) -> str | None:
    """Accept legacy analytical names and emit ``internal-analytical``.

    The canonical token and legacy aliases are matched case-insensitively after
    trimming. Every other value is returned byte-for-byte, preserving strict
    matching for real backends and unknown-name refusals. ``None`` is unchanged.
    """
    if backend_name is None:
        return None
    normalized = str(backend_name).strip().lower()
    if (
        normalized == ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
        or normalized in ANALYTICAL_BACKEND_ALIASES
    ):
        return ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
    return backend_name


def canonical_backend_class_name(class_name: str | None) -> str | None:
    """Accept the legacy analytical class label and emit the 0.6 label."""
    if class_name is None:
        return None
    raw = str(class_name)
    leaf = raw.strip().split(".")[-1]
    if leaf in LEGACY_ANALYTICAL_BACKEND_CLASS_DISPLAY_NAMES or leaf == (
        ANALYTICAL_BACKEND_CLASS_DISPLAY_NAME
    ):
        if "." in raw:
            return ANALYTICAL_BACKEND_QUALIFIED_CLASS_NAME
        return ANALYTICAL_BACKEND_CLASS_DISPLAY_NAME
    return class_name
