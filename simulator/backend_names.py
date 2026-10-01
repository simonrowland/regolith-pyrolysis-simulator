"""Canonical naming for the analytical (builtin) melt backend.

Trust-architecture vocabulary names the analytical, non-real-engine chemistry
model ``internal-analytical``. The 0.6 corpus migration makes that name the
serialization identity while retaining the old spellings as read-side aliases.

Owner-ratified (O1, 2026-07-31) vapour-pressure analytical evidence classes
also route through :func:`canonical_backend_name` at every name-keyed trust,
serialization, cache-admission, and denylist boundary. Each ratified token
canonicalizes to itself; neither aliases to ``internal-analytical``,
``builtin``, or ``vaporock``. They may drive HKL flux only with status-bearing
non-authoritative verdicts and may never certify (see
``simulator.fidelity_vocabulary``).

The retired ``imcc-sf04`` and ``imcc-sf04-ext`` names remain
canonicalization identities so old records still load. Backend selection
rejects them with a typed retirement error that points to ``openimcc``.

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

# O1-ratified vapour-pressure analytical evidence-class tokens (version 1).
# Self-identity at every name-keyed boundary; not backend aliases.
VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED = "analytical:vaporock_calibrated"
VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED = "analytical:external_grounded"
RATIFIED_VAPOUR_ANALYTICAL_EVIDENCE_CLASSES = frozenset(
    {
        VAPOUR_ANALYTICAL_VAPOROCK_CALIBRATED,
        VAPOUR_ANALYTICAL_EXTERNAL_GROUNDED,
    }
)

# Retired IMCC producer identities. Keep their spelling for old records and
# reject them at backend selection; they are never aliases for openimcc.
IMCC_SF04_BACKEND_NAME = "imcc-sf04"
IMCC_SF04_EXT_BACKEND_NAME = "imcc-sf04-ext"
IMCC_SF04_BACKEND_NAMES = frozenset(
    {
        IMCC_SF04_BACKEND_NAME,
        IMCC_SF04_EXT_BACKEND_NAME,
    }
)
RETIRED_IMCC_BACKEND_NAMES = IMCC_SF04_BACKEND_NAMES
OPENIMCC_BACKEND_NAME = "openimcc"

ANALYTICAL_BACKEND_CLASS_DISPLAY_NAME = "InternalAnalyticalBackend"
ANALYTICAL_BACKEND_QUALIFIED_CLASS_NAME = (
    "simulator.melt_backend.base.InternalAnalyticalBackend"
)
LEGACY_ANALYTICAL_BACKEND_CLASS_DISPLAY_NAMES = frozenset({"StubBackend"})
LEGACY_ANALYTICAL_FIDELITY_DIAGNOSTIC_ENV = "FIDELITY_DIAGNOSTIC_STUB_HIGH"


def canonical_backend_name(backend_name: str | None) -> str | None:
    """Fold legacy analytical aliases; keep ratified vapour tokens self-identity.

    The canonical analytical token and its legacy aliases are matched
    case-insensitively after trimming and emit ``internal-analytical``.
    Owner-ratified vapour analytical evidence-class tokens
    (``analytical:vaporock_calibrated``, ``analytical:external_grounded``)
    match case-insensitively and canonicalize to themselves — they never fold
    into ``internal-analytical``. IMCC-SF04 shadow names ``imcc-sf04`` and
    ``imcc-sf04-ext`` match case-insensitively and fold hyphen/underscore
    spellings to the retired hyphenated token. Every other value is returned
    byte-for-byte, preserving strict matching for real backends and
    unknown-name refusals. ``None`` is unchanged.
    """
    if backend_name is None:
        return None
    normalized = str(backend_name).strip().lower()
    if normalized in RATIFIED_VAPOUR_ANALYTICAL_EVIDENCE_CLASSES:
        return normalized
    if (
        normalized == ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
        or normalized in ANALYTICAL_BACKEND_ALIASES
    ):
        return ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
    folded = normalized.replace("_", "-")
    if folded in IMCC_SF04_BACKEND_NAMES:
        return folded
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
