"""Guards and regressions for simulator.equilibrium."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

import simulator.equilibrium as equilibrium_module
from simulator.core import (
    PyrolysisSimulator,
    _MeltRedoxLiquidusFloorFallback,
    _RESOLVE_MELT_REDOX_GATE_AUTHORITY,
)
from simulator.chemistry.melt_activity import (
    MELT_OXIDE_CATIONS_PER_FORMULA,
    single_cation_component_formula,
)
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.equilibrium import (
    EquilibriumMixin,
    _internal_analytical_control_refusal,
)
from simulator.evaporation import (
    _KRESS91_FLOOR_OPEN_SIDE_C,
    _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE,
)
from simulator.fe_redox import (
    KRESS91_LIQUID_CALIBRATION_MIN_T_C,
    LiquidFeODomain,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.silent_zero import (
    CATEGORY_PROVEN_ZERO,
    CATEGORY_REFUSE,
    ZeroBecause,
)


def _minimal_sim(*, temperature_C, p_total_mbar):
    return SimpleNamespace(
        melt=SimpleNamespace(
            temperature_C=temperature_C,
            p_total_mbar=p_total_mbar,
        )
    )


def _typed_omission_sim(*, metals=None, oxide_vapors=None, species=None):
    metals = {} if metals is None else metals
    return SimpleNamespace(
        melt=SimpleNamespace(
            temperature_C=726.85,
            p_total_mbar=1.0,
            oxygen_reservoir=None,
            melt_fO2_log=-9.0,
            fO2_log=-9.0,
            composition_wt_pct=lambda: {},
        ),
        vapor_pressures={
            "metals": metals,
            "oxide_vapors": oxide_vapors or {},
        },
        _ELLINGHAM_THERMO=(tuple(metals) if species is None else tuple(species)),
        _headspace_transport_pO2_bar=lambda: 1e-12,
        _oxygen_numerical_floor_bar=lambda: 1e-12,
        atom_ledger=None,
    )


def _run_typed_omission_sim(sim, monkeypatch, *, a_feo=1.0):
    monkeypatch.setattr(
        equilibrium_module,
        "calphad_ferrous_feo_activity_diagnostic",
        lambda **_kwargs: {
            "a_FeO_authoritative": a_feo,
            "sources": {},
        },
    )
    return EquilibriumMixin._internal_analytical_equilibrium(sim)


def _metal_ellingham_fallback_sim(species, parent_oxide, temperature_K):
    row = {
        'parent_oxide': parent_oxide,
        'pure_component_antoine': {'A': 0.0, 'B': 0.0, 'C': 0.0},
        'valid_range_K': [1000.0, 3000.0],
    }
    sim = _typed_omission_sim(metals={species: row}, species=(species,))
    sim.melt.temperature_C = temperature_K - CELSIUS_TO_KELVIN_OFFSET
    return sim


def test_internal_analytical_cold_path_still_ok_at_25_c():
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=25.0, p_total_mbar=1.0)
    )
    assert result.status == "ok"
    assert result.pressure_bar == pytest.approx(0.001)
    assert result.vapor_pressures_Pa == {}
    assert result.liquid_fraction is None
    assert result.phase_assemblage_available is False
    assert result.diagnostics["vapor_pressure_zero_reason"] == "internal_analytical_below_400K"


def test_internal_analytical_zero_mbar_is_admitted_vacuum():
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=25.0, p_total_mbar=0.0)
    )
    assert result.status == "ok"
    assert result.pressure_bar == 0.0


# Mg/Ca liquid-standard pressure ratios below use their JANAF factors at
# 2156.15 K; keep the shared solve at that same physical temperature.
def _markova_core(
    fO2_log: float,
    temperature_K: float = 2156.15,
    *,
    composition_kg: dict[str, float] | None = None,
):
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _new_internal_analytical_core,
    )

    core = _new_internal_analytical_core()
    if composition_kg is None:
        composition_kg = {
            "SiO2": 0.4402,
            "Al2O3": 0.0456,
            "FeO": 0.0804,
            "MgO": 0.3644,
            "CaO": 0.0695,
        }
    else:
        composition_kg = dict(composition_kg)
    core.atom_ledger = core._new_atom_ledger()
    core._load_ledger_account(
        "process.cleaned_melt",
        composition_kg,
        source="Markova VI surface-potential invariant",
    )
    core.melt.composition_kg = dict(composition_kg)
    core.melt.update_total_mass()
    core.melt.temperature_C = temperature_K - CELSIUS_TO_KELVIN_OFFSET
    core.melt.p_total_mbar = 100.0
    core.melt.fO2_log = fO2_log
    core.melt.melt_fO2_log = fO2_log
    core.melt.oxygen_reservoir.melt_intrinsic_fO2_log = fO2_log
    core.melt.oxygen_reservoir.headspace_transport_pO2_bar = (
        43.7 * 10.0**fO2_log
    )
    core.melt.oxygen_reservoir.headspace_ledger_pO2_bar = (
        43.7 * 10.0**fO2_log
    )
    return core


def test_internal_analytical_mn_uses_selected_segment_at_2058k():
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _markova_core(
            -100.0,
            2058.0,
            composition_kg={"MnO": 1.0},
        )
    )

    metal_caveat = result.diagnostics["activity_standard_state_flags"]["Mn"]
    floor_notice = result.diagnostics["melt_metal_saturation_floor_notice"]
    assert "MnO(lit. liquid range)" in metal_caveat["K_reference_phase"]
    assert floor_notice["metal"] == "Mn"
    assert "MnO(lit. liquid range)" in floor_notice["engine_model_caveat"][
        "K_reference_phase"
    ]


def _markova_surface_equilibrium(fO2_log: float):
    return EquilibriumMixin._internal_analytical_equilibrium(
        _markova_core(fO2_log)
    )


def test_internal_analytical_channels_share_exactly_one_surface_pO2():
    result = _markova_surface_equilibrium(-5.344)
    inputs = result.diagnostics["surface_oxygen_potential_inputs_bar"]
    surface_pO2_bar = result.diagnostics["surface_oxygen_potential_bar"]

    assert "SiO" in inputs
    assert "Mg" in inputs
    assert set(inputs.values()) == {surface_pO2_bar}
    assert surface_pO2_bar != 43.7 * 10.0**-5.344


def test_internal_analytical_mg_pressure_uses_liquid_standard_conversion(
    monkeypatch,
):
    import simulator.equilibrium as equilibrium_module

    temperature_K = 2156.15
    log10_factor = (
        equilibrium_module._oxide_liquid_standard_log10_factor_per_oxide(
            'MgO', temperature_K
        )
    )
    assert log10_factor == pytest.approx(0.542275, abs=1e-6)

    converted_pressure = _markova_surface_equilibrium(-5.344).vapor_pressures_Pa[
        "Mg"
    ]
    with monkeypatch.context() as patch:
        patch.setattr(
            equilibrium_module,
            "_oxide_liquid_standard_log10_factor_per_oxide",
            lambda _oxide, _temperature_K: 0.0,
        )
        crystal_standard_pressure = (
            _markova_surface_equilibrium(-5.344).vapor_pressures_Pa["Mg"]
        )

    # Mg-010's decomposition K contains two MgO units and the gas pressure
    # takes the square root, so the JANAF per-MgO correction survives once.
    assert converted_pressure / crystal_standard_pressure == pytest.approx(
        10.0**log10_factor,
        rel=1e-12,
    )


def test_internal_analytical_ca_pressure_uses_liquid_standard_conversion(
    monkeypatch,
):
    temperature_K = 2156.15
    log10_factor = equilibrium_module._oxide_liquid_standard_log10_factor_per_oxide(
        'CaO', temperature_K
    )
    assert log10_factor == pytest.approx(0.624220, abs=1e-6)

    converted_pressure = _markova_surface_equilibrium(-5.344).vapor_pressures_Pa[
        'Ca'
    ]
    with monkeypatch.context() as patch:
        patch.setattr(
            equilibrium_module,
            '_oxide_liquid_standard_log10_factor_per_oxide',
            lambda _oxide, _temperature_K: 0.0,
        )
        crystal_standard_pressure = (
            _markova_surface_equilibrium(-5.344).vapor_pressures_Pa['Ca']
        )

    # Ca's two-CaO Ellingham reaction and square-root metal pressure leave
    # one JANAF CaO liquid-standard correction in the predicted pressure.
    assert converted_pressure / crystal_standard_pressure == pytest.approx(
        10.0**log10_factor,
        rel=1e-12,
    )


@pytest.mark.parametrize(
    (
        'species',
        'oxide',
        'temperature_K',
        'liquid_standard_factor_dex',
        'pressure_dex_per_oxide_dex',
    ),
    [
        ('Al', 'Al2O3', 2156.15, 0.1884078657, 0.5),
        ('Ti', 'TiO2', 2073.15, 0.0443590835, 1.0),
        ('Cr', 'Cr2O3', 2156.15, 0.5246179442, 0.5),
        ('Fe', 'FeO', 1500.0, 0.075147, 1.0),
        ('Zr', 'ZrO2', 2156.15, 0.5289049456, 1.0),
    ],
)
def test_internal_analytical_crystal_ellingham_k_is_converted_to_liquid(
    monkeypatch,
    species,
    oxide,
    temperature_K,
    liquid_standard_factor_dex,
    pressure_dex_per_oxide_dex,
):
    original_factor = (
        equilibrium_module._oxide_liquid_standard_log10_factor_per_oxide
    )
    log10_factor = original_factor(oxide, temperature_K)
    assert log10_factor == pytest.approx(liquid_standard_factor_dex, abs=1e-6)

    sim = _metal_ellingham_fallback_sim(species, oxide, temperature_K)
    # Zr's strongly favorable Ellingham K puts its pressure below the
    # 1e-15 Pa output cutoff at this test's usual 1e-8 oxide activity.
    # Unit activity keeps the Zr channel computed so the standard-state
    # conversion ratio remains observable.
    oxide_activity_value = 1.0 if species == 'Zr' else 1e-8
    oxide_activity = SimpleNamespace(
        activity=oxide_activity_value,
        warning=None,
        equivalent_parent_activity=lambda exponent: (
            oxide_activity_value ** (1.0 / exponent)
        ),
        provenance=lambda: {},
    )
    monkeypatch.setattr(
        equilibrium_module,
        'melt_oxide_activity',
        lambda *_args, **_kwargs: oxide_activity,
    )
    converted_pressure = _run_typed_omission_sim(sim, monkeypatch).vapor_pressures_Pa[
        species
    ]

    with monkeypatch.context() as patch:
        patch.setattr(
            equilibrium_module,
            '_oxide_liquid_standard_log10_factor_per_oxide',
            lambda current_oxide, _temperature_K: (
                0.0 if current_oxide == oxide
                else original_factor(current_oxide, _temperature_K)
            ),
        )
        crystal_standard_pressure = _run_typed_omission_sim(
            sim, monkeypatch
        ).vapor_pressures_Pa[species]

    assert converted_pressure / crystal_standard_pressure == pytest.approx(
        10.0 ** (pressure_dex_per_oxide_dex * log10_factor),
        rel=1e-12,
    )


@pytest.mark.parametrize(
    ('species', 'oxide', 'temperature_K'),
    [
        ('Al', 'Al2O3', 2400.0),
        ('Ti', 'TiO2', 2156.15),
    ],
)
def test_internal_analytical_liquid_ellingham_source_is_not_shifted(
    monkeypatch, species, oxide, temperature_K
):
    original_factor = (
        equilibrium_module._oxide_liquid_standard_log10_factor_per_oxide
    )
    oxide_activity = SimpleNamespace(
        activity=1e-8,
        warning=None,
        equivalent_parent_activity=lambda exponent: 1e-8 ** (1.0 / exponent),
        provenance=lambda: {},
    )
    monkeypatch.setattr(
        equilibrium_module,
        'melt_oxide_activity',
        lambda *_args, **_kwargs: oxide_activity,
    )

    def reject_liquid_source_shift(current_oxide, current_temperature_K):
        if current_oxide == oxide:
            pytest.fail(f'{species} source K is already on the liquid oxide standard')
        return original_factor(current_oxide, current_temperature_K)

    monkeypatch.setattr(
        equilibrium_module,
        '_oxide_liquid_standard_log10_factor_per_oxide',
        reject_liquid_source_shift,
    )
    sim = _metal_ellingham_fallback_sim(species, oxide, temperature_K)
    result = _run_typed_omission_sim(sim, monkeypatch)

    assert species in result.vapor_pressures_Pa
    assert species not in result.diagnostics['activity_standard_state_flags']


@pytest.mark.parametrize(
    ('species', 'oxide', 'temperature_K'),
    [
        ('K', 'K2O', 2156.15),
        ('Ni', 'NiO', 2156.15),
        ('Mn', 'MnO', 2000.0),
        ('Mn', 'MnO', 2156.15),
    ],
)
def test_internal_analytical_unresolved_oxide_standard_keeps_pressure_with_caveat(
    monkeypatch, species, oxide, temperature_K
):
    oxide_activity = SimpleNamespace(
        activity=1e-8,
        warning=None,
        equivalent_parent_activity=lambda exponent: 1e-8 ** (1.0 / exponent),
        provenance=lambda: {},
    )
    monkeypatch.setattr(
        equilibrium_module,
        'melt_oxide_activity',
        lambda *_args, **_kwargs: oxide_activity,
    )
    result = _run_typed_omission_sim(
        _metal_ellingham_fallback_sim(species, oxide, temperature_K),
        monkeypatch,
    )
    caveat = result.diagnostics['activity_standard_state_flags'][species]

    assert species in result.vapor_pressures_Pa
    assert caveat['type'] == 'typed_notice'
    assert caveat['kind'] == 'engine_model_caveat'
    assert caveat['K_reference_phase'].startswith(oxide)
    assert caveat['activity_reference_phase'].startswith(f'{oxide}(l)')
    assert caveat['reference_phase_mismatch_magnitude'] == 'unknown'
    assert caveat['conversion_applied'] is False
    if oxide == 'K2O':
        assert 'Lamoreaux & Hildenbrand 1984 K2O(l)' in caveat['reason']
        assert 'JANAF K-012' in caveat['reason']


@pytest.mark.parametrize(
    ('oxide', 'melting_temperature_K'),
    [('Al2O3', 2327.0), ('TiO2', 2130.0), ('Cr2O3', 2603.0), ('ZrO2', 2950.0)],
)
def test_oxide_liquid_standard_shift_is_zero_at_melting_and_positive_below(
    oxide, melting_temperature_K
):
    factor = equilibrium_module._oxide_liquid_standard_log10_factor_per_oxide

    assert factor(oxide, melting_temperature_K) == pytest.approx(0.0, abs=1e-3)
    assert factor(oxide, melting_temperature_K - 100.0) > 0.0


def test_internal_analytical_cao_vapor_uses_liquid_standard_conversion(
    monkeypatch,
):
    class Evaluator:
        pO2_reference_bar = 1.0

        def __init__(self, species):
            self.activity_exponent = 1.0 if species == 'CaO_gas' else 2.0
            self.pO2_exponent = 0.0 if species == 'CaO_gas' else -1.0

        def evaluate(self, temperature_K, *, source_activity, pO2_bar):
            assert temperature_K == 1000.0
            assert source_activity == 1.0
            return SimpleNamespace(
                pressure_pa=(
                    10.0
                    * (pO2_bar / self.pO2_reference_bar)
                    ** self.pO2_exponent
                ),
                out_of_range=False,
            )

    class Catalog:
        def evaluator_for_hot_train(self, species):
            assert species in {'CaO_gas', 'Ca2'}
            return Evaluator(species)

        species = {
            name: SimpleNamespace(
                source_reaction_activity=SimpleNamespace(
                    component_id='CaO',
                    standard_state=SimpleNamespace(phase='liquid'),
                )
            )
            for name in ('CaO_gas', 'Ca2')
        }

    sim = _typed_omission_sim(
        oxide_vapors={
            'CaO_gas': {
                'parent_oxide': 'CaO',
                'fit_target': 'standard_reaction_term',
                'reference_pressure_model': {'evaluator_family': 'nasa_cea_9'},
                'valid_range_K': [1000.0, 1000.0],
                'reaction': {'formula': 'CaO(cr) -> CaO(g)'},
                'oxide_activity_exponent': 1.0,
            },
            'Ca2': {
                'parent_oxide': 'CaO',
                'fit_target': 'standard_reaction_term',
                'reference_pressure_model': {'evaluator_family': 'nasa_cea_9'},
                'valid_range_K': [1000.0, 1000.0],
                'reaction': {'formula': '2 CaO(cr) -> Ca2(g) + O2(g)'},
                'oxide_activity_exponent': 2.0,
                'pO2_exponent': -1.0,
                'pO2_reference_bar': 1.0,
            },
        }
    )
    sim.vapour_rail_catalog = Catalog()
    oxide_activity = SimpleNamespace(
        single_cation_component='CaO',
        activity=0.5,
        warning=None,
        thermodynamic_parent_activity=lambda: 0.5,
        provenance=lambda: {},
    )
    monkeypatch.setattr(
        equilibrium_module,
        'melt_oxide_activity',
        lambda *_args, **_kwargs: oxide_activity,
    )
    result = _run_typed_omission_sim(sim, monkeypatch)
    converted_pressure = result.vapor_pressures_Pa['CaO_gas']
    converted_ca2_pressure = result.vapor_pressures_Pa['Ca2']
    original_factor = equilibrium_module._oxide_liquid_standard_log10_factor_per_oxide

    with monkeypatch.context() as patch:
        patch.setattr(
            equilibrium_module,
            '_oxide_liquid_standard_log10_factor_per_oxide',
            lambda oxide, temperature_K: (
                original_factor(oxide, temperature_K)
                if oxide != 'CaO'
                else 0.0
            ),
        )
        crystal_standard_pressure = _run_typed_omission_sim(
            sim, monkeypatch
        ).vapor_pressures_Pa
        oxide_activity = SimpleNamespace(
            single_cation_component='CaO',
            activity=1.0,
            warning=None,
            thermodynamic_parent_activity=lambda: 1.0,
            provenance=lambda: {},
        )
        unit_activity_pressure = _run_typed_omission_sim(
            sim, monkeypatch
        ).vapor_pressures_Pa

    log10_factor = equilibrium_module._oxide_liquid_standard_log10_factor_per_oxide(
        'CaO', 1000.0
    )
    assert converted_pressure / crystal_standard_pressure['CaO_gas'] == pytest.approx(
        10.0**log10_factor,
        rel=1e-12,
    )
    assert converted_ca2_pressure / crystal_standard_pressure['Ca2'] == pytest.approx(
        10.0 ** (2.0 * log10_factor),
        rel=1e-12,
    )
    # Ca2 consumes two CaO formula units: P(Ca2) scales as a_CaO**2.
    # equivalent_parent_activity(2)**2 would cancel to a_CaO and fail here.
    assert crystal_standard_pressure['Ca2'] / unit_activity_pressure['Ca2'] == pytest.approx(
        0.25,
        rel=1e-12,
    )


def test_internal_analytical_coo_keeps_prediction_with_typed_phase_caveat(
    monkeypatch,
):
    class Evaluator:
        pO2_reference_bar = 1.0
        activity_exponent = 1.0
        pO2_exponent = 0.0

        def evaluate(self, temperature_K, *, source_activity, pO2_bar):
            assert temperature_K == 2000.0
            assert source_activity == 1.0
            return SimpleNamespace(pressure_pa=4.0, out_of_range=False)

    class Catalog:
        def evaluator_for_hot_train(self, species):
            assert species == 'CoO_gas'
            return Evaluator()

        species = {
            'CoO_gas': SimpleNamespace(
                source_reaction_activity=SimpleNamespace(
                    component_id='CoO',
                    # The shipped CoO_gas model declares CoO(cr); preserve
                    # that phase and disclose the ungrounded liquid activity.
                    standard_state=SimpleNamespace(phase='crystalline'),
                )
            )
        }

    sim = _typed_omission_sim(
        oxide_vapors={
            'CoO_gas': {
                'parent_oxide': 'CoO',
                'reference_pressure_model': {'evaluator_family': 'shomate'},
                'valid_range_K': [1400.0, 2000.0],
                'oxide_activity_exponent': 1.0,
                'pO2_exponent': 0.0,
            }
        }
    )
    sim.melt.temperature_C = 1726.85
    sim.vapour_rail_catalog = Catalog()
    oxide_activity = SimpleNamespace(
        single_cation_component='CoO',
        activity=0.5,
        warning=None,
        thermodynamic_parent_activity=lambda: 0.5,
        provenance=lambda: {},
    )
    monkeypatch.setattr(
        equilibrium_module,
        'melt_oxide_activity',
        lambda *_args, **_kwargs: oxide_activity,
    )

    result = _run_typed_omission_sim(sim, monkeypatch)
    caveat = result.diagnostics['activity_standard_state_flags']['CoO_gas']

    assert result.vapor_pressures_Pa['CoO_gas'] == pytest.approx(2.0)
    assert caveat['type'] == 'typed_notice'
    assert caveat['kind'] == 'engine_model_caveat'
    assert caveat['K_reference_phase'] == 'CoO(cr)'
    assert caveat['activity_reference_phase'] == 'CoO(l), Raoultian pure-liquid'
    assert caveat['reference_phase_mismatch_magnitude'] == 'unknown'
    assert caveat['conversion_applied'] is False


@pytest.mark.parametrize(
    ('species', 'oxide', 'reaction', 'activity_exponent', 'pO2_exponent'),
    [
        ('Mg2', 'MgO', '2 MgO(cr) -> Mg2(g) + O2(g)', 2.0, -1.0),
        ('MgO_gas', 'MgO', 'MgO(cr) -> MgO(g)', 1.0, 0.0),
    ],
)
def test_internal_analytical_predicts_compiled_oxide_vapors_without_antoine(
    monkeypatch, species, oxide, reaction, activity_exponent, pO2_exponent
):
    class Evaluator:
        pO2_reference_bar = 1.0
        valid_temperature_K = (1000.0, 1000.0)

        def __init__(self):
            self.activity_exponent = activity_exponent
            self.pO2_exponent = pO2_exponent

        def evaluate(self, temperature_K, *, source_activity, pO2_bar):
            assert temperature_K == 1000.0
            assert source_activity == 1.0
            return SimpleNamespace(
                pressure_pa=(
                    10.0
                    * (pO2_bar / self.pO2_reference_bar)
                    ** self.pO2_exponent
                ),
                out_of_range=False,
            )

    class Catalog:
        def evaluator_for_hot_train(self, species_id):
            assert species_id == species
            return Evaluator()

        def __init__(self):
            self.species = {
                species: SimpleNamespace(
                    source_reaction_activity=SimpleNamespace(
                        component_id=single_cation_component_formula(oxide),
                        standard_state=SimpleNamespace(phase='liquid'),
                    )
                )
            }

    sim = _typed_omission_sim(
        oxide_vapors={
            species: {
                'parent_oxide': oxide,
                'fit_target': 'standard_reaction_term',
                'reference_pressure_model': {'evaluator_family': 'nasa_cea_9'},
                'valid_range_K': [1000.0, 1000.0],
                'reaction': {'formula': reaction},
                'oxide_activity_exponent': activity_exponent,
                'pO2_exponent': pO2_exponent,
                'pO2_reference_bar': 1.0,
            }
        },
        species=(),
    )
    sim.vapour_rail_catalog = Catalog()
    oxide_activity = SimpleNamespace(
        single_cation_component=single_cation_component_formula(oxide),
        activity=0.5,
        warning=None,
        thermodynamic_parent_activity=lambda: 0.5,
        provenance=lambda: {},
    )
    monkeypatch.setattr(
        equilibrium_module,
        'melt_oxide_activity',
        lambda *_args, **_kwargs: oxide_activity,
    )

    result = _run_typed_omission_sim(sim, monkeypatch)

    assert species in result.vapor_pressures_Pa
    surface_pO2 = result.diagnostics['surface_oxygen_potential_bar']
    expected = 10.0 * 0.5**activity_exponent * (
        surface_pO2**pO2_exponent
    )
    if oxide == 'MgO':
        factor = equilibrium_module._oxide_liquid_standard_log10_factor_per_oxide(
            'MgO', 1000.0
        )
        expected *= 10.0 ** (activity_exponent * factor)
        assert species not in result.diagnostics['activity_standard_state_flags']
    assert result.vapor_pressures_Pa[species] == pytest.approx(expected)


@pytest.mark.parametrize(
    ('species', 'oxide'), [('NiO_gas', 'NiO'), ('MnO_gas', 'MnO')]
)
def test_internal_analytical_ni_mn_vapors_use_shipped_liquid_reactions(
    monkeypatch, species, oxide
):
    core = _markova_core(-5.344, 2156.15)
    row = dict(core.vapor_pressures['oxide_vapors'][species])
    compiled_species = core.vapour_rail_catalog.species[species]
    assert row['reaction']['formula'] == f'{oxide}(l) -> {oxide}(g)'
    assert (
        compiled_species.source_reaction_activity.standard_state.phase
        == 'liquid'
    )
    assert row.get('reference_pressure_model')
    row['antoine'] = {}
    sim = _typed_omission_sim(oxide_vapors={species: row}, species=())
    sim.melt.temperature_C = 2156.15 - CELSIUS_TO_KELVIN_OFFSET
    sim.vapour_rail_catalog = core.vapour_rail_catalog
    oxide_activity = SimpleNamespace(
        single_cation_component=single_cation_component_formula(oxide),
        activity=0.5,
        warning=None,
        thermodynamic_parent_activity=lambda: 0.5,
        provenance=lambda: {},
    )
    monkeypatch.setattr(
        equilibrium_module,
        'melt_oxide_activity',
        lambda *_args, **_kwargs: oxide_activity,
    )

    result = _run_typed_omission_sim(sim, monkeypatch)

    assert species in result.vapor_pressures_Pa
    # The shipped K is already liquid-standard, so no crystal/liquid
    # conversion caveat belongs on either channel.
    assert species not in result.diagnostics['activity_standard_state_flags']


def test_internal_analytical_surface_channel_inherits_floor_caveat(monkeypatch):
    class Evaluator:
        activity_exponent = 0.0
        pO2_exponent = 0.5
        pO2_reference_bar = 1.0
        valid_temperature_K = (2000.0, 2200.0)

        def evaluate(self, temperature_K, *, source_activity, pO2_bar):
            assert temperature_K == 2156.15
            assert source_activity == 1.0
            return SimpleNamespace(
                pressure_pa=10.0 * (pO2_bar / self.pO2_reference_bar) ** 0.5,
                out_of_range=False,
            )

    class Catalog:
        species = {}

        def evaluator_for_hot_train(self, species):
            assert species == 'FeO_association_gas'
            return Evaluator()

    sim = _typed_omission_sim(
        oxide_vapors={
            'FeO_association_gas': {
                'parent_oxide': 'FeO',
                'fit_target': 'standard_reaction_term',
                'authority_class': 'analytical_non_authoritative',
                'runtime_disposition': 'status_bearing_non_authoritative',
                'reference_pressure_model': {'evaluator_family': 'nasa_cea_9'},
                'valid_range_K': [2000.0, 2200.0],
                'reaction': {
                    'formula': 'Fe(g) + 0.5 O2(g) -> FeO(g)',
                },
            }
        },
        species=(),
    )
    sim.melt.composition_wt_pct = lambda: {'FeO': 100.0}
    sim.melt.temperature_C = 2156.15 - CELSIUS_TO_KELVIN_OFFSET
    sim.melt.melt_fO2_log = -100.0
    sim.melt.fO2_log = -100.0
    sim.vapour_rail_catalog = Catalog()
    # Ni's Ellingham row ends at 2000 K, below this 2156.15 K fixture. Mn's
    # upper reconstructed segment is active here and must carry its ungrounded
    # liquid-standard caveat through the shared surface pO2 channel.
    monkeypatch.setattr(
        equilibrium_module,
        'activities_for_metal_saturation_floor',
        lambda *_args, **_kwargs: {'MnO': 1.0},
    )

    result = _run_typed_omission_sim(sim, monkeypatch, a_feo=0.0)

    floor_notice = result.diagnostics['melt_metal_saturation_floor_notice']
    caveat = floor_notice['engine_model_caveat']
    assert floor_notice['metal'] == 'Mn'
    assert caveat['kind'] == 'engine_model_caveat'
    assert caveat['floor_log10_fO2_bar'] == pytest.approx(
        floor_notice['floor_log10_fO2_bar']
    )
    assert result.diagnostics['surface_oxygen_potential_inputs_bar'][
        'FeO_association_gas'
    ] == result.diagnostics['surface_oxygen_potential_bar']
    channel_caveat = result.diagnostics['activity_standard_state_flags'][
        'FeO_association_gas'
    ]
    assert channel_caveat['kind'] == 'engine_model_caveat'
    assert channel_caveat['floor_log10_fO2_bar'] == pytest.approx(
        floor_notice['floor_log10_fO2_bar']
    )
    assert result.vapor_pressures_source['FeO_association_gas'].startswith(
        'builtin_authority_limited:standard_reaction_term'
    )


def test_internal_analytical_every_channel_obeys_mass_action_exponents(
    monkeypatch,
):
    from simulator.vapour_rail.catalog import HotTrainInapplicable

    core = _markova_core(-5.344)
    compiled_catalog = core.vapour_rail_catalog
    projected_rows = core.vapor_pressures['oxide_vapors']

    class ReactionExponentProbe:
        def __init__(self, evaluator):
            self.activity_exponent = evaluator.activity_exponent
            self.pO2_exponent = evaluator.pO2_exponent
            self.pO2_reference_bar = evaluator.pO2_reference_bar
            self.valid_temperature_K = evaluator.valid_temperature_K

        def evaluate(self, temperature_K, *, source_activity, pO2_bar):
            assert source_activity == 1.0
            pressure = 1000.0 * (
                pO2_bar / self.pO2_reference_bar
            ) ** self.pO2_exponent
            return SimpleNamespace(pressure_pa=pressure, out_of_range=False)

    class Catalog:
        def __init__(self):
            self.species = compiled_catalog.species

        def evaluator_for_hot_train(self, species):
            return ReactionExponentProbe(
                compiled_catalog.evaluator_for_hot_train(species)
            )

    catalog = Catalog()
    candidates = []
    compiled_names = set()
    for species, row in projected_rows.items():
        if row.get('reference_pressure_model'):
            try:
                evaluator = compiled_catalog.evaluator_for_hot_train(species)
            except HotTrainInapplicable:
                continue
            candidates.append((species, row, evaluator, True, False))
            compiled_names.add(species)
        else:
            try:
                equilibrium_module._read_antoine_coefficients(
                    row.get('antoine', {})
                )
            except equilibrium_module._AntoineCoefficientRefusal:
                continue
            candidates.append((species, row, None, False, False))

    metal_row = {
        'parent_oxide': 'FeO',
        'pure_component_antoine': {'A': 3.0, 'B': 0.0, 'C': 0.0},
        'valid_range_K': [2000.0, 2200.0],
    }
    candidates.append(('Fe', metal_row, None, False, True))

    assert {
        'Al2O', 'CrO', 'Ca2', 'Mg2', 'FeO_association_gas',
        'K2O_gas', 'Na2O_gas',
    } <= compiled_names

    original_factor = equilibrium_module._oxide_liquid_standard_log10_factor_per_oxide
    original_floor_activities = equilibrium_module.activities_for_metal_saturation_floor
    original_melt_oxide_activity = equilibrium_module.melt_oxide_activity
    original_physical_pO2 = equilibrium_module.physical_melt_dissociation_pO2_bar
    original_feo_activity = equilibrium_module.calphad_ferrous_feo_activity_diagnostic
    monkeypatch.setattr(
        equilibrium_module,
        '_oxide_liquid_standard_log10_factor_per_oxide',
        lambda *_args, **_kwargs: 0.0,
    )
    monkeypatch.setattr(
        equilibrium_module,
        'activities_for_metal_saturation_floor',
        lambda *_args, **_kwargs: {},
    )

    def pressure_for(
        species, row, is_compiled, is_metal, parent_activity, pO2
    ):
        parent_oxide = row.get('parent_oxide', '')
        cation_count = float(
            MELT_OXIDE_CATIONS_PER_FORMULA.get(parent_oxide, 1.0)
        )
        single_cation_activity = parent_activity ** (1.0 / cation_count)
        monkeypatch.setattr(
            equilibrium_module,
            'melt_oxide_activity',
            lambda requested_oxide, *_args, **_kwargs: SimpleNamespace(
                parent_oxide=requested_oxide,
                single_cation_component=single_cation_component_formula(
                    requested_oxide
                ),
                activity=single_cation_activity,
                warning=None,
                thermodynamic_parent_activity=lambda: parent_activity,
                provenance=lambda: {},
            ),
        )
        monkeypatch.setattr(
            equilibrium_module,
            'physical_melt_dissociation_pO2_bar',
            lambda _fO2: (pO2, False),
        )
        channel_row = dict(row)
        channel_row['valid_range_K'] = [2000.0, 2200.0]
        if not is_metal:
            channel_row['antoine'] = (
                {}
                if is_compiled
                else {'A': 3.0, 'B': 0.0, 'C': 0.0}
            )
        sim = _typed_omission_sim(
            metals={species: channel_row} if is_metal else {},
            oxide_vapors={} if is_metal else {species: channel_row},
            species=(species,) if is_metal else (),
        )
        sim.melt.composition_wt_pct = lambda: {parent_oxide: 100.0}
        sim.melt.temperature_C = 2156.15 - CELSIUS_TO_KELVIN_OFFSET
        sim.vapour_rail_catalog = catalog
        result = _run_typed_omission_sim(
            sim,
            monkeypatch,
            a_feo=parent_activity if is_metal else 1.0,
        )
        assert species in result.vapor_pressures_Pa, species
        return result.vapor_pressures_Pa[species]

    tested_names = set()
    for species, row, evaluator, is_compiled, is_metal in candidates:
        base = pressure_for(
            species, row, is_compiled, is_metal, 0.8, 1.0e-3
        )
        activity_changed = pressure_for(
            species, row, is_compiled, is_metal, 0.4, 1.0e-3
        )
        oxygen_changed = pressure_for(
            species, row, is_compiled, is_metal, 0.8, 4.0e-3
        )
        parent_oxide = row.get('parent_oxide', '')
        if is_metal:
            n_m, n_ox = equilibrium_module.ellingham_stoichiometry(species)
            activity_exponent = n_ox / n_m
            pO2_exponent = -1.0 / n_m
        elif is_compiled:
            declaration = compiled_catalog.species[
                species
            ].source_reaction_activity
            declared_component = (
                declaration.component_id if declaration is not None else None
            )
            if not parent_oxide or evaluator.activity_exponent == 0.0:
                activity_exponent = 0.0
            elif declared_component == parent_oxide:
                # The reaction exponent acts on the declared formula activity,
                # whose log slope against parent activity is one.
                activity_exponent = evaluator.activity_exponent
            elif declared_component == single_cation_component_formula(
                parent_oxide
            ):
                # A single-cation activity is a_parent**(1/c); apply the
                # reaction's declared exponent through that chain rule.
                activity_exponent = evaluator.activity_exponent / float(
                    MELT_OXIDE_CATIONS_PER_FORMULA.get(parent_oxide, 1.0)
                )
            else:
                raise AssertionError(
                    f'{species}: unexpected declared activity basis '
                    f'{declared_component!r} for {parent_oxide!r}'
                )
            pO2_exponent = evaluator.pO2_exponent
        else:
            activity_exponent = (
                float(row.get('oxide_activity_exponent', 1.0))
                if parent_oxide
                else 0.0
            )
            pO2_exponent = float(row.get('pO2_exponent', 0.0) or 0.0)
            if species == 'SiO' and pO2_exponent == 0.0:
                # SiO's shared helper implements its declared half-O2 term.
                pO2_exponent = -0.5
        assert math.log(activity_changed / base) / math.log(0.5) == pytest.approx(
            activity_exponent,
            abs=1.0e-10,
        ), species
        assert math.log(oxygen_changed / base) / math.log(4.0) == pytest.approx(
            pO2_exponent,
            abs=1.0e-10,
        ), species
        tested_names.add(species)

    assert compiled_names <= tested_names
    assert 'Fe' in tested_names
    assert tested_names

    # Exercise the actual Markova floor and compiled evaluator together. The
    # synthetic probes above isolate each logarithmic slope; this solve checks
    # that production routes both a metal rail and FeO association through the
    # same computed surface pO2 without substituting either owner.
    for name, original in (
        ('_oxide_liquid_standard_log10_factor_per_oxide', original_factor),
        ('activities_for_metal_saturation_floor', original_floor_activities),
        ('melt_oxide_activity', original_melt_oxide_activity),
        ('physical_melt_dissociation_pO2_bar', original_physical_pO2),
        ('calphad_ferrous_feo_activity_diagnostic', original_feo_activity),
    ):
        monkeypatch.setattr(equilibrium_module, name, original)
    real_result = EquilibriumMixin._internal_analytical_equilibrium(
        _markova_core(-100.0)
    )
    floor_notice = real_result.diagnostics['melt_metal_saturation_floor_notice']
    surface_pO2_bar = real_result.diagnostics['surface_oxygen_potential_bar']
    assert floor_notice['floor_log10_fO2_bar'] > -100.0
    assert floor_notice['metal'] in real_result.vapor_pressures_Pa
    assert 'FeO_association_gas' in real_result.vapor_pressures_Pa
    assert real_result.diagnostics['surface_oxygen_potential_inputs_bar'][
        'FeO_association_gas'
    ] == surface_pO2_bar

    # Raise the real binding saturation floor without changing melt
    # activities. Scaling each floor-couple activity by its reaction
    # stoichiometry raises every candidate floor by the same amount; the
    # production pressure evaluators then expose each channel's real pO2 slope.
    from simulator.chemistry.ellingham_thermo import (
        ellingham_segment_for_temperature,
        ellingham_stoichiometry,
    )
    from simulator.metal_saturation_floor import METAL_SATURATION_FLOOR_OXIDES

    floor_activity_provider = (
        equilibrium_module.activities_for_metal_saturation_floor
    )
    floor_offset_dex = 0.2
    metal_rail_composition_kg = {
        'SiO2': 0.65,
        'Al2O3': 0.20,
        'CaO': 0.05,
        'MgO': 0.05,
        'Na2O': 0.02,
        'K2O': 0.01,
        'TiO2': 1.0e-5,
        'Cr2O3': 1.0e-5,
        'MnO': 1.0e-5,
        'FeO': 1.0e-5,
    }
    floor_activity_log_slopes = {'FeO': 2.0}
    floor_activity_log_slopes.update(
        {
            oxide: float(
                ellingham_segment_for_temperature(metal, 2156.15).n_ox
            )
            for metal, oxide in METAL_SATURATION_FLOOR_OXIDES
        }
    )

    def solve_metal_rails_with_floor_offset(offset_dex):
        def floor_activities(*args, **kwargs):
            activities = floor_activity_provider(*args, **kwargs)
            for oxide, log_slope in floor_activity_log_slopes.items():
                if oxide in activities:
                    activities[oxide] *= 10.0 ** (offset_dex / log_slope)
            return activities

        monkeypatch.setattr(
            equilibrium_module,
            'activities_for_metal_saturation_floor',
            floor_activities,
        )
        core = _markova_core(
            -100.0,
            composition_kg=metal_rail_composition_kg,
        )
        return EquilibriumMixin._internal_analytical_equilibrium(core)

    silica_floor_base = solve_metal_rails_with_floor_offset(0.0)
    silica_floor_raised = solve_metal_rails_with_floor_offset(floor_offset_dex)
    base_floor_notice = silica_floor_base.diagnostics[
        'melt_metal_saturation_floor_notice'
    ]
    raised_floor_notice = silica_floor_raised.diagnostics[
        'melt_metal_saturation_floor_notice'
    ]
    assert base_floor_notice['metal'] == raised_floor_notice['metal'] == 'Si'
    measured_floor_shift = (
        raised_floor_notice['floor_log10_fO2_bar']
        - base_floor_notice['floor_log10_fO2_bar']
    )
    assert measured_floor_shift == pytest.approx(floor_offset_dex, abs=1e-10)
    silica_channel_pO2_exponents = {
        'Si': -1.0,
        'SiO': -0.5,
        'Si2': -2.0,
        'Si3': -3.0,
        'SiO2_gas': 0.0,
    }
    for species, exponent in silica_channel_pO2_exponents.items():
        base_pressure = silica_floor_base.vapor_pressures_Pa[species]
        raised_pressure = silica_floor_raised.vapor_pressures_Pa[species]
        measured_pressure_shift = math.log10(raised_pressure / base_pressure)
        assert measured_pressure_shift == pytest.approx(
            exponent * measured_floor_shift,
            abs=1e-8,
        ), species

    # The fixture includes major Al/Ca/Mg/Na/K sources and trace Ti/Cr/Mn/Fe
    # sources. Those trace oxides keep their saturation floors below the
    # silica floor while still publishing uncapped metal pressures.
    metal_channel_pO2_exponents = {
        metal: -1.0 / ellingham_stoichiometry(metal)[0]
        for metal in ('Al', 'Ca', 'Mg', 'Ti', 'Cr', 'Mn', 'Fe')
    }
    metal_channel_pO2_exponents.update({'Na': -0.25, 'K': -0.25})
    for species, exponent in metal_channel_pO2_exponents.items():
        assert species in silica_floor_base.vapor_pressures_Pa, species
        assert species in silica_floor_raised.vapor_pressures_Pa, species
        base_pressure = silica_floor_base.vapor_pressures_Pa[species]
        raised_pressure = silica_floor_raised.vapor_pressures_Pa[species]
        measured_pressure_shift = math.log10(raised_pressure / base_pressure)
        assert measured_pressure_shift == pytest.approx(
            exponent * measured_floor_shift,
            abs=1e-8,
        ), species


def test_internal_analytical_ca_channels_keep_estimates_at_3700k():
    from simulator.vapour_rail.catalog import CompiledPressureEvaluator

    core = _markova_core(-5.344, 3700.0)
    assert isinstance(
        core.vapour_rail_catalog.evaluator_for_hot_train('Ca2'),
        CompiledPressureEvaluator,
    )
    result = EquilibriumMixin._internal_analytical_equilibrium(core)

    assert result.status == 'ok'
    assert {'Ca', 'Ca2'} <= result.vapor_pressures_Pa.keys()
    for species in ('Ca', 'Ca2'):
        caveat = result.diagnostics['activity_standard_state_flags'][species]
        assert caveat['type'] == 'typed_notice'
        assert caveat['kind'] == 'engine_model_caveat'
        assert caveat['conversion_applied'] is False
    assert (
        result.diagnostics['extrapolated_beyond_valid_range_K']['Ca2'][
            'authority_level'
        ]
        == 'extrapolated'
    )


def test_internal_analytical_unresolved_mgo_standard_state_keeps_prediction(
    monkeypatch,
):
    from simulator.reference_data import janaf

    original_fusion_energy = janaf.janaf_fusion_energy

    def missing_mgo_fusion_pair(oxide, *args, **kwargs):
        if oxide != "MgO":
            return original_fusion_energy(oxide, *args, **kwargs)
        raise FileNotFoundError("MgO JANAF liquid/crystal table is unavailable")

    monkeypatch.setattr(janaf, "janaf_fusion_energy", missing_mgo_fusion_pair)
    result = _markova_surface_equilibrium(-5.344)
    flag = result.diagnostics["activity_standard_state_flags"]["Mg"]

    assert flag["type"] == "typed_notice"
    assert flag["kind"] == "engine_model_caveat"
    assert flag["conversion_applied"] is False
    assert "Mg" in result.vapor_pressures_Pa


def test_internal_analytical_janaf_hash_mismatch_is_not_caught(monkeypatch):
    from simulator.reference_data import janaf

    original_fusion_energy = janaf.janaf_fusion_energy

    def bad_source_hash(oxide, *args, **kwargs):
        if oxide == "MgO":
            raise ValueError("JANAF source hash mismatch")
        return original_fusion_energy(oxide, *args, **kwargs)

    monkeypatch.setattr(janaf, "janaf_fusion_energy", bad_source_hash)
    with pytest.raises(ValueError, match="source hash mismatch"):
        _markova_surface_equilibrium(-5.344)


def _real_compiled_ca2_omission_sim(temperature_K):
    core = _markova_core(-5.344, temperature_K)
    ca2 = dict(core.vapor_pressures["oxide_vapors"]["Ca2"])
    ca2["antoine"] = {}
    assert ca2.get("reference_pressure_model")
    sim = _typed_omission_sim(oxide_vapors={"Ca2": ca2}, species=())
    sim.melt.temperature_C = temperature_K - CELSIUS_TO_KELVIN_OFFSET
    sim.vapour_rail_catalog = core.vapour_rail_catalog
    return sim


@pytest.mark.parametrize(
    ("failure_type", "failure_message", "expected_to_raise"),
    [
        (FileNotFoundError, "JANAF CaO table unavailable", False),
        (ValueError, "JANAF source hash mismatch", True),
    ],
)
def test_compiled_ca2_janaf_failures_keep_missing_data_and_raise_hash_bugs(
    monkeypatch, failure_type, failure_message, expected_to_raise
):
    from simulator.reference_data import janaf

    original_fusion_energy = janaf.janaf_fusion_energy

    def injected_failure(oxide, *args, **kwargs):
        if oxide == "CaO":
            raise failure_type(failure_message)
        return original_fusion_energy(oxide, *args, **kwargs)

    monkeypatch.setattr(janaf, "janaf_fusion_energy", injected_failure)
    oxide_activity = SimpleNamespace(
        single_cation_component="CaO",
        activity=0.5,
        warning=None,
        thermodynamic_parent_activity=lambda: 0.5,
        provenance=lambda: {},
    )
    monkeypatch.setattr(
        equilibrium_module,
        "melt_oxide_activity",
        lambda *_args, **_kwargs: oxide_activity,
    )
    sim = _real_compiled_ca2_omission_sim(2156.15)
    if expected_to_raise:
        with pytest.raises(ValueError, match="source hash mismatch"):
            _run_typed_omission_sim(sim, monkeypatch)
        return

    result = _run_typed_omission_sim(sim, monkeypatch)
    assert "Ca2" in result.vapor_pressures_Pa
    caveat = result.diagnostics["activity_standard_state_flags"]["Ca2"]
    assert caveat["type"] == "typed_notice"
    assert caveat["kind"] == "engine_model_caveat"
    assert caveat["conversion_applied"] is False


def test_internal_analytical_equal_exponent_sio_mg_ratio_cancels_fO2():
    lower_fO2 = _markova_surface_equilibrium(-5.344)
    higher_fO2 = _markova_surface_equilibrium(-4.344)
    lower_ratio = (
        lower_fO2.vapor_pressures_Pa["SiO"]
        / lower_fO2.vapor_pressures_Pa["Mg"]
    )
    higher_ratio = (
        higher_fO2.vapor_pressures_Pa["SiO"]
        / higher_fO2.vapor_pressures_Pa["Mg"]
    )

    assert lower_ratio == pytest.approx(higher_ratio, rel=1e-12, abs=0.0)


@pytest.mark.parametrize(
    "temperature_C",
    (
        -CELSIUS_TO_KELVIN_OFFSET,
        -CELSIUS_TO_KELVIN_OFFSET - 1.0,
        float("-inf"),
        float("nan"),
        float("inf"),
    ),
)
def test_internal_analytical_refuses_non_positive_or_nonfinite_temperature(
    temperature_C,
):
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=temperature_C, p_total_mbar=1.0)
    )
    assert result.status == "out_of_domain"
    assert result.vapor_pressures_Pa == {}
    assert result.fO2_log is None
    assert result.pressure_bar == 0.0
    assert result.diagnostics["internal_analytical_refusal"] == (
        "invalid_scientific_controls"
    )
    fields = {row["field"] for row in result.diagnostics["invalid_controls"]}
    assert "temperature_C" in fields


@pytest.mark.parametrize("p_total_mbar", (float("nan"), float("inf"), -1.0))
def test_internal_analytical_refuses_nonfinite_or_negative_total_pressure(
    p_total_mbar,
):
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=1600.0, p_total_mbar=p_total_mbar)
    )
    assert result.status == "out_of_domain"
    assert result.vapor_pressures_Pa == {}
    assert result.pressure_bar == 0.0
    fields = {row["field"] for row in result.diagnostics["invalid_controls"]}
    assert "p_total_mbar" in fields


def test_internal_analytical_nan_temperature_does_not_raise_ellingham_valueerror():
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=float("nan"), p_total_mbar=1.0)
    )
    assert result.status == "out_of_domain"
    assert result.diagnostics["internal_analytical_refusal"] == (
        "invalid_scientific_controls"
    )


def test_internal_analytical_refuses_non_numeric_controls():
    result = EquilibriumMixin._internal_analytical_equilibrium(
        _minimal_sim(temperature_C=True, p_total_mbar="1.0")
    )
    assert result.status == "out_of_domain"
    fields = {row["field"] for row in result.diagnostics["invalid_controls"]}
    assert fields == {"temperature_C", "p_total_mbar"}


def test_internal_analytical_control_refusal_helper_shape():
    diag = _internal_analytical_control_refusal(float("nan"), -1.0)
    assert diag is not None
    fields = {row["field"] for row in diag["invalid_controls"]}
    assert fields == {"temperature_C", "p_total_mbar"}
    assert _internal_analytical_control_refusal(25.0, 0.0) is None
    assert _internal_analytical_control_refusal(
        -CELSIUS_TO_KELVIN_OFFSET, 1.0
    ) is not None


# Mutation B936_DROP_TYPED_OMISSION_NOTE removes the _sz_omit call from a
# targeted continue; the corresponding scenario must fail for its missing note.
def test_internal_analytical_inactive_and_parentless_metal_omissions_are_typed(
    monkeypatch,
):
    """Mutation B936_DROP_TYPED_OMISSION_NOTE restores both bare continues."""
    sim = _typed_omission_sim(
        metals={
            "Mg": {
                "consumer_status": "inactive",
                "parent_oxide": "MgO",
            },
            "Na": {"parent_oxide": ""},
        }
    )

    result = _run_typed_omission_sim(sim, monkeypatch)

    notes = {
        note["species"]: note
        for note in result.diagnostics["silent_zero_notes"]
    }
    assert result.status == "ok"
    assert result.vapor_pressures_Pa == {}
    assert notes["Mg"]["zero_because"] == ZeroBecause.REFUSED_UPSTREAM.value
    assert notes["Mg"]["doctrine_category"] == CATEGORY_REFUSE
    assert notes["Mg"]["field"] == "consumer_status"
    assert notes["Na"]["zero_because"] == ZeroBecause.MISSING_THERMO.value
    assert notes["Na"]["doctrine_category"] == CATEGORY_REFUSE
    assert notes["Na"]["field"] == "parent_oxide"
    assert result.diagnostics["silent_zero_notes"] == sim._silent_zero_notes


@pytest.mark.parametrize(
    "metals",
    ({}, {"Fe": {}}),
    ids=("missing", "empty"),
)
def test_internal_analytical_missing_or_empty_metal_row_is_typed(
    metals,
    monkeypatch,
):
    sim = _typed_omission_sim(metals=metals, species=("Fe",))

    result = _run_typed_omission_sim(sim, monkeypatch)

    assert result.status == "ok"
    assert result.vapor_pressures_Pa == {}
    note, = result.diagnostics["silent_zero_notes"]
    assert note["species"] == "Fe"
    assert note["zero_because"] == ZeroBecause.MISSING_THERMO.value
    assert note["doctrine_category"] == CATEGORY_REFUSE
    assert note["field"] == "metal_row"
    assert sim._silent_zero_notes == [note]


def test_internal_analytical_missing_antoine_intercepts_are_typed(monkeypatch):
    from engines.builtin import vapor_pressure as vapor_pressure_module
    from engines.builtin.vapor_pressure import FIT_TARGET_PSEUDO_VAPOROCK

    monkeypatch.setattr(
        vapor_pressure_module,
        "_is_noncertifying_pseudo_vapor_pressure_runtime",
        lambda *_args, **_kwargs: False,
    )
    sim = _typed_omission_sim(
        metals={
            "Mg": {
                "parent_oxide": "MgO",
                "fit_target": FIT_TARGET_PSEUDO_VAPOROCK,
                "antoine": {"B": 0.0, "C": 0.0},
            }
        },
        oxide_vapors={
            "SiO": {"antoine": {"B": 0.0, "C": 0.0}},
        },
    )

    result = _run_typed_omission_sim(sim, monkeypatch)

    notes = {
        note["species"]: note
        for note in result.diagnostics["silent_zero_notes"]
    }
    assert result.status == "ok"
    assert result.vapor_pressures_Pa == {}
    assert set(notes) == {"Mg", "SiO"}
    for species in ("Mg", "SiO"):
        assert notes[species]["zero_because"] == ZeroBecause.MISSING_COEFFICIENT.value
        assert notes[species]["doctrine_category"] == CATEGORY_REFUSE
        assert notes[species]["field"] == "antoine.A"
    assert result.diagnostics["silent_zero_notes"] == sim._silent_zero_notes
    assert any("species=Mg" in warning for warning in result.warnings)
    assert any("species=SiO" in warning for warning in result.warnings)


def test_internal_analytical_oxide_vapor_outside_fit_retains_flagged_value(
    monkeypatch,
):
    sim = _typed_omission_sim(
        oxide_vapors={
            "SiO": {
                "antoine": {"A": 1.0, "B": 0.0, "C": 0.0},
                "valid_range_K": [1100.0, 1200.0],
            }
        }
    )

    result = _run_typed_omission_sim(sim, monkeypatch)

    assert result.status == "ok"
    # SiO is now evaluated at the 1e-9 bar surface potential. Its -1/2 law
    # scales the 10 Pa reference by sqrt(1e-12 / 1e-9), instead of using the
    # fixture's 1e-12 bar headspace value and leaving that reference unchanged.
    assert result.vapor_pressures_Pa["SiO"] == pytest.approx(
        10.0 * math.sqrt(1e-12 / 1e-9)
    )
    notice = result.diagnostics["extrapolated_beyond_valid_range_K"]["SiO"]
    assert notice["reason"] == "out_of_domain_marked"
    assert notice["authority_level"] == "extrapolated"
    assert notice["certified_band"] == {"temperature_K": (1100., 1200.)}
    assert result.vapor_pressures_source["SiO"].startswith("builtin_extrapolation_limited")


def test_internal_analytical_nonpositive_metal_activity_numerator_is_typed(
    monkeypatch,
):
    from engines.builtin import vapor_pressure as vapor_pressure_module
    from engines.builtin.vapor_pressure import FIT_TARGET_PSEUDO_VAPOROCK

    monkeypatch.setattr(
        equilibrium_module,
        "ellingham_delta_g_kj_per_mol_o2",
        lambda *_args: -1e9,
    )
    monkeypatch.setattr(
        equilibrium_module,
        "ellingham_authority_limit",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        vapor_pressure_module,
        "_is_noncertifying_pseudo_vapor_pressure_runtime",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        vapor_pressure_module,
        "require_antoine_source_certified_temperature",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        vapor_pressure_module,
        "vapor_pressure_valid_range_K",
        lambda *_args, **_kwargs: None,
    )
    sim = _typed_omission_sim(
        metals={
            "Fe": {
                "parent_oxide": "FeO",
                "fit_target": FIT_TARGET_PSEUDO_VAPOROCK,
                "antoine": {"A": 1.0, "B": 0.0, "C": 0.0},
            }
        }
    )

    result = _run_typed_omission_sim(sim, monkeypatch)

    assert result.status == "ok"
    assert result.vapor_pressures_Pa == {}
    assert result.activity_coefficients == {"Fe": 1.0}
    note, = result.diagnostics["silent_zero_notes"]
    assert note["species"] == "Fe"
    assert note["zero_because"] == ZeroBecause.PROVEN_BELOW_THRESHOLD.value
    assert note["doctrine_category"] == CATEGORY_PROVEN_ZERO
    assert note["field"] == "metal_activity_numerator"
    assert result.diagnostics["silent_zero_notes"] == sim._silent_zero_notes


def test_internal_analytical_noncertifying_pseudo_fallback_is_typed(
    monkeypatch,
):
    from engines.builtin.vapor_pressure import FIT_TARGET_PSEUDO_VAPOROCK

    sim = _typed_omission_sim(
        metals={
            "Mg": {
                "parent_oxide": "MgO",
                "fit_target": FIT_TARGET_PSEUDO_VAPOROCK,
                "residual_dex": 1.0,
                "valid_range_K": [900.0, 950.0],
                "antoine": {"A": 1.0, "B": 0.0, "C": 0.0},
            }
        }
    )

    result = _run_typed_omission_sim(sim, monkeypatch)

    assert result.status == "ok"
    assert result.vapor_pressures_Pa == {}
    note, = result.diagnostics["silent_zero_notes"]
    assert note["species"] == "Mg"
    assert note["zero_because"] == ZeroBecause.REFUSED_UPSTREAM.value
    assert note["doctrine_category"] == CATEGORY_REFUSE
    assert note["field"] == "valid_range_K"
    assert any(
        warning.startswith("non_certifying_vapor_pressure_fallback_omitted:")
        for warning in result.warnings
    )
    assert result.diagnostics["silent_zero_notes"] == sim._silent_zero_notes


def test_internal_analytical_source_range_continues_but_missing_activity_refuses(
    monkeypatch,
):
    from engines.builtin.vapor_pressure import FIT_TARGET_PSEUDO_VAPOROCK

    sim = _typed_omission_sim(
        metals={
            "Mg": {
                "parent_oxide": "MgO",
                "fit_target": FIT_TARGET_PSEUDO_VAPOROCK,
                "extrapolation_policy": "refuse",
                "source_certified_range_K": [1100.0, 1200.0],
                "antoine": {"A": 1.0, "B": 0.0, "C": 0.0},
            }
        }
    )

    result = _run_typed_omission_sim(sim, monkeypatch)

    assert result.status == "ok"
    assert result.vapor_pressures_Pa == {}
    note, = result.diagnostics["silent_zero_notes"]
    assert note["species"] == "Mg"
    assert note["zero_because"] == ZeroBecause.MISSING_ACTIVITY.value
    assert note["doctrine_category"] == CATEGORY_REFUSE
    assert note["field"] == "oxide_activity"
    notice = result.diagnostics["extrapolated_beyond_valid_range_K"]["Mg"]
    assert notice["authority_level"] == "extrapolated"
    assert notice["certified_band"] == {"temperature_K": (1100., 1200.)}
    assert any(
        warning.startswith("metal_vapor_pressure_out_of_source_certified_range:")
        for warning in result.warnings
    )
    assert result.diagnostics["silent_zero_notes"] == sim._silent_zero_notes


_FEO_MELT_WT = {
    "SiO2": 50.0,
    "Al2O3": 15.0,
    "FeO": 12.0,
    "MgO": 10.0,
    "CaO": 13.0,
}


def _feo_equilibrium_sim(*, temperature_C, domain=None, with_domain_method=True):
    melt = SimpleNamespace(
        temperature_C=temperature_C,
        p_total_mbar=1.0,
        oxygen_reservoir=None,
        melt_fO2_log=-12.0,
        fO2_log=-12.0,
        composition_wt_pct=lambda: dict(_FEO_MELT_WT),
    )
    sim = SimpleNamespace(
        melt=melt,
        vapor_pressures={"metals": {}, "oxide_vapors": {}},
        _ELLINGHAM_THERMO=(),
        _headspace_transport_pO2_bar=lambda: 1e-12,
        _oxygen_numerical_floor_bar=lambda: 1e-12,
        atom_ledger=None,
    )
    if with_domain_method:
        sim.liquid_feo_domain_at = lambda _T_K: domain
    return sim


def test_subliquidus_equilibrium_carries_liquid_feo_notice_without_changing_activity():
    domain = LiquidFeODomain(
        below_liquidus=True,
        temperature_K=225.0 + 273.15,
        liquidus_T_C=1700.0,
        solidus_T_C=1050.0,
        source="gate_liquid_fraction",
    )
    plain = EquilibriumMixin._internal_analytical_equilibrium(
        _feo_equilibrium_sim(temperature_C=225.0, with_domain_method=False)
    )
    flagged = EquilibriumMixin._internal_analytical_equilibrium(
        _feo_equilibrium_sim(temperature_C=225.0, domain=domain)
    )
    plain_diag = plain.diagnostics["a_FeO_calphad"]
    flagged_diag = flagged.diagnostics["a_FeO_calphad"]

    assert flagged.status == "ok"
    assert flagged_diag["notices"] == [
        {
            "type": "typed_notice",
            "kind": "liquid_model_below_liquidus",
            "temperature_K": domain.temperature_K,
            "liquidus_T_C": 1700.0,
            "solidus_T_C": 1050.0,
            "source": "gate_liquid_fraction",
            "authority": "extrapolated",
        }
    ]
    assert {
        key: value for key, value in flagged_diag.items() if key != "notices"
    } == plain_diag
    assert flagged_diag["a_FeO_authoritative"] == plain_diag["a_FeO_authoritative"]
    assert flagged_diag["a_FeO_authoritative"] > 0.0
    assert "gamma_FeO" in flagged_diag


def test_above_liquidus_equilibrium_diagnostic_is_byte_identical():
    domain = LiquidFeODomain(
        below_liquidus=False,
        temperature_K=1600.0 + 273.15,
        liquidus_T_C=1200.0,
        solidus_T_C=1050.0,
        source="gate_liquid_fraction",
    )
    plain = EquilibriumMixin._internal_analytical_equilibrium(
        _feo_equilibrium_sim(temperature_C=1600.0, with_domain_method=False)
    )
    liquid = EquilibriumMixin._internal_analytical_equilibrium(
        _feo_equilibrium_sim(temperature_C=1600.0, domain=domain)
    )

    assert liquid.diagnostics == plain.diagnostics
    assert liquid.vapor_pressures_Pa == plain.vapor_pressures_Pa
    assert "notices" not in liquid.diagnostics["a_FeO_calphad"]


def _gate_host(curve, *, pinned=True):
    def _resolve(gate_authority=_RESOLVE_MELT_REDOX_GATE_AUTHORITY, **_kwargs):
        if gate_authority is _RESOLVE_MELT_REDOX_GATE_AUTHORITY:
            raise AssertionError("FeO domain started a liquidus solve")
        return curve

    host = SimpleNamespace(
        melt=SimpleNamespace(hour=1),
        _resolved_melt_redox_gate_authority=_resolve,
    )
    if pinned:
        host._melt_redox_gate_authority_tick_hour = 1
        host._melt_redox_gate_authority_this_tick = (
            curve
            if not isinstance(curve, dict)
            else {"disposition": "live", "curve": curve}
        )
    else:
        host._melt_redox_gate_authority_tick_hour = None
        host._melt_redox_gate_authority_this_tick = (
            _RESOLVE_MELT_REDOX_GATE_AUTHORITY
        )
    return host


def test_liquid_feo_domain_uses_silicate_liquidus_not_the_kress_floor():
    curve = {
        "liquidus_T_C": 1700.0,
        "solidus_T_C": 1050.0,
        "source": "gate_liquid_fraction",
    }
    host = _gate_host(curve)
    cold = PyrolysisSimulator.liquid_feo_domain_at(host, 225.0 + 273.15)
    assert cold.below_liquidus is True
    assert cold.liquidus_T_C == 1700.0
    assert cold.solidus_T_C == 1050.0
    assert cold.source == "gate_liquid_fraction"
    assert cold.temperature_K == pytest.approx(225.0 + 273.15)

    hot = PyrolysisSimulator.liquid_feo_domain_at(host, 1750.0 + 273.15)
    assert hot.below_liquidus is False

    at_liquidus_K = 1700.0 + 273.15
    at_curve = {
        "liquidus_T_C": at_liquidus_K - 273.15,
        "solidus_T_C": 1050.0,
        "source": "gate_liquid_fraction",
    }
    at = PyrolysisSimulator.liquid_feo_domain_at(_gate_host(at_curve), at_liquidus_K)
    assert at.below_liquidus is False

    # A fully molten melt between a sub-1200 liquidus and the Kress floor
    # is liquid for this model. The floor is a redox-capacity gate.
    low_liquidus = {
        "liquidus_T_C": 1100.0,
        "solidus_T_C": 900.0,
        "source": "gate_liquid_fraction",
    }
    between = PyrolysisSimulator.liquid_feo_domain_at(
        _gate_host(low_liquidus),
        1150.0 + 273.15,
    )
    assert between.below_liquidus is False


def test_liquid_feo_domain_floor_fallback_and_unresolved_gate():
    fallback = _MeltRedoxLiquidusFloorFallback(
        source="none:liquidus_unavailable",
        reason="test",
        liquidus_status="invalid",
    )
    host = _gate_host(fallback)
    floor_C = float(KRESS91_LIQUID_CALIBRATION_MIN_T_C)
    at_floor = PyrolysisSimulator.liquid_feo_domain_at(host, floor_C + 273.15)
    assert at_floor.below_liquidus is True
    assert at_floor.liquidus_T_C == floor_C
    assert at_floor.solidus_T_C == floor_C
    assert at_floor.source == _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE
    assert fallback.source == "none:liquidus_unavailable"
    above = PyrolysisSimulator.liquid_feo_domain_at(host, floor_C + 273.15 + 0.1)
    assert above.below_liquidus is False

    missing = PyrolysisSimulator.liquid_feo_domain_at(
        _gate_host(None),
        1700.0 + 273.15,
    )
    assert missing.below_liquidus is True
    assert missing.liquidus_T_C is None
    assert missing.solidus_T_C is None
    assert missing.source == "none:liquidus_gate_in_progress"

    unresolved = PyrolysisSimulator.liquid_feo_domain_at(
        _gate_host({"source": "broken"}),
        1700.0 + 273.15,
    )
    assert unresolved.below_liquidus is True
    assert unresolved.source == "none:liquidus_unresolved"

    not_established = PyrolysisSimulator.liquid_feo_domain_at(
        _gate_host(
            {
                "liquidus_T_C": 1700.0,
                "solidus_T_C": 1050.0,
                "source": "gate_liquid_fraction",
            },
            pinned=False,
        ),
        1750.0 + 273.15,
    )
    assert not_established is None


def _established_record_host(record):
    """Real establish, resolver, and curve unwrap. Only the record is supplied."""

    host = SimpleNamespace(
        melt=SimpleNamespace(hour=1),
        _melt_redox_liquidus_gate_record=lambda **_kwargs: record,
    )
    host._melt_redox_liquidus_gate_curve = (
        PyrolysisSimulator._melt_redox_liquidus_gate_curve.__get__(host)
    )
    host._melt_redox_gate_record_is_admitted = (
        PyrolysisSimulator._melt_redox_gate_record_is_admitted.__get__(host)
    )
    return host


def test_replay_and_originless_live_curves_feed_the_liquid_feo_domain():
    """A resolved curve admission will not pin still has a liquidus.

    The redox resolver collapses both records to None. That None is not the
    FeO domain: 225 C is below the curve, and a temperature above the curve
    is not.
    """

    replay = {
        "disposition": "replay",
        "origin": None,
        "repair_notices": [],
        "curve": {
            "source": "gate_liquid_fraction",
            "liquidus_T_C": 1700.0,
            "solidus_T_C": 1050.0,
        },
    }
    replay_host = _established_record_host(replay)
    established = (
        PyrolysisSimulator._establish_melt_redox_gate_authority_for_current_hour(
            replay_host
        )
    )
    assert established["liquidus_T_C"] == 1700.0
    assert (
        PyrolysisSimulator._melt_redox_gate_record_is_admitted(
            replay_host, replay
        )
        is False
    )
    resolved = PyrolysisSimulator._resolved_melt_redox_gate_authority(
        replay_host
    )
    assert resolved["liquidus_T_C"] == 1700.0
    assert (
        PyrolysisSimulator._resolved_melt_redox_gate_authority(
            replay_host, replay
        )
        is None
    )
    assert getattr(
        replay_host, "_melt_redox_gate_authority_tick_hour", None
    ) is None
    cold = PyrolysisSimulator.liquid_feo_domain_at(
        replay_host, 225.0 + 273.15
    )
    assert cold.below_liquidus is True
    assert cold.liquidus_T_C == 1700.0
    assert cold.solidus_T_C == 1050.0
    assert cold.source == "gate_liquid_fraction"
    hot = PyrolysisSimulator.liquid_feo_domain_at(
        replay_host, 1750.0 + 273.15
    )
    assert hot.below_liquidus is False
    assert hot.liquidus_T_C == 1700.0
    assert hot.source == "gate_liquid_fraction"

    floor_C = float(KRESS91_LIQUID_CALIBRATION_MIN_T_C)
    liquidus_T_C = floor_C + float(_KRESS91_FLOOR_OPEN_SIDE_C)
    live = {
        "disposition": "live",
        "origin": None,
        "repair_notices": [],
        "curve": {
            "source": _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE,
            "liquidus_T_C": liquidus_T_C,
            "solidus_T_C": floor_C,
        },
    }
    live_host = _established_record_host(live)
    live_curve = (
        PyrolysisSimulator._establish_melt_redox_gate_authority_for_current_hour(
            live_host
        )
    )
    assert live_curve["liquidus_T_C"] == pytest.approx(liquidus_T_C)
    assert (
        PyrolysisSimulator._melt_redox_gate_record_is_admitted(live_host, live)
        is False
    )
    live_resolved = PyrolysisSimulator._resolved_melt_redox_gate_authority(
        live_host
    )
    assert live_resolved["source"] == _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE
    assert (
        PyrolysisSimulator._resolved_melt_redox_gate_authority(live_host, live)
        is None
    )
    below_floor = PyrolysisSimulator.liquid_feo_domain_at(
        live_host, 225.0 + 273.15
    )
    assert below_floor.below_liquidus is True
    assert below_floor.liquidus_T_C == pytest.approx(liquidus_T_C)
    assert below_floor.solidus_T_C == pytest.approx(floor_C)
    assert below_floor.source == _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE
    above_floor = PyrolysisSimulator.liquid_feo_domain_at(
        live_host, 1750.0 + 273.15
    )
    assert above_floor.below_liquidus is False
    assert above_floor.source == _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE


def _oxide_batch_sim() -> PyrolysisSimulator:
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    return PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"SiO2": 60.0, "FeO": 40.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )


def _replay_record(*, liquidus_T_C: float) -> dict:
    return {
        "disposition": "replay",
        "origin": None,
        "repair_notices": [],
        "curve": {
            "source": "gate_liquid_fraction",
            "liquidus_T_C": liquidus_T_C,
            "solidus_T_C": 1050.0,
        },
    }


def test_load_batch_drops_a_retained_liquid_feo_curve():
    """A second batch must not keep the previous batch's liquidus.

    load_batch sets melt.hour back to 0. The retained curve is cleared
    with the redox pin, so the new establish is what the domain reads.
    """

    sim = _oxide_batch_sim()
    sim._melt_redox_liquidus_gate_record = lambda **_kwargs: _replay_record(
        liquidus_T_C=1700.0
    )
    sim.load_batch("oxide")
    first = (
        PyrolysisSimulator._establish_melt_redox_gate_authority_for_current_hour(
            sim
        )
    )
    assert first["liquidus_T_C"] == 1700.0
    assert sim._liquid_feo_resolved_curve_hour == sim.melt.hour

    sim.load_batch("oxide")
    assert sim.melt.hour == 0
    assert sim._liquid_feo_resolved_curve_hour is None
    assert sim._liquid_feo_resolved_curve is not None
    fallback = _MeltRedoxLiquidusFloorFallback(
        source="none:liquidus_unavailable",
        reason="test",
        liquidus_status="invalid",
    )
    sim._melt_redox_liquidus_gate_record = lambda **_kwargs: fallback
    second = (
        PyrolysisSimulator._establish_melt_redox_gate_authority_for_current_hour(
            sim
        )
    )
    assert isinstance(second, _MeltRedoxLiquidusFloorFallback)
    at_1300 = PyrolysisSimulator.liquid_feo_domain_at(sim, 1300.0 + 273.15)
    assert at_1300.below_liquidus is False
    assert at_1300.source == _KRESS91_LIQUID_CALIBRATION_FLOOR_SOURCE
    assert at_1300.liquidus_T_C == float(KRESS91_LIQUID_CALIBRATION_MIN_T_C)

    sim._melt_redox_liquidus_gate_record = lambda **_kwargs: _replay_record(
        liquidus_T_C=1700.0
    )
    sim.load_batch("oxide")
    replay = (
        PyrolysisSimulator._establish_melt_redox_gate_authority_for_current_hour(
            sim
        )
    )
    assert replay["liquidus_T_C"] == 1700.0
    pinned = {
        "disposition": "live",
        "origin": None,
        "repair_notices": [],
        "curve": {
            "source": "gate_liquid_fraction",
            "liquidus_T_C": 1700.0,
            "solidus_T_C": 1050.0,
        },
    }
    sim.load_batch("oxide")
    sim._melt_redox_liquidus_gate_record = lambda **_kwargs: pinned
    sim._melt_redox_gate_record_is_admitted = lambda _value, **_kwargs: True
    pinned_curve = (
        PyrolysisSimulator._establish_melt_redox_gate_authority_for_current_hour(
            sim
        )
    )
    assert pinned_curve["liquidus_T_C"] == 1700.0
    assert sim._melt_redox_gate_authority_tick_hour == sim.melt.hour
    assert sim._liquid_feo_resolved_curve_hour is None
    at_1500 = PyrolysisSimulator.liquid_feo_domain_at(sim, 1500.0 + 273.15)
    assert at_1500.below_liquidus is True
    assert at_1500.liquidus_T_C == 1700.0
    assert at_1500.source == "gate_liquid_fraction"


def test_native_fe_saturation_carries_the_liquidus_notice_on_both_returns():
    comp = dict(_FEO_MELT_WT)
    cold_K = 225.0 + 273.15
    below = LiquidFeODomain(
        below_liquidus=True,
        temperature_K=cold_K,
        liquidus_T_C=1700.0,
        solidus_T_C=1050.0,
        source="gate_liquid_fraction",
    )
    above = LiquidFeODomain(
        below_liquidus=False,
        temperature_K=1600.0 + 273.15,
        liquidus_T_C=1200.0,
        solidus_T_C=1050.0,
        source="gate_liquid_fraction",
    )

    def _host(domain):
        return SimpleNamespace(
            _feot_equivalent_wt_pct=PyrolysisSimulator._feot_equivalent_wt_pct,
            liquid_feo_domain_at=(None if domain is None else (lambda _T: domain)),
        )

    def _state(domain, *, T_K, fO2_log):
        host = _host(domain)
        if domain is None:
            del host.liquid_feo_domain_at
        return PyrolysisSimulator._native_fe_saturation_state(
            host,
            comp,
            fe3_over_sigma_fe=0.1,
            T_K=T_K,
            pressure_bar=1e-6,
            fO2_log=fO2_log,
        )

    cold_fO2 = -14.0
    flagged = _state(below, T_K=cold_K, fO2_log=cold_fO2)
    plain = _state(None, T_K=cold_K, fO2_log=cold_fO2)
    assert flagged["notices"][0]["kind"] == "liquid_model_below_liquidus"
    assert flagged["notices"][0]["authority"] == "extrapolated"
    assert flagged["native_fe_activity"] == plain["native_fe_activity"]
    assert {
        key: value for key, value in flagged.items() if key != "notices"
    } == plain
    assert "notices" not in plain

    hot_K = 1600.0 + 273.15
    hot_plain = _state(None, T_K=hot_K, fO2_log=-10.0)
    hot_liquid = _state(above, T_K=hot_K, fO2_log=-10.0)
    assert hot_liquid == hot_plain
    assert "notices" not in hot_liquid

    zero = _state(below, T_K=cold_K, fO2_log=1000.0)
    zero_plain = _state(None, T_K=cold_K, fO2_log=1000.0)
    assert zero["native_fe_activity_source"] == "unavailable:no_positive_a_FeO"
    assert zero["notices"][0]["kind"] == "liquid_model_below_liquidus"
    assert {
        key: value for key, value in zero.items() if key != "notices"
    } == zero_plain
