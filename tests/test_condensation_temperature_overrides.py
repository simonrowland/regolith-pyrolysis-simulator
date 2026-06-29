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

import copy
import math
from pathlib import Path

import pytest
import yaml

from simulator import condensation as condensation_module
from simulator.condensation import (
    CONDENSATION_TEMPS_C,
    CondensationModel,
    apply_setpoints_condensation_temperature_overrides,
    restore_condensation_temperature_overrides,
)
from simulator.state import CondensationTrain, EvaporationFlux, MeltState


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


def test_instance_apply_setpoints_overrides_isolates_per_model():
    """0.5.4.1 review-cluster-C (P2 #1): the new
    ``CondensationModel.apply_setpoints_overrides`` instance method
    MUST NOT mutate the module-level ``CONDENSATION_TEMPS_C`` dict.
    Two CondensationModel instances with different setpoints can
    coexist in the same Python interpreter without cross-
    contamination (multi-tenant requirement)."""
    from simulator.condensation import CondensationModel
    from simulator.state import CondensationTrain

    pre_module_dict = dict(CONDENSATION_TEMPS_C)

    model_a = CondensationModel(CondensationTrain.create_default())
    model_b = CondensationModel(CondensationTrain.create_default())

    model_a.apply_setpoints_overrides({
        'condensation_train': {
            'condensation_temperatures_C': {'SiO': 920.0},
        },
    })
    model_b.apply_setpoints_overrides({
        'condensation_train': {
            'condensation_temperatures_C': {'SiO': 1180.0},
        },
    })

    # Each instance carries its own dict — no cross-contamination.
    assert model_a.condensation_temperatures_C['SiO'] == 920.0
    assert model_b.condensation_temperatures_C['SiO'] == 1180.0
    # Module-level dict UNTOUCHED by the instance method.
    assert CONDENSATION_TEMPS_C == pre_module_dict, (
        "instance apply_setpoints_overrides MUST NOT mutate the "
        "module-level CONDENSATION_TEMPS_C fallback dict"
    )


def test_stage0_hot_wall_diagnostic_is_sibling_and_route_neutral():
    melt = MeltState(temperature_C=1700.0)
    flux = EvaporationFlux(species_kg_hr={'SiO': 1.0}, total_kg_hr=1.0)

    def _model(threshold_C: float | None = None) -> CondensationModel:
        model = CondensationModel(
            CondensationTrain.create_default(),
            wall_temperature_C=1500.0,
        )
        if threshold_C is not None:
            model.apply_setpoints_overrides({
                'condensation_train': {
                    'metals_train': {
                        'stage_0_hot_duct': {
                            'temp_range_C': [threshold_C, 1600.0],
                        },
                    },
                },
            })
        model.configure_operating_conditions(
            wall_temperature_C=1500.0,
            pipe_segment_temperatures_C={
                segment.name: (
                    1410.0
                    if segment.name == 'stage_0_to_stage_1'
                    else 1800.0
                )
                for segment in model.pipe_segments
            },
        )
        return model

    base = _model()
    custom = _model(1425.0)
    base_route = base.route(flux, melt)
    custom_route = custom.route(flux, melt)

    assert not base.last_cold_spot_diagnostic[
        'has_upstream_hot_wall_violation'
    ]
    assert custom.last_cold_spot_diagnostic['upstream_hot_wall_min_C'] == 1425.0
    assert custom.last_cold_spot_diagnostic[
        'has_upstream_hot_wall_violation'
    ]
    assert custom.last_cold_spot_diagnostic['upstream_hot_wall_findings'][0][
        'segment'
    ] == 'stage_0_to_stage_1'
    assert base_route.cold_spot_warnings == ()
    assert custom_route.cold_spot_warnings == ()
    assert custom_route.remaining_by_species == base_route.remaining_by_species
    assert (
        custom_route.condensed_by_stage_species
        == base_route.condensed_by_stage_species
    )
    assert custom_route.wall_deposit_by_species == base_route.wall_deposit_by_species
    assert (
        custom_route.wall_deposit_by_segment_species
        == base_route.wall_deposit_by_segment_species
    )


def test_pyrolysis_simulator_uses_instance_isolation_not_module_mutation():
    """End-to-end: a fresh PyrolysisSimulator built with custom
    setpoints applies the override on the CondensationModel
    instance, NOT the module dict. Two sims with different SiO
    Tcond values keep their values independently."""
    from simulator.core import PyrolysisSimulator
    from simulator.melt_backend.base import StubBackend

    pre_module_dict = dict(CONDENSATION_TEMPS_C)

    def _build(sio_override: float) -> PyrolysisSimulator:
        b = StubBackend()
        b.initialize({})
        return PyrolysisSimulator(
            b,
            {
                'campaigns': {},
                'condensation_train': {
                    'condensation_temperatures_C': {
                        'SiO': sio_override,
                    },
                },
            },
            {'x': {'label': 'X', 'composition_wt_pct': {'SiO2': 100}}},
            {'metals': {}, 'oxide_vapors': {}},
        )

    sim_a = _build(940.0)
    sim_b = _build(1190.0)

    _ = sim_a.condensation_model
    _ = sim_b.condensation_model

    assert sim_a.condensation_model.condensation_temperatures_C['SiO'] == 940.0
    assert sim_b.condensation_model.condensation_temperatures_C['SiO'] == 1190.0
    # Module dict completely untouched.
    assert CONDENSATION_TEMPS_C == pre_module_dict, (
        "PyrolysisSimulator condensation_model property MUST NOT "
        "mutate the module-level dict (was the cross-contamination "
        "footgun reported in evening-4commits review P2 #1)"
    )


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
    # runs the override on the INSTANCE dict (post-cluster-C, the
    # module-level dict is no longer mutated by the production
    # path — instance isolation is the new contract).
    _ = sim.condensation_model
    assert (
        sim.condensation_model.condensation_temperatures_C['SiO']
        == 980.0
    )


def test_new_simulator_uses_reloaded_materials_without_module_reload(tmp_path):
    from simulator.backends import SimulatorBuildConfig, build_simulator
    from simulator.config import load_config_bundle
    from simulator.melt_backend.base import StubBackend

    default_model = CondensationModel(
        CondensationTrain.create_default(),
        wall_temperature_C=900.0,
    )
    default_route = default_model.route(
        EvaporationFlux(species_kg_hr={'SiO': 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1700.0),
    )
    assert (
        default_route.sticking_alpha_provenance_notice["alpha_s_by_species"][
            "SiO"
        ]
        == pytest.approx(0.04)
    )

    materials_path = tmp_path / "materials.yaml"
    source_path = Path(__file__).resolve().parents[1] / "data" / "materials.yaml"
    materials_path.write_text(
        source_path.read_text().replace(
            "  SiO: &alpha_SiO\n"
            "    value_ref: data/literature/vacuum_pyrolysis_sticking.yaml::species.SiO.value\n",
            "  SiO: &alpha_SiO\n"
            "    value: 0.23\n"
            "    status: UNCERTIFIED\n"
            "    source_class: test_material_override\n"
            "    source: tests/test_condensation_temperature_overrides.py\n"
            "    output_status: status_bearing\n",
            1,
        )
    )
    bundle = load_config_bundle(materials_path=materials_path)

    assert (
        condensation_module.MATERIALS_DATA["default_alpha_s_by_species"][
            "SiO"
        ]["value_ref"]
        == "data/literature/vacuum_pyrolysis_sticking.yaml::species.SiO.value"
    )
    backend = StubBackend()
    backend.initialize({})
    sim = build_simulator(
        SimulatorBuildConfig(
            backend=backend,
            setpoints=bundle.setpoints,
            feedstocks=bundle.feedstocks,
            vapor_pressures=bundle.vapor_pressures,
            materials=bundle.materials,
        )
    )
    sim.condensation_model.configure_operating_conditions(
        wall_temperature_C=900.0
    )
    route = sim.condensation_model.route(
        EvaporationFlux(species_kg_hr={'SiO': 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1700.0),
    )

    assert sim.condensation_model.materials["default_liner_alpha_s_by_species"][
        "SiO"
    ]["value"] == pytest.approx(0.23)
    assert route.sticking_alpha_provenance_notice["alpha_s_by_species"][
        "SiO"
    ] == pytest.approx(0.23)


@pytest.mark.parametrize(
    "subpath",
    ["cold_spot_diagnostic", "pressure_isolated_capture_budget"],
)
def test_instance_temperature_override_reaches_all_route_subpaths(subpath):
    CONDENSATION_TEMPS_C['SiO'] = 1300.0

    default_model = CondensationModel(
        CondensationTrain.create_default(),
        wall_temperature_C=1000.0,
    )
    default_result = default_model.route(
        EvaporationFlux(species_kg_hr={'SiO': 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1500.0),
    )

    overridden_model = CondensationModel(
        CondensationTrain.create_default(),
        wall_temperature_C=1000.0,
    )
    overridden_model.condensation_temperatures_C['SiO'] = 900.0
    overridden_result = overridden_model.route(
        EvaporationFlux(species_kg_hr={'SiO': 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1500.0),
    )

    if subpath == "cold_spot_diagnostic":
        assert default_model.last_cold_spot_diagnostic["has_cold_spot"]
        assert not overridden_model.last_cold_spot_diagnostic["has_cold_spot"]
    else:
        assert overridden_result.remaining_by_species["SiO"] > (
            default_result.remaining_by_species["SiO"] + 0.05
        )


def test_custom_vapor_pressure_bundle_reaches_condensation_route_with_fallback():
    def route_calcium(vapor_pressure_data=None):
        model = CondensationModel(
            CondensationTrain.create_default(),
            vapor_pressure_data=vapor_pressure_data,
            wall_temperature_C=900.0,
        )
        model.configure_operating_conditions(
            wall_temperature_C=900.0,
            pipe_segment_temperatures_C={
                segment.name: 900.0 for segment in model.pipe_segments
            },
        )
        return model.route(
            EvaporationFlux(species_kg_hr={"Ca": 1.0}, total_kg_hr=1.0),
            MeltState(temperature_C=1700.0),
        )

    custom_vapor_pressures = {
        "metals": {
            "Ca": copy.deepcopy(
                condensation_module.VAPOR_PRESSURE_DATA["metals"]["Ca"]
            )
        },
        "oxide_vapors": {},
    }
    custom_vapor_pressures["metals"]["Ca"]["antoine"]["A"] += 8.0

    default_route = route_calcium()
    custom_route = route_calcium(custom_vapor_pressures)

    default_stage4 = default_route.condensed_by_stage_species[4]["Ca"]
    default_stage5 = default_route.condensed_by_stage_species[5]["Ca"]
    custom_stage4 = custom_route.condensed_by_stage_species[4]["Ca"]
    custom_stage5 = custom_route.condensed_by_stage_species[5]["Ca"]

    assert custom_stage4 > default_stage4
    assert custom_stage5 < default_stage5 * 0.8

    default_sio_psat = condensation_module._antoine_psat_pa("SiO", 1700.0)
    fallback_sio_psat = condensation_module._antoine_psat_pa(
        "SiO",
        1700.0,
        vapor_pressure_data=custom_vapor_pressures,
    )
    assert fallback_sio_psat == default_sio_psat


def test_partial_custom_antoine_block_falls_back_to_global_coefficients():
    temperature_K = 320.0 + 273.15
    default_psat = condensation_module._antoine_psat_pa("Ca", temperature_K)
    custom_vapor_pressures = {
        "metals": {"Ca": {"antoine": {"A": 99.0}}},
        "oxide_vapors": {},
    }

    with pytest.warns(RuntimeWarning, match="incomplete custom vapor-pressure"):
        fallback_psat = condensation_module._antoine_psat_pa(
            "Ca",
            temperature_K,
            vapor_pressure_data=custom_vapor_pressures,
        )

    assert fallback_psat == default_psat
    assert fallback_psat < 1.0
