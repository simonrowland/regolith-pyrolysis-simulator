"""Helper-level tests for the 0.5.3 Phase B 2-axis stirring schema.

Pins the ``StirState`` dataclass, the ``clamp_stir_state`` 2-axis
operator-boundary helper, and the backward-compat
``MeltState.stir_factor`` property + dict-vs-scalar override paths.

References:
 - Phase B design (B1 office-hours framing, inline in
   ``docs-private/goal-queue-physics-correctness-0.5.3-2026-05-28.md``):
   axial × radial decomposition (option 1) was selected.
 - Phase B P1 (post-0.5.2 codex/gstack subagent trail): the canonical
   clamp ``clamp_stir_factor`` is preserved as the per-axis helper; the
   2-axis ``clamp_stir_state`` companion routes each axis through it so
   the operator-boundary contract (fail-closed for non-finite/bool,
   clamp to ``[0.0, MAX_STIR_FACTOR]``) carries component-wise.
 - Backward-compat: ``MeltState.stir_factor`` is now a property that
   aliases ``stir_state.axial`` so 0.5.2 readers + writers (web UI,
   campaign overrides, snapshot replay) keep working through the
   deprecation cycle.
"""

from __future__ import annotations

import pytest

from simulator.state import (
    MAX_STIR_FACTOR,
    MeltState,
    StirState,
    clamp_stir_factor,
    clamp_stir_state,
)


# ---------------------------------------------------------------------------
# StirState dataclass: defaults + construction
# ---------------------------------------------------------------------------

def test_stir_state_defaults_preserve_legacy_axial_setpoint():
    """``StirState()`` defaults to ``axial=6.0`` (legacy 0.5.2 C2A
    scalar default) and ``radial=1.0`` (laminar Sherwood baseline,
    ``Sh = 3.66``). These two defaults are load-bearing: ``axial=6.0``
    keeps the evaporation H-K-L multiplier at its 0.5.2 value, and
    ``radial=1.0`` makes the condensation Sh enhancement fall back to
    the laminar baseline until the operator explicitly dials radial."""
    state = StirState()
    assert state.axial == 6.0
    assert state.radial == 1.0


def test_stir_state_explicit_construction_allows_either_axis():
    """Direct construction supports any combination — the dataclass
    is a passive bag of two floats; the clamp + write contracts live
    in ``clamp_stir_state``."""
    assert StirState(axial=8.0, radial=4.0).axial == 8.0
    assert StirState(axial=8.0, radial=4.0).radial == 4.0
    assert StirState(axial=0.0, radial=0.0) == StirState(axial=0.0, radial=0.0)


# ---------------------------------------------------------------------------
# clamp_stir_state: input-shape coverage (scalar / dict / StirState /
# bool / None / non-finite)
# ---------------------------------------------------------------------------

def test_clamp_stir_state_scalar_maps_to_axial_only():
    """Legacy single-axis writer (``session.adjust("stir_factor", 6.0)``,
    campaign overrides with ``'stir_factor': 6.0``) → operator
    intended the axial axis only. The radial axis defaults to ``1.0``
    (laminar baseline) so the condensation Sh path stays at its
    no-stir asymptote rather than silently inheriting the axial
    value. This is the dict-vs-scalar override path's backward-compat
    semantics."""
    state = clamp_stir_state(6.0)
    assert state.axial == 6.0
    assert state.radial == 1.0


def test_clamp_stir_state_scalar_clamps_to_max():
    """Above-ceiling scalar input (auto-tuner override) clamps to
    ``MAX_STIR_FACTOR`` on the axial axis; radial defaults to 1.0."""
    state = clamp_stir_state(100.0)
    assert state.axial == MAX_STIR_FACTOR
    assert state.radial == 1.0


def test_clamp_stir_state_full_dict_drives_both_axes():
    """Canonical 2-axis writer path (new
    ``session.adjust("stir_state", {axial, radial})``). Each axis
    independently clamped, no defaults injected."""
    state = clamp_stir_state({"axial": 6.0, "radial": 4.0})
    assert state.axial == 6.0
    assert state.radial == 4.0


def test_clamp_stir_state_partial_dict_defaults_missing_axis_to_laminar():
    """Partial dict signals "operator only touched this axis" — the
    other axis defaults to ``1.0`` (laminar baseline), not the legacy
    ``6.0``. This is the canonical operator-intent reading: a partial
    dict is NOT a merge against the current state; it's an explicit
    write of one axis with the other intentionally left at the no-stir
    baseline."""
    only_radial = clamp_stir_state({"radial": 8.0})
    assert only_radial.axial == 1.0
    assert only_radial.radial == 8.0
    only_axial = clamp_stir_state({"axial": 8.0})
    assert only_axial.axial == 8.0
    assert only_axial.radial == 1.0


def test_clamp_stir_state_dict_clamps_each_axis_independently():
    """Each axis goes through ``clamp_stir_factor`` independently —
    the per-axis ``MAX_STIR_FACTOR`` ceiling carries to both."""
    state = clamp_stir_state({"axial": 100.0, "radial": -5.0})
    assert state.axial == MAX_STIR_FACTOR
    # Negative clamps to 0.0 (halt-evap signal preserved per-axis).
    assert state.radial == 0.0


def test_clamp_stir_state_dict_per_axis_non_finite_fails_closed():
    """Non-finite per-axis input (NaN, +/-inf) fails closed to 0.0
    on that axis only — the other axis is unaffected. Mirrors the
    ``clamp_stir_factor`` defensive contract component-wise."""
    state = clamp_stir_state({"axial": float("nan"), "radial": 3.0})
    assert state.axial == 0.0
    assert state.radial == 3.0
    state2 = clamp_stir_state({"axial": 5.0, "radial": float("inf")})
    assert state2.axial == 5.0
    assert state2.radial == 0.0


def test_clamp_stir_state_existing_stirstate_reclamps_both_axes():
    """Passing a ``StirState`` through ``clamp_stir_state`` re-clamps
    both axes (sanitises a hand-constructed instance whose axes drift
    out of range). Idempotent for already-valid inputs."""
    sanitised = clamp_stir_state(StirState(axial=100.0, radial=-1.0))
    assert sanitised.axial == MAX_STIR_FACTOR
    assert sanitised.radial == 0.0
    # Idempotency
    valid = StirState(axial=6.0, radial=4.0)
    repeated = clamp_stir_state(valid)
    assert repeated.axial == 6.0 and repeated.radial == 4.0


def test_clamp_stir_state_bool_rejects_both_axes():
    """``bool`` is a Python int subclass; a naive ``float(True)`` lies
    silently ``True``→1.0 / ``False``→0.0. ``clamp_stir_state``
    rejects bool explicitly, both axes fail-closed to 0.0. Same
    defensive contract as ``clamp_stir_factor``."""
    state_true = clamp_stir_state(True)
    state_false = clamp_stir_state(False)
    assert state_true.axial == 0.0 and state_true.radial == 0.0
    assert state_false.axial == 0.0 and state_false.radial == 0.0


def test_clamp_stir_state_none_fails_closed_both_axes():
    """Corrupt-state recovery (a partially-built MeltState whose
    ``stir_state`` field is somehow ``None``). Both axes fail-closed
    to 0.0 — the evaporation consumer reads this as halt-evap and the
    condensation Sherwood helper floors at the laminar baseline."""
    state = clamp_stir_state(None)
    assert state.axial == 0.0 and state.radial == 0.0


def test_clamp_stir_state_non_coercible_string_fails_closed_axial():
    """Non-numeric strings on the scalar path → fail-closed to 0.0
    on axial (the legacy single-axis writer). Radial defaults to 1.0
    because the operator didn't explicitly touch it."""
    state = clamp_stir_state("bogus")
    assert state.axial == 0.0
    assert state.radial == 1.0


def test_clamp_stir_state_returns_fresh_instance_does_not_alias():
    """The helper MUST return a fresh ``StirState`` — mutating the
    returned instance must NOT propagate back to a callable-input
    dataclass that the operator code retains a reference to."""
    original = StirState(axial=6.0, radial=4.0)
    clamped = clamp_stir_state(original)
    clamped.axial = 99.0
    assert original.axial == 6.0  # original untouched


# ---------------------------------------------------------------------------
# MeltState.stir_factor property: backward-compat alias to stir_state.axial
# ---------------------------------------------------------------------------

def test_melt_default_stir_factor_still_equals_six_via_property():
    """Pre-0.5.3 ``MeltState.stir_factor = 6.0`` was the bare-float
    default. 0.5.3 Phase B replaces it with a ``StirState`` field
    holding ``axial=6.0`` + a property that aliases to ``axial``.
    The default-value contract for the legacy attribute is preserved
    byte-for-byte: any code that read ``melt.stir_factor`` keeps
    seeing ``6.0``."""
    melt = MeltState()
    assert melt.stir_factor == 6.0
    assert melt.stir_state.axial == 6.0
    assert melt.stir_state.radial == 1.0  # no-stir Sh baseline


def test_melt_stir_factor_property_setter_writes_axial_only():
    """Legacy writer (``melt.stir_factor = 8.0``) → writes the axial
    axis, leaves radial untouched. Backward-compat: 0.5.2 callers
    that set ``stir_factor`` did NOT intend to inflate the radial
    Sh path."""
    melt = MeltState()
    assert melt.stir_state.radial == 1.0
    melt.stir_factor = 8.0
    assert melt.stir_factor == 8.0
    assert melt.stir_state.axial == 8.0
    assert melt.stir_state.radial == 1.0  # unchanged


def test_melt_stir_state_direct_write_does_not_disturb_property_read():
    """The forward-compat path: operators / runners can write
    ``melt.stir_state`` directly (canonical 2-axis API). The legacy
    ``stir_factor`` property reads through the underlying dataclass,
    so any code that mixes the two access patterns stays consistent."""
    melt = MeltState()
    melt.stir_state = StirState(axial=4.0, radial=7.0)
    assert melt.stir_factor == 4.0
    assert melt.stir_state.radial == 7.0


def test_melt_stir_factor_property_setter_does_not_clamp():
    """Property setter is deliberately UNclamped — the operator-
    boundary writers (``simulator/session.py``,
    ``simulator/campaigns.py``) pre-clamp via ``clamp_stir_factor``
    BEFORE writing the property. Pre-0.5.3 the bare-float field
    didn't clamp either, so this preserves the byte-compatible
    contract for direct in-process writers (unit tests, snapshot
    replay, runner internal hooks). Clamping is the writer's job."""
    melt = MeltState()
    # No clamp on direct property write — caller is responsible.
    melt.stir_factor = 100.0
    assert melt.stir_factor == 100.0  # NOT clamped at property setter
    # Operator-boundary writers DO clamp:
    melt.stir_factor = clamp_stir_factor(100.0)
    assert melt.stir_factor == MAX_STIR_FACTOR
