"""Canonical naming for the analytical (builtin) melt backend.

Trust-architecture vocabulary names the analytical, non-real-engine chemistry
model ``internal-analytical`` (docs-private/design-fidelity-surface-2026-06-10.md
§STUB REBRAND; AGENTS.md §Chemistry-engine policy C3). Its legacy name is
``stub``.

The rebrand is **alias-preserving**: the literal ``stub`` token is baked into
cache keys, EvalSpec serialization, shipped profiles, recipe-DB artifacts, and
goldens, so ``stub`` stays the STABLE serialization identity. We accept the new
display name on input but always fold it back onto ``stub`` before any
name-keyed branch or serialization. Renaming the serialized token is a
deferred, ``corpus_version``-gated key-migration chunk — not this one — so
caches and physics goldens do not move.

This module deliberately has no heavy dependencies so it can be imported from
the EvalSpec cache-key path without pulling in ``simulator.core``.
"""

from __future__ import annotations


# CORPUS-BUMP MIGRATION HINGE: t-011 must change this token only as part of the
# atomic corpus_version bump described in
# docs-private/research/2026-07-11-t172-rename/flip-list.md. Never rename it in
# an ordinary internal-analytical wording or symbol cleanup.
ANALYTICAL_BACKEND_SERIALIZATION_TOKEN = "stub"
ANALYTICAL_BACKEND_DISPLAY_NAME = "internal-analytical"
ANALYTICAL_BACKEND_ALIASES = frozenset(
    {"internal-analytical", "internal_analytical"}
)


def canonical_backend_name(backend_name: str | None) -> str | None:
    """Fold the ``internal-analytical`` display alias onto the stable ``stub`` token.

    Only the analytical-model aliases are folded; every other value (including
    ``stub``, ``alphamelts``, ``auto``, ``cached-real``, ``""``, and unknown
    names) is returned byte-for-byte so existing case-sensitivity (runner-strict
    exact matching) and serialization identity stay unchanged. ``None`` is
    returned unchanged. Keeping ``stub`` as the emitted token is what makes the
    rebrand golden-neutral.
    """
    if backend_name is None:
        return None
    if str(backend_name).strip().lower() in ANALYTICAL_BACKEND_ALIASES:
        return ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
    return backend_name
