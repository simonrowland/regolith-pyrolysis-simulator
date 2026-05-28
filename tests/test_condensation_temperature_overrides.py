"""B1-tunable (CW3 follow-on, 2026-05-28): YAML-driven per-species
condensation temperatures via
``simulator.condensation.apply_setpoints_condensation_temperature_overrides``.

Pre-B1-tunable the per-species condensation temperatures in
``CONDENSATION_TEMPS_C`` were hardcoded — operators editing
``data/setpoints.yaml`` saw no effect (dead-config pattern, same as
the CW1 MRE voltage ladder before B5). This wire makes the YAML
canonical for the operator surface; the in-source dict is the
fallback when the YAML is missing or has degenerate values.

These tests pin:
1. ``apply_setpoints_condensation_temperature_overrides`` applies
   valid YAML values to the module-level dict.
2. Snapshot return value enables restore via
   ``restore_condensation_temperature_overrides``.
3. Defensive: non-finite / non-coercible / missing inputs are
   skipped.
4. Idempotency: re-applying the same setpoints leaves the dict
   identical.
5. End-to-end: a sim built from a setpoints dict with a custom
   ``SiO`` value reads the override at route time.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simulator import condensation as condensation_module
from simulator.condensation import (
    CONDENSATION_TEMPS_C,
    apply_setpoints_condensation_temperature_overrides,
    restore_condensation_temperature_overrides,
)


@pytest.fixture(autouse=True)
def _restore_condensation_temps():
    """Snapshot the module-level dict before each test and restore
    after, so tests don't leak override state into each other."""
    snapshot = dict(CONDENSATION_TEMPS_C)
    yield
    restore_condensation_temperature_overrides(snapshot)


# ---------------------------------------------------------------------------
# 1. Apply / restore round-trip
# ---------------------------------------------------------------------------

def test_apply_valid_yaml_overrides_module_dict():
    """A YAML setpoints block with a recognised species key
    overrides the module-level dict in place."""
    original_SiO = CONDENSATION_TEMPS_C['SiO']
    apply_setpoints_condensation_temperature_overrides({
        'condensation_train': {
            'condensation_temperatures_C': {
                'SiO': 950.0,
            },
        },
    })
    assert CONDENSATION_TEMPS_C['SiO'] == 950.0
    # Other species untouched.
    assert CONDENSATION_TEMPS_C['Fe'] == 1250
    # Confirm the autouse fixture restores the original after the test.


def test_apply_returns_snapshot_of_pre_merge_state():
    """The return value is a pre-merge snapshot so callers can
    restore in a try/finally."""
    pre = apply_setpoints_condensation_temperature_overrides({
        'condensation_train': {
            'condensation_temperatures_C': {'SiO': 900.0},
        },
    })
    assert pre['SiO'] == 1050  # the fallback default
    assert CONDENSATION_TEMPS_C['SiO'] == 900.0


def test_restore_returns_dict_to_snapshot_state():
    """``restore_condensation_temperature_overrides`` is the inverse
    of ``apply_*`` — it sets the module dict to the snapshot
    exactly (handles species added by the apply call too)."""
    snapshot = apply_setpoints_condensation_temperature_overrides(None)
    CONDENSATION_TEMPS_C['UnknownSpecies'] = 99.0
    restore_condensation_temperature_overrides(snapshot)
    assert 'UnknownSpecies' not in CONDENSATION_TEMPS_C


# ---------------------------------------------------------------------------
# 2. Defensive paths: None / missing / malformed inputs
# ---------------------------------------------------------------------------

def test_apply_none_setpoints_returns_snapshot_unchanged():
    """``None`` setpoints → snapshot of current state, no mutation."""
    pre_SiO = CONDENSATION_TEMPS_C['SiO']
    snapshot = apply_setpoints_condensation_temperature_overrides(None)
    assert snapshot == CONDENSATION_TEMPS_C
    assert CONDENSATION_TEMPS_C['SiO'] == pre_SiO


def test_apply_missing_block_returns_snapshot_unchanged():
    """Setpoints without the canonical
    ``condensation_train.condensation_temperatures_C`` block → no
    mutation."""
    pre_state = dict(CONDENSATION_TEMPS_C)
    apply_setpoints_condensation_temperature_overrides({'other_keys': 1})
    assert CONDENSATION_TEMPS_C == pre_state


def test_apply_skips_non_finite_and_non_coercible_values():
    """NaN, inf, non-coercible strings, None values — all skipped
    silently. The legitimate entry in the same block is still
    applied."""
    pre_Fe = CONDENSATION_TEMPS_C['Fe']
    apply_setpoints_condensation_temperature_overrides({
        'condensation_train': {
            'condensation_temperatures_C': {
                'Fe': 1500.0,                 # ok
                'SiO': float('nan'),          # NaN
                'Mg': float('inf'),           # inf
                'CrO2': 'bogus',              # non-coercible
                'Na': None,                   # None
            },
        },
    })
    assert CONDENSATION_TEMPS_C['Fe'] == 1500.0
    # Skipped entries keep their pre-merge values.
    assert CONDENSATION_TEMPS_C['SiO'] == 1050
    assert CONDENSATION_TEMPS_C['Mg'] == 580
    assert CONDENSATION_TEMPS_C['CrO2'] == 1250
    assert CONDENSATION_TEMPS_C['Na'] == 480


# ---------------------------------------------------------------------------
# 3. Idempotency + species the fallback doesn't have
# ---------------------------------------------------------------------------

def test_apply_is_idempotent_under_same_setpoints():
    """Calling apply twice with the same input leaves the dict in
    the same state — no incremental drift."""
    setpoints = {
        'condensation_train': {
            'condensation_temperatures_C': {'SiO': 950.0},
        },
    }
    apply_setpoints_condensation_temperature_overrides(setpoints)
    snapshot_after_first = dict(CONDENSATION_TEMPS_C)
    apply_setpoints_condensation_temperature_overrides(setpoints)
    assert CONDENSATION_TEMPS_C == snapshot_after_first


def test_apply_adds_species_not_in_fallback():
    """Operators can ADD species via the YAML (e.g., a custom oxide
    they want routed). The module dict gains the new key; the
    fallback species are unchanged."""
    pre_Fe = CONDENSATION_TEMPS_C['Fe']
    apply_setpoints_condensation_temperature_overrides({
        'condensation_train': {
            'condensation_temperatures_C': {
                'CustomOxide': 1100.0,
            },
        },
    })
    assert CONDENSATION_TEMPS_C['CustomOxide'] == 1100.0
    assert CONDENSATION_TEMPS_C['Fe'] == pre_Fe


# ---------------------------------------------------------------------------
# 4. End-to-end via project setpoints.yaml
# ---------------------------------------------------------------------------

def test_real_setpoints_yaml_produces_same_dict_as_fallback():
    """The shipped ``data/setpoints.yaml`` carries the same SiO etc.
    values as the in-source fallback (per the B1-tunable design:
    YAML is canonical, fallback is the safety net; both agree by
    convention). Loading the real YAML must NOT shift any species
    away from the fallback values."""
    repo_root = Path(__file__).resolve().parent.parent
    setpoints = yaml.safe_load(
        (repo_root / "data" / "setpoints.yaml").read_text()
    )
    pre_state = dict(CONDENSATION_TEMPS_C)
    apply_setpoints_condensation_temperature_overrides(setpoints)
    # SiO: YAML says 1050 (the documented recipe midpoint); fallback
    # also says 1050. Round-trip equality.
    assert CONDENSATION_TEMPS_C['SiO'] == pre_state['SiO']
    assert CONDENSATION_TEMPS_C['Fe'] == pre_state['Fe']
    assert CONDENSATION_TEMPS_C['CrO2'] == pre_state['CrO2']


def test_species_condensation_temp_reads_yaml_override_end_to_end():
    """0.5.4.1 morning-review P2 #3 (codex 2026-05-28) refutation:
    the reviewer claimed B1-tunable's YAML override doesn't flow
    through to ``_species_condensation_temperature_C``. This test
    confirms it does — the override mutates the module-level
    ``CONDENSATION_TEMPS_C`` dict, and the reader reads from that
    dict at line 1391.

    Path traced:
    1. ``PyrolysisSimulator.condensation_model`` property
       calls ``apply_setpoints_condensation_temperature_overrides(
       self.setpoints)``.
    2. That mutates ``CONDENSATION_TEMPS_C`` in place.
    3. ``_species_condensation_temperature_C(species)`` at
       ``simulator/condensation.py:1391`` checks
       ``if species in CONDENSATION_TEMPS_C:`` and returns
       ``float(CONDENSATION_TEMPS_C[species])``.

    Override flows through end-to-end."""
    from simulator.condensation import _species_condensation_temperature_C

    apply_setpoints_condensation_temperature_overrides({
        'condensation_train': {
            'condensation_temperatures_C': {'SiO': 1099.0},
        },
    })
    # The canonical reader sees the YAML override, not the original
    # 1050 fallback.
    assert _species_condensation_temperature_C('SiO') == 1099.0


def test_simulator_construction_applies_setpoints_overrides():
    """End-to-end: a PyrolysisSimulator built from a setpoints dict
    with a custom SiO Tcond reads the override when the
    condensation model accesses
    ``CONDENSATION_TEMPS_C['SiO']``."""
    from simulator.core import PyrolysisSimulator
    from simulator.melt_backend.base import StubBackend

    custom_setpoints = {
        'campaigns': {},
        'condensation_train': {
            'condensation_temperatures_C': {
                'SiO': 980.0,  # cold-baffle override
            },
        },
    }
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        custom_setpoints,
        {'x': {'label': 'X', 'composition_wt_pct': {'SiO2': 100}}},
        {'metals': {}, 'oxide_vapors': {}},
    )
    # Trigger condensation_model build; the property apply path
    # runs the override.
    _ = sim.condensation_model
    assert CONDENSATION_TEMPS_C['SiO'] == 980.0
