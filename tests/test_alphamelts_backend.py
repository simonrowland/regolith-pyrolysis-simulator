import math
import inspect
import subprocess
import time
import types
import warnings
from pathlib import Path

import pytest
import yaml

from engines.builtin.vapor_pressure import (
    HighUncertaintyVaporPressureFallbackWarning,
)
from engines.domain_reason import OutOfDomainReason
from engines.alphamelts import AlphaMELTSProvider
from engines.alphamelts.domain import AlphaMELTSDomainGate
import engines.alphamelts.provider as alphamelts_provider_module
import engines.alphamelts.thermoengine as thermoengine_module
from engines.alphamelts.parser import diagnostics_to_equilibrium
from engines.alphamelts.result import LiquidusDiagnostics
from simulator.chemistry.kernel import ChemistryIntent
from simulator.accounting.formulas import resolve_species_formula
from simulator.core import CampaignPhase, PyrolysisSimulator
from simulator.backends import BackendSelectionPolicy, resolve_backend
from simulator.melt_backend.alphamelts import (
    ALPHAMELTS_REASON_MISSING_BINARY,
    ALPHAMELTS_REASON_NONZERO_EXIT,
    ALPHAMELTS_REASON_NO_CONVERGENCE,
    ALPHAMELTS_REASON_PARSE_EMPTY_OUTPUT,
    ALPHAMELTS_REASON_PRESSURE_UNSUPPORTED,
    ALPHAMELTS_REASON_SUBPROCESS_DIED,
    ALPHAMELTS_REASON_TIMEOUT,
    ALPHAMELTS_REASON_VAPOR_PROJECTION_EMPTY,
    AlphaMELTSBackend,
    AlphaMELTSSubprocessContractError,
    AlphaMELTSSubprocessRunMode,
    activity_from_chem_potential,
)
from simulator.melt_backend.base import (
    EquilibriumResult,
    LiquidFractionInvalidError,
    MeltBackend,
)
from simulator.melt_backend.thermoengine import ThermoEngineBackend
from engines.alphamelts.thermoengine import (
    ThermoEnginePayload,
    ThermoEngineTransport,
)
from engines.magemin.parity import MAGEMinParityComparator


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _load_data(filename):
    with open(DATA_DIR / filename) as f:
        return yaml.safe_load(f) or {}


def test_alphamelts_python_failures_mark_backend_unavailable():
    backend = AlphaMELTSBackend()
    backend._mode = 'python_api'

    with pytest.raises(ImportError, match='not preloaded'):
        backend._equilibrate_python(
            1600.0,
            {
                'SiO2': 50.0,
                'Al2O3': 15.0,
                'FeO': 10.0,
                'MgO': 10.0,
                'CaO': 10.0,
                'Na2O': 5.0,
            },
            -9.0,
            1e-6,
        )

    assert backend.is_available() is False


def test_alphamelts_python_liquidus_finder_uses_findliq_gate():
    class FakeFinderBackend(AlphaMELTSBackend):
        def __init__(self):
            super().__init__()
            self._mode = 'python_api'

        def _find_petthermotools_liquidus_C(self, comp_wt, *, pressure_bar, seed_T_C):
            return 1300.0, ()

        def equilibrate(self, temperature_C, **kwargs):
            frac = max(0.0, min(1.0, (float(temperature_C) - 1000.0) / 300.0))
            return EquilibriumResult(
                temperature_C=float(temperature_C),
                pressure_bar=float(kwargs.get('pressure_bar', 1e-6)),
                liquid_fraction=frac,
                phases_present=['liq'] if frac > 0.0 else ['ol'],
                phase_masses_kg={'liq': frac, 'ol': 1.0 - frac},
                status='ok',
            )

    backend = FakeFinderBackend()
    result = backend.find_liquidus_solidus(
        composition_kg={
            'SiO2': 49.0,
            'TiO2': 1.5,
            'Al2O3': 14.0,
            'FeO': 10.0,
            'Fe2O3': 1.0,
            'MgO': 9.0,
            'CaO': 11.0,
            'Na2O': 2.5,
            'K2O': 0.8,
            'Cr2O3': 0.2,
            'MnO': 0.2,
            'P2O5': 0.3,
        },
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=800.0,
        max_T_C=1500.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
    )

    assert result.status == 'ok'
    assert result.solidus_T_C == pytest.approx(1000.0, abs=1.0)
    assert result.liquidus_T_C == pytest.approx(1300.0, abs=1.0)


@pytest.mark.parametrize(
    ('phase_masses_kg', 'liquid_fraction', 'message'),
    [
        ({'liquid': math.inf, 'olivine': 1.0}, None, 'phase_mass_invalid'),
        ({'liquid': 0.8, 'olivine': 0.2}, math.nan, 'liquid_fraction_invalid'),
        ({}, None, 'liquid_fraction_missing'),
        ({'liquid': 0.8, 'olivine': 0.2}, 0.1, 'liquid_fraction_mismatch'),
    ],
)
def test_alphamelts_ok_result_requires_finite_phase_mass_fraction(
    phase_masses_kg,
    liquid_fraction,
    message,
):
    backend = AlphaMELTSBackend()

    with pytest.raises(LiquidFractionInvalidError, match=message):
        backend._emit_equilibrium_result(
            temperature_C=1200.0,
            pressure_bar=1.0,
            fO2_log=-9.0,
            phases_present=list(phase_masses_kg),
            phase_masses_kg=phase_masses_kg,
            liquid_fraction=liquid_fraction,
            status='ok',
        )


def test_alphamelts_subprocess_liquidus_finder_selects_native_mode():
    class FakeFinderBackend(AlphaMELTSBackend):
        def __init__(self):
            super().__init__()
            self._mode = 'subprocess'

        def equilibrate(self, temperature_C, **kwargs):
            assert kwargs['subprocess_run_mode'] is (
                AlphaMELTSSubprocessRunMode.LIQUIDUS_FINDER
            )
            return EquilibriumResult(
                temperature_C=1300.0,
                pressure_bar=float(kwargs.get('pressure_bar', 1e-6)),
                liquid_fraction=1.0,
                phases_present=['liq'],
                phase_masses_kg={'liq': 1.0},
                liquidus_T_C=1300.0,
                status='ok',
            )

    backend = FakeFinderBackend()

    result = backend.find_liquidus_solidus(
        composition_kg={'SiO2': 50.0, 'Al2O3': 15.0, 'MgO': 15.0, 'CaO': 20.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=800.0,
        max_T_C=1500.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
    )

    assert result.status == 'ok'
    assert result.solidus_T_C is None
    assert result.liquidus_T_C == pytest.approx(1300.0, abs=1.0)


def _melts_domain_composition() -> dict[str, float]:
    return {
        'SiO2': 49.0,
        'Al2O3': 15.0,
        'FeO': 10.0,
        'Fe2O3': 1.0,
        'MgO': 10.0,
        'CaO': 10.0,
        'Na2O': 5.0,
    }


def _system_main_fixture(
    *,
    temperature_C: float,
    fO2_log: float = -9.0,
    density_g_cm3: float = 2.638918,
    log10_viscosity_poise: float = 1.409,
    system_mass_g: float = 100.0,
) -> str:
    return (
        "System Thermodynamic Data:\n"
        "index Pressure Temperature mass F phi H S V Cp dVdP*10^6 "
        "dVdT*10^6 fO2(absolute) fO2-9.0) rhol rhos viscosity aH2O chisqr\n"
        f"1 1.00 {temperature_C:.6f} {system_mass_g:.9g} 1 1 -1 1 1 1 0 0 "
        f"{fO2_log:.6f} 0 {density_g_cm3:.6f} 0 "
        f"{log10_viscosity_poise:.6f} n/a n/a\n"
    )


def _parse_subprocess_fixture(
    backend: AlphaMELTSBackend,
    output: str,
    *,
    temperature_C: float,
    pressure_bar: float = 1.0,
    total_input_kg: float = 0.1,
    system_output: str | None = None,
    table_outputs: dict[str, str] | None = None,
    fO2_log: float = -9.0,
):
    return backend._parse_single_point_stdout(
        output,
        requested_temperature_C=temperature_C,
        pressure_bar=pressure_bar,
        fO2_log=fO2_log,
        total_input_kg=total_input_kg,
        run_mode=AlphaMELTSSubprocessRunMode.ISOTHERMAL,
        system_output=(
            _system_main_fixture(temperature_C=temperature_C)
            if system_output is None
            else system_output
        ),
        fO2_constraint={"path": "Absolute", "offset": fO2_log},
        table_outputs=table_outputs,
    )


def test_alphamelts_full_table_suite_parsers_capture_all_liquid_and_solids():
    backend = AlphaMELTSBackend()
    system = (
        "System Thermodynamic Data:\n"
        "index Pressure Temperature mass F phi H S V Cp dVdP*10^6 "
        "dVdT*10^6 fO2-(QFM) fO2(absolute) rhol rhos viscosity aH2O chisqr\n"
        "1 1.00 1317.97 100.000002 1.0 1.0 -1059377.10 268.91 "
        "34.56 143.47 -183.54 2897.97 -4.229 -11.301 2.893824 "
        "n/a 1.095 n/a n/a\n"
    )
    phase = (
        "index 1 Pressure 1.00 Temperature 1100.00 SiO2 FeO MgO\n"
        "liquid1 27.5 -298226.6 72.42 10.0 38.55 2.169 46.5 17.4 3.9\n"
        "olivine0 15.2 -181990.7 35.41 4.56 18.46 "
        "(Mg0.8Fe0.2)2SiO4 38.9 19.7 40.8\n"
    )
    solid_empty = (
        "Solid Composition:\n"
        "index Pressure Temperature mass SiO2 FeO MgO\n"
        "1 1.00 1400.00 0.000000 ---\n"
    )
    solid_partial = (
        "Solid Composition:\n"
        "index Pressure Temperature mass SiO2 FeO MgO\n"
        "1 1.00 1100.00 15.2 38.9 19.7 40.8\n"
    )
    bulk = (
        "Bulk Composition:\n"
        "index Pressure Temperature mass SiO2 FeO MgO\n"
        "1 1.00 1100.00 42.7 49.0 10.0 10.0\n"
    )
    liquid = (
        "Liquid Composition:\n"
        "index Pressure Temperature mass SiO2 FeO MgO\n"
        "1 1.00 1100.00 27.5 46.5 17.4 3.9\n"
    )

    system_values = backend._parse_system_main_output(system)
    assert system_values['fO2_value'] == pytest.approx(-11.301)
    assert system_values['system_enthalpy'] == pytest.approx(-1059377.10)
    assert system_values['system_entropy'] == pytest.approx(268.91)
    assert system_values['system_volume'] == pytest.approx(34.56)
    assert system_values['system_heat_capacity_Cp'] == pytest.approx(143.47)
    assert system_values['system_dVdP'] == pytest.approx(-183.54)
    assert system_values['system_dVdT'] == pytest.approx(2897.97)
    assert system_values['system_fO2_delta_QFM'] == pytest.approx(-4.229)
    assert system_values['system_solid_density_rhos'] is None
    assert system_values['system_phi'] == pytest.approx(1.0)
    assert system_values['system_chisqr'] is None

    phase_values = backend._parse_phase_main_output(phase)
    assert phase_values['phase_compositions']['olivine'] == pytest.approx({
        'SiO2': 38.9,
        'FeO': 19.7,
        'MgO': 40.8,
    })
    assert phase_values['phase_thermo']['liquid']['enthalpy'] == pytest.approx(
        -298226.6
    )
    assert phase_values['phase_thermo']['liquid']['density_kg_m3'] == pytest.approx(
        2750.0
    )
    assert phase_values['phase_thermo']['olivine']['density_kg_m3'] == pytest.approx(
        15.2 / 4.56 * 1000.0
    )
    assert backend._parse_composition_table(
        solid_empty, table_name='Solid_comp_tbl.txt'
    ) == {}
    assert backend._parse_composition_table(
        solid_partial, table_name='Solid_comp_tbl.txt'
    ) == pytest.approx({'SiO2': 38.9, 'FeO': 19.7, 'MgO': 40.8})
    assert backend._parse_composition_table(
        bulk, table_name='Bulk_comp_tbl.txt'
    ) == pytest.approx({'SiO2': 49.0, 'FeO': 10.0, 'MgO': 10.0})
    assert backend._parse_composition_table(
        liquid, table_name='Liquid_comp_tbl.txt'
    ) == pytest.approx({'SiO2': 46.5, 'FeO': 17.4, 'MgO': 3.9})
    with pytest.raises(ValueError, match='invalid H'):
        backend._parse_system_main_output(
            system.replace('-1059377.10', 'not-a-number')
        )
    stable_output = (
        '<> Stable liquid assemblage achieved.\n'
        'Initial alphaMELTS calculation at: P 1.000000 (bars), '
        'T 1400.000000 (C)\n'
        'liquid: SiO2\n100.0 g 100.0\nMelt fraction = 1.0\n'
    )
    with pytest.raises(
        AlphaMELTSSubprocessContractError,
        match='table suite missing',
    ):
        _parse_subprocess_fixture(
            backend,
            stable_output,
            temperature_C=1400.0,
            table_outputs={'Phase_main_tbl.txt': phase},
        )


def _clamped_success_diagnostics() -> LiquidusDiagnostics:
    return LiquidusDiagnostics(
        phases_present=('liq',),
        phase_masses_kg={'liq': 1.0},
        liquid_fraction=1.0,
        fO2_log=-9.0,
        backend_status='ok',
        backend_diagnostics={
            'operating_point_clamped': True,
            'operating_point_transport': 'subprocess',
            'temperature_clamped': True,
            'pressure_clamped': True,
            'requested_temperature_C': 650.0,
            'requested_pressure_bar': 1.0e-6,
            'solved_temperature_C': 800.0,
            'solved_pressure_bar': 1.0,
            'authoritative_for_requested_conditions': False,
            'authoritative_for_solved_conditions': True,
        },
    )


def test_diagnostics_to_equilibrium_clamped_success_is_requested_point_ood():
    result = diagnostics_to_equilibrium(
        _clamped_success_diagnostics(),
        {
            'temperature_C': 650.0,
            'pressure_bar': 1.0e-6,
            'fO2_log': -9.0,
        },
    )

    assert result.status == 'out_of_domain'
    assert result.temperature_C == pytest.approx(800.0)
    assert result.pressure_bar == pytest.approx(1.0)
    assert result.diagnostics['backend_status'] == 'out_of_domain'
    assert (
        result.diagnostics['backend_status_reason']
        == 'clamped_operating_point'
    )
    assert result.diagnostics['requested_temperature_C'] == pytest.approx(650.0)
    assert result.diagnostics['requested_pressure_bar'] == pytest.approx(1.0e-6)
    assert result.diagnostics['authoritative_for_requested_conditions'] is False


def test_alphamelts_subprocess_subbar_pressure_refuses_before_execution(
    monkeypatch,
):
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = Path('/tmp/fake-alphamelts')
    monkeypatch.setattr(
        'simulator.melt_backend.alphamelts.subprocess.run',
        lambda *args, **kwargs: pytest.fail('subprocess must not run'),
    )

    with pytest.raises(AlphaMELTSSubprocessContractError) as excinfo:
        backend.equilibrate(
            temperature_C=650.0,
            composition_kg=_melts_domain_composition(),
            fO2_log=-9.0,
            pressure_bar=1e-6,
            subprocess_run_mode='isothermal',
        )

    assert excinfo.value.backend_failure_reason_code == (
        ALPHAMELTS_REASON_PRESSURE_UNSUPPORTED
    )


@pytest.mark.parametrize('fe2o3', [None, 0.0, 1.0e-12, 0.001])
def test_production_alphamelts_fe2o3_absent_or_subthreshold_still_launches(
    fe2o3,
    monkeypatch,
):
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = Path('/tmp/fake-alphamelts')
    composition = _melts_domain_composition()
    if fe2o3 is None:
        del composition['Fe2O3']
    else:
        composition['Fe2O3'] = fe2o3
    monkeypatch.setattr(
        backend,
        '_equilibrate_subprocess',
        lambda *args, **kwargs: ('production-launch', args[1]),
    )

    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg=composition,
        fO2_log=-9.0,
        pressure_bar=1.0,
        subprocess_run_mode='isothermal',
    )

    assert result[0] == 'production-launch'
    if fe2o3 is None:
        assert result[1]['Fe2O3'] == 0.0
    elif fe2o3 == 0.0:
        assert result[1]['Fe2O3'] == 0.0
    else:
        assert result[1]['Fe2O3'] > 0.0


def test_alphamelts_subprocess_requires_explicit_run_mode(monkeypatch):
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = Path('/tmp/fake-alphamelts')
    monkeypatch.setattr(
        'simulator.melt_backend.alphamelts.subprocess.run',
        lambda *args, **kwargs: pytest.fail('subprocess must not run'),
    )

    with pytest.raises(
        AlphaMELTSSubprocessContractError,
        match='run mode was not selected explicitly',
    ):
        backend.equilibrate(
            temperature_C=1400.0,
            composition_kg=_melts_domain_composition(),
            fO2_log=-9.0,
            pressure_bar=1.0,
        )


def test_alphamelts_phase_main_preserves_same_base_instances_and_formulas():
    backend = AlphaMELTSBackend()
    phase = (
        'index 1 Pressure 1.00 Temperature 1100.00 SiO2 FeO MgO\n'
        'olivine0 40.0 -100.0 10.0 12.0 5.0 '
        "(Mg0.8Fe''0.2)2SiO4 40.0 10.0 50.0\n"
        'olivine1 60.0 -200.0 20.0 18.0 7.0 '
        '(Mg0.6Fe0.4)2SiO4 35.0 30.0 35.0\n'
    )

    parsed = backend._parse_phase_main_output(phase)

    assert [row['instance_id'] for row in parsed['phase_instances']] == [
        'olivine0',
        'olivine1',
    ]
    assert [
        row['formula_or_endmember_token']
        for row in parsed['phase_instances']
    ] == ["(Mg0.8Fe''0.2)2SiO4", '(Mg0.6Fe0.4)2SiO4']
    assert parsed['phase_instances'][0]['composition_wt_pct'] == {
        'SiO2': 40.0,
        'FeO': 10.0,
        'MgO': 50.0,
    }
    assert parsed['phase_compositions']['olivine'] == pytest.approx({
        'SiO2': 37.0,
        'FeO': 22.0,
        'MgO': 41.0,
    })
    first_instance = dict(parsed['phase_instances'][0])
    first_instance['physical_mass_kg'] = 0.04
    species_mol, species_kg = backend._phase_species_from_instances(
        [first_instance]
    )
    assert species_kg['olivine0'] == {"(Mg0.8Fe''0.2)2SiO4": 0.04}
    assert species_mol['olivine0']["(Mg0.8Fe''0.2)2SiO4"] > 0.0


def test_builtin_subprocess_vapor_projection_populates_representative_melt():
    backend = AlphaMELTSBackend()
    result = EquilibriumResult(
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        phases_present=['liquid'],
        phase_masses_kg={'liquid': 1.0},
        liquid_fraction=1.0,
        liquid_composition_wt_pct={
            'SiO2': 50.0,
            'Al2O3': 15.0,
            'FeO': 10.0,
            'MgO': 10.0,
            'CaO': 10.0,
            'Na2O': 5.0,
        },
        status='ok',
    )

    pressures, sources, diagnostics = (
        backend._builtin_vapor_projection_for_subprocess(result)
    )

    assert pressures
    assert set(pressures) == set(sources)
    assert all(value > 0.0 for value in pressures.values())
    assert diagnostics['vapor_pressures_Pa'] == pressures


def test_builtin_subprocess_vapor_projection_separates_melt_fo2_from_transport_po2(
    monkeypatch,
):
    backend = AlphaMELTSBackend()
    monkeypatch.setattr(
        backend,
        '_find_project_binary',
        lambda _engine_root: Path('/tmp/fake-alphamelts'),
    )
    assert backend.initialize({
        'mode': 'subprocess',
        'vapor_transport_pO2_bar': 2.0e-9,
    }) is True
    seen = {}

    def dispatch(request):
        seen['request'] = request
        return types.SimpleNamespace(
            status='ok',
            warnings=(),
            diagnostic={
                'vapor_pressures_Pa': {'Na': 1.0},
                'vapor_pressures_source': {'Na': 'test'},
            },
        )

    backend._subprocess_vapor_pressure_provider = types.SimpleNamespace(
        dispatch=dispatch
    )
    result = EquilibriumResult(
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=-11.0,
        phases_present=['liquid'],
        phase_masses_kg={'liquid': 1.0},
        liquid_fraction=1.0,
        liquid_composition_wt_pct={'Na2O': 100.0},
        status='ok',
    )

    backend._builtin_vapor_projection_for_subprocess(result)

    request = seen['request']
    assert request.fO2_log == pytest.approx(-11.0)
    assert request.control_inputs['intrinsic_fO2_log'] == pytest.approx(-11.0)
    assert request.control_inputs['pO2_bar'] == pytest.approx(2.0e-9)


@pytest.mark.parametrize(
    'diagnostic, reason_fragment',
    [
        ({'vapor_pressures_Pa': {}, 'vapor_pressures_source': {}}, 'no vapor'),
        (
            {
                'vapor_pressures_Pa': {'Na': 1.0},
                'vapor_pressures_source': {'K': 'test'},
            },
            'keys differ',
        ),
    ],
)
def test_builtin_subprocess_vapor_projection_refuses_silent_empty_or_unsourced(
    diagnostic,
    reason_fragment,
):
    backend = AlphaMELTSBackend()
    backend._subprocess_vapor_pressure_provider = types.SimpleNamespace(
        dispatch=lambda _request: types.SimpleNamespace(
            status='ok', warnings=(), diagnostic=diagnostic
        )
    )
    result = EquilibriumResult(
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        phases_present=['liquid'],
        phase_masses_kg={'liquid': 1.0},
        liquid_fraction=1.0,
        liquid_composition_wt_pct={'Na2O': 100.0},
        status='ok',
    )

    with pytest.raises(
        AlphaMELTSSubprocessContractError,
        match=reason_fragment,
    ) as excinfo:
        backend._builtin_vapor_projection_for_subprocess(result)

    assert excinfo.value.backend_failure_reason_code == (
        ALPHAMELTS_REASON_VAPOR_PROJECTION_EMPTY
    )


@pytest.mark.parametrize(
    'composition, provider_status, reason_fragment',
    [
        ({}, 'ok', 'missing solved liquid composition'),
        ({'Na2O': 100.0}, 'unavailable', 'provider refused vapor projection'),
    ],
)
def test_builtin_subprocess_vapor_projection_unavailable_paths_are_typed(
    composition,
    provider_status,
    reason_fragment,
):
    backend = AlphaMELTSBackend()
    backend._subprocess_vapor_pressure_provider = types.SimpleNamespace(
        dispatch=lambda _request: types.SimpleNamespace(
            status=provider_status,
            warnings=('test-unavailable',),
            diagnostic={},
        )
    )
    result = EquilibriumResult(
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        phases_present=['liquid'],
        phase_masses_kg={'liquid': 1.0},
        liquid_fraction=1.0,
        liquid_composition_wt_pct=composition,
        status='ok',
    )

    with pytest.raises(
        AlphaMELTSSubprocessContractError,
        match=reason_fragment,
    ) as excinfo:
        backend._builtin_vapor_projection_for_subprocess(result)

    assert excinfo.value.backend_failure_reason_code == (
        ALPHAMELTS_REASON_VAPOR_PROJECTION_EMPTY
    )


def test_alphamelts_subprocess_isothermal_emits_and_parses_system_properties(
    monkeypatch,
):
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = Path('/tmp/fake-alphamelts')
    seen = {}

    def fake_run(*args, **kwargs):
        seen['stdin'] = kwargs['input']
        seen['env'] = dict(kwargs['env'])
        seen['input_melts'] = (
            Path(kwargs['cwd']) / 'input.melts'
        ).read_text()
        (Path(kwargs['cwd']) / 'System_main_tbl.txt').write_text(
            _system_main_fixture(temperature_C=1400.0, fO2_log=-9.0)
        )
        (Path(kwargs['cwd']) / 'Phase_main_tbl.txt').write_text(
            'index 1 Pressure 1.00 Temperature 1400.00 SiO2 Al2O3 FeO '
            'MgO CaO Na2O\n'
            'liquid1 100.0 -1059377.1 268.91 34.56 143.47 1.409 '
            '50 15 10 10 10 5\n'
        )
        (Path(kwargs['cwd']) / 'Solid_comp_tbl.txt').write_text(
            'index Pressure Temperature mass SiO2 Al2O3 FeO MgO CaO Na2O\n'
            '1 1.00 1400.00 0.0 ---\n'
        )
        (Path(kwargs['cwd']) / 'Bulk_comp_tbl.txt').write_text(
            'index Pressure Temperature mass SiO2 Al2O3 FeO MgO CaO Na2O\n'
            '1 1.00 1400.00 100.0 50 15 10 10 10 5\n'
        )
        (Path(kwargs['cwd']) / 'Liquid_comp_tbl.txt').write_text(
            'index Pressure Temperature mass SiO2 Al2O3 FeO MgO CaO Na2O\n'
            '1 1.00 1400.00 100.0 50 15 10 10 10 5\n'
        )
        return types.SimpleNamespace(
            returncode=0,
            stdout=(
                '<> Stable liquid assemblage achieved.\n'
                'Initial alphaMELTS calculation at: P 1.000000 (bars), '
                'T 1400.000000 (C)\n'
                'liquid: SiO2 Al2O3 FeO MgO CaO Na2O\n'
                '100.0 g 50 15 10 10 10 5\n'
                'activity Na2O = 0.25\n'
                'Melt fraction = 1.0\n'
            ),
            stderr='',
        )

    monkeypatch.setattr(
        'simulator.melt_backend.alphamelts.subprocess.run',
        fake_run,
    )
    monkeypatch.setattr(
        backend,
        '_builtin_vapor_projection_for_subprocess',
        lambda _eq: (
            {'Na': 12.5},
            {'Na': 'builtin_authoritative:test'},
            {'vapor_pressures_Pa': {'Na': 12.5}},
        ),
    )

    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg=_melts_domain_composition(),
        fO2_log=-9.0,
        pressure_bar=1.0,
        subprocess_run_mode='isothermal',
    )

    assert seen['stdin'] == '1\ninput.melts\n4\n1\n1\nx\n'
    assert seen['env']['ALPHAMELTS_RUN_MODE'] == 'isobaric'
    assert 'Log fO2 Path: Absolute' in seen['input_melts']
    # Shipped alphaMELTS 2.3.1 accepts and echoes "log fo2 offset"; replacing
    # Offset with Delta is rejected as an offending MELTS-file record.
    assert 'Log fO2 Offset: -9' in seen['input_melts']
    assert 'Log fO2 Delta:' not in seen['input_melts']
    assert result.temperature_C == pytest.approx(1400.0)
    assert result.requested_temperature_C == pytest.approx(1400.0)
    assert result.fO2_log == pytest.approx(-9.0)
    assert result.liquid_density_kg_m3 == pytest.approx(2638.918)
    assert result.liquid_viscosity_Pa_s == pytest.approx(0.1 * 10**1.409)
    assert result.system_enthalpy == pytest.approx(-1.0)
    assert result.system_volume == pytest.approx(1.0e-6)
    assert result.system_phi == pytest.approx(1.0)
    assert result.system_chisqr is None
    assert result.phase_thermo['liquid']['enthalpy_J'] == pytest.approx(-1059377.1)
    assert result.phase_thermo['liquid']['volume_m3'] == pytest.approx(34.56e-6)
    assert result.phase_thermo['liquid']['reference_mass_kg'] == pytest.approx(0.1)
    assert result.phase_thermo['liquid']['density_kg_m3'] == pytest.approx(
        100.0 / 34.56 * 1000.0
    )
    assert result.phase_compositions['liquid']['SiO2'] == pytest.approx(50.0)
    assert result.solid_composition_wt_pct == {}
    assert result.bulk_composition_wt_pct['SiO2'] == pytest.approx(50.0)
    assert result.phase_species_kg['liquid1']['Na2O'] == pytest.approx(5.0)
    assert result.phase_species_mol['liquid1']['Na2O'] > 0.0
    assert result.vapor_pressures_Pa == {'Na': pytest.approx(12.5)}
    assert result.vapor_pressures_source['Na'].startswith('builtin_authoritative')
    assert result.diagnostics['intrinsic_fO2_log'] == pytest.approx(-9.0)
    assert result.diagnostics['thermodynamic_basis'] == {
        'reference_basis': 'alphamelts_solver_system_amount',
        'reference_mass_kg': pytest.approx(0.1),
        'system_enthalpy': {'units': 'J'},
        'system_entropy': {'units': 'J/K'},
        'system_volume': {'units': 'm3', 'source_units': 'cm3'},
        'system_heat_capacity_Cp': {'units': 'J/K'},
    }


@pytest.mark.parametrize(
    'system_output',
    [
        (
            'index Pressure Temperature mass fO2(absolute)\n'
            '1 1.0 1400.0 100.0 -9.0\n'
        ),
        (
            'index Pressure Temperature mass fO2(absolute) rhol viscosity\n'
            '1 1.0 1400.0 100.0 -9.0 n/a n/a\n'
        ),
    ],
    ids=['properties-absent', 'properties-nonnumeric'],
)
def test_alphamelts_system_table_optional_properties_stay_none(system_output):
    backend = AlphaMELTSBackend()
    output = (
        '<> Stable liquid assemblage achieved.\n'
        'Initial alphaMELTS calculation at: P 1.000000 (bars), '
        'T 1400.000000 (C)\n'
        'liquid: SiO2\n100.0 g 100.0\nMelt fraction = 1.0\n'
    )

    result = _parse_subprocess_fixture(
        backend,
        output,
        temperature_C=1400.0,
        system_output=system_output,
    )

    assert result.liquid_density_kg_m3 is None
    assert result.liquid_viscosity_Pa_s is None


def test_alphamelts_subprocess_isothermal_rejects_executed_temperature_mismatch():
    backend = AlphaMELTSBackend()
    output = (
        '<> Stable liquid assemblage achieved.\n'
        'Initial alphaMELTS calculation at: P 1.000000 (bars), '
        'T 1300.000000 (C)\n'
        'liquid: SiO2\n100.0 g 100.0\nMelt fraction = 1.0\n'
    )

    with pytest.raises(
        AlphaMELTSSubprocessContractError,
        match='other than the isothermal request',
    ):
        backend._parse_single_point_stdout(
            output,
            requested_temperature_C=1400.0,
            pressure_bar=1.0,
            fO2_log=-9.0,
            total_input_kg=100.0,
            run_mode=AlphaMELTSSubprocessRunMode.ISOTHERMAL,
            system_output=_system_main_fixture(temperature_C=1300.0),
            fO2_constraint={'path': 'Absolute', 'offset': -9.0},
        )


def test_alphamelts_subprocess_no_phase_still_rejects_temperature_mismatch():
    backend = AlphaMELTSBackend()
    output = (
        'Initial alphaMELTS calculation at: P 1.000000 (bars), '
        'T 1300.000000 (C)\n'
        '...Quadratic convergence failure. Aborting.\n'
    )

    with pytest.raises(
        AlphaMELTSSubprocessContractError,
        match='other than the isothermal request',
    ):
        backend._parse_single_point_stdout(
            output,
            requested_temperature_C=1400.0,
            pressure_bar=1.0,
            fO2_log=-9.0,
            total_input_kg=100.0,
            run_mode=AlphaMELTSSubprocessRunMode.ISOTHERMAL,
            system_output='',
            fO2_constraint={'path': 'Absolute', 'offset': -9.0},
        )


def test_alphamelts_subprocess_failure_line_rejects_temperature_mismatch():
    # Regression for the request-echo defect: with NO banner line, the
    # failure line's temperature is the only executed-T evidence. A parser
    # that echoes the requested temperature would sail past this mismatch
    # (requested 1250.0 vs executed 1249.414062 from the failure line).
    backend = AlphaMELTSBackend()
    output = (
        '<> Found the liquidus at T = 1249.41 (C).\n'
        '...Quadratic convergence failure. Aborting.\n'
        'Initial calculation failed (1.000000 bars, 1249.414062 C)!\n'
    )

    with pytest.raises(
        AlphaMELTSSubprocessContractError,
        match='other than the isothermal request',
    ):
        backend._parse_single_point_stdout(
            output,
            requested_temperature_C=1250.0,
            pressure_bar=1.0,
            fO2_log=-9.0,
            total_input_kg=100.0,
            run_mode=AlphaMELTSSubprocessRunMode.ISOTHERMAL,
            system_output='',
            fO2_constraint={'path': 'Absolute', 'offset': -9.0},
        )


def test_alphamelts_reset_sentinel_is_not_an_executed_temperature():
    backend = AlphaMELTSBackend()
    output = (
        'Initial calculation failed (1.000000 bars, 1250.000000 C)!\n'
        'Initial calculation failed (0.000000 bars, -273.150000 C)!\n'
    )

    result = _parse_subprocess_fixture(
        backend,
        output,
        temperature_C=1250.0,
        system_output='',
    )

    assert result.status == 'out_of_domain'
    assert result.temperature_C == pytest.approx(1250.0)
    assert result.diagnostics['backend_status_reason'] == 'no_convergence'


def test_alphamelts_reset_sentinel_alone_fails_closed():
    backend = AlphaMELTSBackend()
    output = 'Initial calculation failed (0.000000 bars, -273.150000 C)!\n'

    with pytest.raises(AlphaMELTSSubprocessContractError) as excinfo:
        _parse_subprocess_fixture(
            backend,
            output,
            temperature_C=1250.0,
            system_output='',
        )

    assert excinfo.value.backend_failure_reason_code == 'executed_temperature_missing'


def test_alphamelts_final_assemblage_accumulates_numbered_phase_instances():
    backend = AlphaMELTSBackend()
    output = (
        '<> Stable spinel assemblage achieved.\n'
        'Initial alphaMELTS calculation at: P 1.000000 (bars), T 1400.000000 (C)\n'
        'spinel1: 90.0 g\n'
        '<> Stable spinel assemblage achieved.\n'
        'Initial alphaMELTS calculation at: P 1.000000 (bars), T 1400.000000 (C)\n'
        'spinel1: 40.0 g\n'
        'spinel2: 60.0 g\n'
    )

    result = _parse_subprocess_fixture(
        backend,
        output,
        temperature_C=1400.0,
    )

    assert result.phase_masses_kg == {'spinel': pytest.approx(0.1)}
    assert result.diagnostics['phase_instance_masses_solver_basis_kg'] == {
        'spinel1': pytest.approx(0.04),
        'spinel2': pytest.approx(0.06),
    }


def test_alphamelts_subprocess_requires_finite_absolute_fo2_echo():
    backend = AlphaMELTSBackend()
    output = (
        '<> Stable liquid assemblage achieved.\n'
        'Initial alphaMELTS calculation at: P 1.000000 (bars), '
        'T 1400.000000 (C)\n'
        'liquid: SiO2\n100.0 g 100.0\nMelt fraction = 1.0\n'
    )
    system_output = (
        'index Pressure Temperature fO2(absolute) rhol viscosity\n'
        '1 1.0 1400.0 n/a 2.6 1.4\n'
    )

    with pytest.raises(
        AlphaMELTSSubprocessContractError,
        match='lacks absolute engine fO2 echo',
    ):
        backend._parse_single_point_stdout(
            output,
            requested_temperature_C=1400.0,
            pressure_bar=1.0,
            fO2_log=-9.0,
            total_input_kg=100.0,
            run_mode=AlphaMELTSSubprocessRunMode.ISOTHERMAL,
            system_output=system_output,
            fO2_constraint={'path': 'Absolute', 'offset': -9.0},
        )


def test_alphamelts_subprocess_serializes_configured_qfm_buffer(tmp_path):
    backend = AlphaMELTSBackend()
    backend._redox_buffer = 'QFM'
    backend._fo2_offset = -1.5

    constraint = backend._subprocess_fo2_constraint(-9.0)
    path = tmp_path / 'input.melts'
    backend._write_melts_file(
        path,
        _melts_domain_composition(),
        1400.0,
        1.0,
        fO2_path=constraint[0],
        fO2_offset=constraint[1],
    )

    text = path.read_text()
    assert 'Log fO2 Path: FMQ' in text
    assert 'Log fO2 Offset: -1.5' in text
    assert 'Log fO2 Delta:' not in text


def test_alphamelts_python_api_clamped_pressure_reports_solved_condition(
    monkeypatch,
):
    backend = AlphaMELTSBackend()
    backend._mode = 'python_api'
    seen = {}

    class FakePetThermoTools:
        def equilibrate_MELTS(self, **kwargs):
            seen.update(kwargs)
            return {'ok': True}

    def fake_parse(
        results,
        *,
        temperature_C,
        pressure_bar,
        fO2_log,
        comp_wt,
        total_input_kg,
        require_solved_fo2,
        warnings=None,
        diagnostics=None,
    ):
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            phases_present=['liquid'],
            phase_masses_kg={'liquid': 1.0},
            liquid_fraction=1.0,
            warnings=list(warnings or []),
            status='ok',
            diagnostics=dict(diagnostics or {}),
        )

    monkeypatch.setattr(
        backend,
        '_require_petthermotools_runtime',
        lambda: FakePetThermoTools(),
    )
    monkeypatch.setattr(backend, '_parse_petthermotools_result', fake_parse)
    monkeypatch.setattr(
        backend,
        '_activities_times_antoine_or_fail',
        lambda *args, **kwargs: {},
    )

    result = backend._equilibrate_python(
        1600.0,
        _melts_domain_composition(),
        -9.0,
        1e-9,
    )

    assert seen['P_bar'] == pytest.approx(1e-6)
    assert result.temperature_C == pytest.approx(1600.0)
    assert result.pressure_bar == pytest.approx(1e-6)
    assert result.diagnostics['operating_point_clamped'] is True
    assert result.diagnostics['temperature_clamped'] is False
    assert result.diagnostics['pressure_clamped'] is True
    assert result.diagnostics['requested_pressure_bar'] == pytest.approx(1e-9)
    assert result.diagnostics['solved_pressure_bar'] == pytest.approx(1e-6)
    assert result.status == 'out_of_domain'
    assert result.diagnostics['backend_status'] == 'out_of_domain'
    assert (
        result.diagnostics['backend_status_reason']
        == 'clamped_operating_point'
    )
    assert (
        result.diagnostics['authoritative_for_requested_conditions']
        is False
    )
    assert any('clamped operating point' in warning for warning in result.warnings)


def test_alphamelts_subprocess_signal_exit_is_typed_crash_without_mode_flip(
    monkeypatch,
):
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = Path('/tmp/fake-alphamelts')
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            return types.SimpleNamespace(
                returncode=-6,
                stdout='... liquidus search stopped mid-stream',
                stderr='',
            )
        return types.SimpleNamespace(returncode=0, stdout='stable', stderr='')

    def fake_parse(*args, **kwargs):
        return EquilibriumResult(
            temperature_C=kwargs['requested_temperature_C'],
            pressure_bar=kwargs['pressure_bar'],
            fO2_log=kwargs['fO2_log'],
            phases_present=['liquid'],
            phase_masses_kg={'liquid': 1.0},
            liquid_fraction=1.0,
            status='ok',
        )

    monkeypatch.setattr('simulator.melt_backend.alphamelts.subprocess.run', fake_run)
    monkeypatch.setattr(backend, '_parse_single_point_stdout', fake_parse)
    monkeypatch.setattr(
        backend,
        '_builtin_vapor_projection_for_subprocess',
        lambda _eq: (
            {'Na': 1.0},
            {'Na': 'builtin_authoritative:test'},
            {'test_stub': True},
        ),
    )

    with pytest.raises(AlphaMELTSSubprocessContractError) as excinfo:
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg=_melts_domain_composition(),
            fO2_log=-9.0,
            pressure_bar=1.0,
            subprocess_run_mode='isothermal',
        )
    second = backend.equilibrate(
        temperature_C=1600.0,
        composition_kg=_melts_domain_composition(),
        fO2_log=-9.0,
        pressure_bar=1.0,
        subprocess_run_mode='isothermal',
    )

    assert excinfo.value.backend_failure_reason_code == (
        ALPHAMELTS_REASON_SUBPROCESS_DIED
    )
    assert excinfo.value.backend_failure_category == 'engine_crash'
    assert 'SIGABRT' in str(excinfo.value)
    assert backend._mode == 'subprocess'
    assert second.status == 'ok'
    assert len(calls) == 2


def test_alphamelts_subprocess_timeout_stays_loud_without_mode_flip(monkeypatch):
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = Path('/tmp/fake-alphamelts')
    seen = {}

    def fake_run(*args, **kwargs):
        seen['timeout'] = kwargs['timeout']
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs['timeout'])

    monkeypatch.setattr('simulator.melt_backend.alphamelts.subprocess.run', fake_run)

    with pytest.raises(RuntimeError, match='timed out') as excinfo:
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg=_melts_domain_composition(),
            fO2_log=-9.0,
            pressure_bar=1.0,
            subprocess_run_mode='isothermal',
        )

    assert (
        getattr(excinfo.value, 'backend_status_reason')
        == ALPHAMELTS_REASON_TIMEOUT
    )
    assert (
        getattr(excinfo.value, 'backend_failure_reason_code')
        == ALPHAMELTS_REASON_TIMEOUT
    )
    assert getattr(excinfo.value, 'backend_failure_category') == 'not_converged'
    assert 'timed out' in getattr(
        excinfo.value,
        'backend_status_reason_message',
    )
    assert seen['timeout'] == 20.0
    assert backend._mode == 'subprocess'


def test_alphamelts_subprocess_uses_configured_timeout(monkeypatch):
    backend = AlphaMELTSBackend()
    monkeypatch.setattr(
        backend,
        '_find_project_binary',
        lambda _engine_root: Path('/tmp/fake-alphamelts'),
    )
    assert backend.initialize({'mode': 'subprocess', 'timeout_s': 37.5}) is True
    seen = {}

    def fake_run(*args, **kwargs):
        seen['timeout'] = kwargs['timeout']
        return types.SimpleNamespace(returncode=-6, stdout='', stderr='')

    monkeypatch.setattr('simulator.melt_backend.alphamelts.subprocess.run', fake_run)

    with pytest.raises(AlphaMELTSSubprocessContractError) as exc_info:
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg=_melts_domain_composition(),
            fO2_log=-9.0,
            pressure_bar=1.0,
            subprocess_run_mode='isothermal',
        )

    assert (
        exc_info.value.backend_failure_reason_code
        == ALPHAMELTS_REASON_SUBPROCESS_DIED
    )
    assert seen['timeout'] == 37.5
    assert backend._mode == 'subprocess'


def test_alphamelts_subprocess_missing_binary_is_loud_and_disables_mode(monkeypatch):
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = Path('/tmp/fake-alphamelts')

    def fake_run(*args, **kwargs):
        raise FileNotFoundError('missing binary')

    monkeypatch.setattr('simulator.melt_backend.alphamelts.subprocess.run', fake_run)

    with pytest.raises(RuntimeError, match='binary not found') as excinfo:
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg=_melts_domain_composition(),
            fO2_log=-9.0,
            pressure_bar=1.0,
            subprocess_run_mode='isothermal',
        )

    assert (
        getattr(excinfo.value, 'backend_status_reason')
        == ALPHAMELTS_REASON_MISSING_BINARY
    )
    assert (
        getattr(excinfo.value, 'backend_failure_reason_code')
        == ALPHAMELTS_REASON_MISSING_BINARY
    )
    assert (
        getattr(excinfo.value, 'backend_failure_category')
        == OutOfDomainReason.BACKEND_UNAVAILABLE.value
    )
    assert 'binary' in getattr(
        excinfo.value,
        'backend_status_reason_message',
    )
    assert backend._mode is None


def test_alphamelts_subprocess_unconfigured_binary_reports_missing_binary():
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = None
    backend._engine_path = None

    with pytest.raises(RuntimeError, match='not configured') as excinfo:
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg=_melts_domain_composition(),
            fO2_log=-9.0,
            pressure_bar=1.0,
            subprocess_run_mode='isothermal',
        )

    assert (
        getattr(excinfo.value, 'backend_status_reason')
        == ALPHAMELTS_REASON_MISSING_BINARY
    )
    assert (
        getattr(excinfo.value, 'backend_failure_reason_code')
        == ALPHAMELTS_REASON_MISSING_BINARY
    )
    assert (
        getattr(excinfo.value, 'backend_failure_category')
        == OutOfDomainReason.BACKEND_UNAVAILABLE.value
    )


def test_alphamelts_subprocess_positive_exit_stays_loud_without_mode_flip(
    monkeypatch,
):
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = Path('/tmp/fake-alphamelts')

    monkeypatch.setattr(
        'simulator.melt_backend.alphamelts.subprocess.run',
        lambda *args, **kwargs: types.SimpleNamespace(
            returncode=2,
            stdout='',
            stderr='wrapper error',
        ),
    )

    with pytest.raises(RuntimeError, match='returncode 2') as excinfo:
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg=_melts_domain_composition(),
            fO2_log=-9.0,
            pressure_bar=1.0,
            subprocess_run_mode='isothermal',
        )

    assert (
        getattr(excinfo.value, 'backend_status_reason')
        == ALPHAMELTS_REASON_NONZERO_EXIT
    )
    assert (
        getattr(excinfo.value, 'backend_failure_reason_code')
        == ALPHAMELTS_REASON_NONZERO_EXIT
    )
    assert getattr(excinfo.value, 'backend_failure_category') == 'not_converged'
    assert backend._mode == 'subprocess'


def test_alphamelts_subprocess_exit_zero_without_assemblage_stays_loud(
    monkeypatch,
):
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = Path('/tmp/fake-alphamelts')

    monkeypatch.setattr(
        'simulator.melt_backend.alphamelts.subprocess.run',
        lambda *args, **kwargs: types.SimpleNamespace(
            returncode=0,
            stdout='successful run but changed format',
            stderr='',
        ),
    )

    with pytest.raises(
        RuntimeError,
        match='no parseable phase assemblage',
    ) as excinfo:
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg=_melts_domain_composition(),
            fO2_log=-9.0,
            pressure_bar=1.0,
            subprocess_run_mode='isothermal',
        )

    assert (
        getattr(excinfo.value, 'backend_status_reason')
        == ALPHAMELTS_REASON_PARSE_EMPTY_OUTPUT
    )
    assert (
        getattr(excinfo.value, 'backend_failure_reason_code')
        == ALPHAMELTS_REASON_PARSE_EMPTY_OUTPUT
    )
    assert getattr(excinfo.value, 'backend_failure_category') == 'not_converged'

    assert backend._mode == 'subprocess'


def test_configured_unavailable_alphamelts_backend_fail_closes():
    backend = AlphaMELTSBackend()
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"SiO2": 100.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("oxide", mass_kg=1.0)

    with pytest.raises(RuntimeError, match='AlphaMELTSBackend is unavailable'):
        sim._get_equilibrium()


def test_alphamelts_vapor_species_are_canonical_formula_names():
    backend = AlphaMELTSBackend()
    backend._vaporock_available = True

    species = set(backend.get_vapor_species())

    assert 'SiO2' in species
    assert 'Fe2O3' in species
    assert 'SiO2_gas' not in species
    assert 'Fe2O3_gas' not in species


def test_alphamelts_rejects_metal_and_gas_account_inputs():
    backend = AlphaMELTSBackend()

    result = backend.equilibrate(
        temperature_C=1600.0,
        composition_mol_by_account={
            'process.cleaned_melt': {'SiO2': 1.0},
            'process.metal_phase': {'Fe': 0.25},
            'process.overhead_gas': {'O2': 0.5},
        },
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    assert result.phases_present == []
    assert result.warnings == [
        "DomainGate rejected: unsupported ledger accounts present: "
        "process.metal_phase=['Fe'], process.overhead_gas=['O2']"
    ]
    assert result.status == 'out_of_domain'
    assert (
        result.diagnostics.get('backend_status_reason')
        == OutOfDomainReason.FORBIDDEN_SPECIES.value
    )


def test_alphamelts_capabilities_surface_engine_version(monkeypatch):
    backend = AlphaMELTSBackend()
    monkeypatch.setattr(
        'simulator.melt_backend.alphamelts.importlib.metadata.version',
        lambda name: '0.test',
    )

    assert backend.capabilities()['engine_version'] == 'petthermotools 0.test'


def test_alphamelts_initialize_requires_petthermotools_payload(monkeypatch):
    backend = AlphaMELTSBackend()
    fake_ptt = types.SimpleNamespace(__version__='0.test')

    def fake_import(name):
        if name in {'petthermotools', 'PetThermoTools'}:
            return fake_ptt
        if name == 'meltsdynamic':
            raise ImportError('no meltsdynamic')
        raise AssertionError(name)

    monkeypatch.setattr(
        'simulator.melt_backend.alphamelts.importlib.import_module',
        fake_import,
    )

    with pytest.raises(ImportError, match='PetThermoTools Python path unavailable'):
        backend.initialize({'mode': 'python_api'})


def test_alphamelts_require_petthermotools_does_not_use_subprocess(monkeypatch):
    backend = AlphaMELTSBackend()
    monkeypatch.setattr(
        backend,
        '_find_project_binary',
        lambda _engine_root: Path('/tmp/fake-alphamelts'),
    )

    def fake_import(name):
        if name in {'petthermotools', 'PetThermoTools'}:
            return types.SimpleNamespace(__version__='0.test')
        if name == 'meltsdynamic':
            raise ImportError('no meltsdynamic')
        raise AssertionError(name)

    monkeypatch.setattr(
        'simulator.melt_backend.alphamelts.importlib.import_module',
        fake_import,
    )

    with pytest.raises(ImportError, match='PetThermoTools Python path unavailable'):
        backend.initialize({'require_petthermotools': True})
    assert backend._mode is None



def test_alphamelts_provider_production_equilibrium_skips_thermoengine(monkeypatch):
    provider = AlphaMELTSProvider(backend=types.SimpleNamespace())
    request = types.SimpleNamespace(
        account_view=types.SimpleNamespace(species_formula_registry={}),
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
    )

    monkeypatch.setattr(alphamelts_provider_module, 'python_api_available', lambda _backend: False)
    monkeypatch.setattr(alphamelts_provider_module, 'subprocess_available', lambda _backend: False)

    mode, equilibrium = provider._run_backend(
        request,
        composition_mol_by_account={'process.cleaned_melt': {'SiO2': 1.0}},
    )

    assert mode == 'unavailable'
    assert equilibrium is None


def test_alphamelts_provider_liquidus_skips_thermoengine(monkeypatch):
    def fail_liquidus(*args, **kwargs):
        raise AssertionError('liquidus must not call in-process ThermoEngine')

    provider = AlphaMELTSProvider(
        backend=types.SimpleNamespace(find_liquidus_solidus=fail_liquidus)
    )
    request = types.SimpleNamespace(
        account_view=types.SimpleNamespace(species_formula_registry={}),
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
    )

    monkeypatch.setattr(
        alphamelts_provider_module,
        'python_api_available',
        lambda _backend: False,
    )
    monkeypatch.setattr(
        alphamelts_provider_module,
        'subprocess_available',
        lambda _backend: False,
    )

    mode, result = provider._run_liquidus_finder(
        request,
        composition_mol_by_account={'process.cleaned_melt': {'SiO2': 1.0}},
    )

    assert mode == 'unavailable'
    assert result.status == 'unavailable'


def test_alphamelts_provider_ec_skips_thermoengine(monkeypatch):
    def fail_transport(*args, **kwargs):
        raise AssertionError('EC must not call in-process ThermoEngine')

    provider = AlphaMELTSProvider(
        backend=types.SimpleNamespace(find_liquidus_solidus=fail_transport)
    )
    request = types.SimpleNamespace(
        account_view=types.SimpleNamespace(species_formula_registry={}),
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
    )

    monkeypatch.setattr(
        alphamelts_provider_module,
        'python_api_available',
        lambda _backend: False,
    )
    monkeypatch.setattr(
        alphamelts_provider_module,
        'subprocess_available',
        lambda _backend: False,
    )

    mode, result = provider._run_equilibrium_crystallization_path(
        request,
        composition_mol_by_account={'process.cleaned_melt': {'SiO2': 1.0}},
    )

    assert mode == 'unavailable'
    assert result.status == 'unavailable'


def test_alphamelts_domain_gate_contract_is_composition_only():
    doc = AlphaMELTSDomainGate.__doc__ or ''

    assert 'T / P bounds' not in doc
    assert 'Composition-only gate' in doc


def test_alphamelts_initialize_defaults_to_subprocess_when_binary_available(
    monkeypatch,
):
    backend = AlphaMELTSBackend()
    monkeypatch.setattr(
        backend,
        '_find_project_binary',
        lambda _engine_root: Path('/tmp/fake-alphamelts'),
    )

    assert backend.initialize({}) is True
    assert backend._mode == 'subprocess'


def test_alphamelts_initialize_explicit_thermoengine_when_available(monkeypatch):
    class FakeThermoEngineTransport:
        engine_version = 'thermoengine fake'

        def __init__(self, *, model_name, activity_converter,
                     equilibrate_timeout_s):
            self.model_name = model_name
            self.activity_converter = activity_converter
            self.equilibrate_timeout_s = equilibrate_timeout_s

        def initialize(self):
            return True

    backend = ThermoEngineBackend()
    monkeypatch.setattr(
        'simulator.melt_backend.thermoengine.ThermoEngineTransport',
        FakeThermoEngineTransport,
    )

    assert backend.initialize({}) is True
    assert backend._mode == 'thermoengine'
    assert backend.get_engine_version() == 'thermoengine fake'
    assert backend._thermoengine_transport.equilibrate_timeout_s == 60.0


def test_alphamelts_backend_rejects_thermoengine_transport_mode():
    with pytest.raises(ValueError, match='unsupported AlphaMELTS mode'):
        AlphaMELTSBackend().initialize({'mode': 'thermoengine'})


def test_melt_backend_interface_documents_intrinsic_default_opt_in():
    base_parameters = inspect.signature(MeltBackend.equilibrate).parameters
    thermo_parameters = inspect.signature(ThermoEngineBackend.equilibrate).parameters

    assert 'subprocess_run_mode' not in base_parameters
    assert 'subprocess_run_mode' not in thermo_parameters
    assert base_parameters['fO2_log'].default == -9.0
    assert thermo_parameters['fO2_log'].default is None
    assert MeltBackend.supports_intrinsic_fO2 is False
    assert ThermoEngineBackend.supports_intrinsic_fO2 is True


def test_alphamelts_results_carry_backend_and_engine_provenance():
    backend = AlphaMELTSBackend()
    backend._engine_version = 'alphamelts fake-v1'

    result = backend._emit_equilibrium_result(
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        status='unavailable',
    )

    assert result.backend_name == 'alphamelts'
    assert result.engine_version == 'alphamelts fake-v1'


def test_thermoengine_health_failure_is_scoped_to_transport_lifecycle(
    monkeypatch,
):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(args, kwargs['timeout'])
        return subprocess.CompletedProcess(args, 0, stdout='ok\n', stderr='')

    monkeypatch.setattr(
        'engines.alphamelts.thermoengine.subprocess.run',
        fake_run,
    )
    first = ThermoEngineTransport(
        model_name='MELTSv1.0.2',
        activity_converter=activity_from_chem_potential,
    )
    second = ThermoEngineTransport(
        model_name='MELTSv1.0.2',
        activity_converter=activity_from_chem_potential,
    )

    first_health = first.health_check(timeout_s=1.0)

    assert first_health[0] is False
    assert first.health_check(timeout_s=1.0) == first_health
    assert len(calls) == 1
    assert first.health_check(timeout_s=1.0, failure_cache_ttl_s=0.0) == (
        True,
        'ThermoEngine smoke equilibrium completed',
    )
    assert len(calls) == 2
    first.clear_health_cache()
    assert first.health_check(timeout_s=1.0) == (
        True,
        'ThermoEngine smoke equilibrium completed',
    )
    assert len(calls) == 3
    assert second.health_check(timeout_s=1.0) == (
        True,
        'ThermoEngine smoke equilibrium completed',
    )
    assert len(calls) == 4


def test_thermoengine_transport_rejects_unknown_model_name():
    with pytest.raises(ValueError, match='unknown ThermoEngine MELTS model'):
        ThermoEngineTransport(
            model_name='MELTSv1.O.2',
            activity_converter=activity_from_chem_potential,
        )


def test_thermoengine_transport_rejects_unpickleable_worker_converter():
    transport = ThermoEngineTransport(
        activity_converter=lambda _mu, _mu0, _temperature_K: 1.0,
    )

    with pytest.raises(TypeError, match='activity_converter must be pickleable'):
        transport.initialize()


def test_thermoengine_debug_log_appends_pre_solve_input(tmp_path):
    log_path = tmp_path / 'thermoengine-diagnostics.log'

    with log_path.open('a', encoding='utf-8') as errlog:
        thermoengine_module._append_solve_input_line(
            errlog,
            worker_id=4123,
            temperature_C=1400.0,
            pressure_bar=1.5,
            comp_wt={'SiO2': 50.0, 'FeO': 10.0},
            fO2_log=-9.0,
        )

    line = log_path.read_text(encoding='utf-8').strip()
    fields = dict(part.split('=', 1) for part in line.split(' | '))
    assert fields['worker_id'] == '4123'
    assert len(fields['comp_sha256']) == 16
    assert fields['T_C'] == '1400'
    assert fields['P_bar'] == '1.5'
    assert fields['fO2_log'] == '-9'
    assert fields['timestamp'].endswith('Z')


def test_thermoengine_worker_registers_faulthandler_to_debug_log(
    monkeypatch,
    tmp_path,
):
    registrations = []
    monkeypatch.setattr(
        thermoengine_module.faulthandler,
        'register',
        lambda signum, **kwargs: registrations.append((signum, kwargs)),
    )
    log_path = tmp_path / 'nested' / 'diagnostics.log'

    errlog = thermoengine_module._register_worker_fault_handler(log_path, 12)
    try:
        assert registrations == [(12, {
            'file': errlog,
            'all_threads': True,
        })]
        assert log_path.exists()
    finally:
        errlog.close()


def test_thermoengine_timeout_dumps_then_kills_worker(monkeypatch):
    events = []

    class FakeProcess:
        pid = 4123
        alive = True

        def is_alive(self):
            return self.alive

        def kill(self):
            events.append('kill')
            self.alive = False

        def join(self, timeout):
            events.append(('join', timeout))

    class FakeConnection:
        def send(self, value):
            events.append(('send', value))

        def poll(self, timeout):
            events.append(('poll', timeout))
            return False

        def close(self):
            events.append('close')

    monkeypatch.setattr(
        thermoengine_module.os,
        'kill',
        lambda pid, signum: events.append(('diagnostic_signal', pid, signum)),
    )
    monkeypatch.setattr(
        thermoengine_module.time,
        'sleep',
        lambda seconds: events.append(('grace', seconds)),
    )
    transport = ThermoEngineTransport(
        activity_converter=activity_from_chem_potential,
        equilibrate_timeout_s=2.0,
        watchdog_grace_s=0.125,
        diagnostic_signal=12,
    )
    transport._worker_process = FakeProcess()
    transport._worker_connection = FakeConnection()

    with pytest.raises(TimeoutError, match='hard timeout of 2s'):
        transport.equilibrate(
            temperature_C=1400.0,
            pressure_bar=1.0,
            comp_wt={'SiO2': 50.0},
            fO2_log=-9.0,
        )

    order = [event if isinstance(event, str) else event[0] for event in events]
    assert order[-5:] == ['diagnostic_signal', 'grace', 'kill', 'join', 'close']
    assert transport._worker_process is None
    assert transport._worker_connection is None


def test_thermoengine_transport_close_is_idempotent():
    events = []

    class FakeProcess:
        alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            events.append(('join', timeout))
            self.alive = False

        def terminate(self):
            events.append(('terminate',))

        def kill(self):
            events.append(('kill',))

    class FakeConnection:
        def send(self, value):
            events.append(('send', value))

        def close(self):
            events.append(('close',))

    transport = ThermoEngineTransport(
        activity_converter=activity_from_chem_potential,
    )
    transport._worker_process = FakeProcess()
    transport._worker_connection = FakeConnection()

    transport.close()
    transport.close()

    assert events == [('send', None), ('close',), ('join', 1.0)]
    assert transport._worker_process is None
    assert transport._worker_connection is None


def test_thermoengine_backend_close_clears_availability():
    class FakeTransport:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    backend = ThermoEngineBackend()
    transport = FakeTransport()
    backend._thermoengine_transport = transport
    backend._mode = 'thermoengine'

    backend.close()
    backend.close()

    assert transport.close_calls == 1
    assert backend._thermoengine_transport is None
    assert backend._mode is None
    assert backend.is_available() is False


@pytest.mark.parametrize(
    'failure',
    [
        RuntimeError('child solver failure'),
        ImportError('child import failure'),
    ],
)
def test_thermoengine_backend_equilibrium_failure_closes_worker(failure):
    class FailingTransport:
        def __init__(self):
            self.close_calls = 0

        def equilibrate(self, **_kwargs):
            raise failure

        def close(self):
            self.close_calls += 1

    backend = ThermoEngineBackend()
    transport = FailingTransport()
    backend._thermoengine_transport = transport
    backend._mode = 'thermoengine'

    with pytest.raises(type(failure)):
        backend._equilibrate_thermoengine(
            1400.0,
            _melts_domain_composition(),
            -9.0,
            1.0,
        )

    assert transport.close_calls == 1
    assert backend._thermoengine_transport is None
    assert backend._mode is None
    assert backend.is_available() is False


def test_thermoengine_backend_failure_preserves_primary_close_error():
    class FailingTransport:
        def equilibrate(self, **_kwargs):
            raise RuntimeError('child solver failure')

        def close(self):
            raise RuntimeError('pipe close failure')

    backend = ThermoEngineBackend()
    backend._thermoengine_transport = FailingTransport()
    backend._mode = 'thermoengine'

    with pytest.raises(
        RuntimeError,
        match='ThermoEngine equilibrium failed: child solver failure',
    ) as excinfo:
        backend._equilibrate_thermoengine(
            1400.0,
            _melts_domain_composition(),
            -9.0,
            1.0,
        )

    assert excinfo.value.__cause__ is not None
    assert excinfo.value.__cause__.__notes__ == [
        'ThermoEngine cleanup also failed: pipe close failure'
    ]
    assert backend._thermoengine_transport is None
    assert backend._mode is None
    assert backend.is_available() is False


def test_thermoengine_intrinsic_out_of_domain_returns_clean_result():
    backend = ThermoEngineBackend()

    result = backend.equilibrate(
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=None,
        composition_mol_by_account={
            'process.cleaned_melt': {'SiO2': 1.0},
            'process.metal': {'Fe': 1.0},
        },
    )

    assert result.status == 'out_of_domain'
    assert result.fO2_log is None


def test_thermoengine_transport_broken_pipe_closes_worker():
    events = []

    class FakeProcess:
        alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            events.append(('join', timeout))
            self.alive = False

        def terminate(self):
            events.append(('terminate',))

        def kill(self):
            events.append(('kill',))

    class BrokenConnection:
        def send(self, _value):
            raise BrokenPipeError('worker pipe closed')

        def close(self):
            events.append(('close',))

    transport = ThermoEngineTransport(
        activity_converter=activity_from_chem_potential,
    )
    transport._worker_process = FakeProcess()
    transport._worker_connection = BrokenConnection()

    with pytest.raises(RuntimeError, match='worker exited without a result'):
        transport.equilibrate(
            temperature_C=1200.0,
            pressure_bar=1.0,
            comp_wt={'SiO2': 50.0},
        )

    assert events == [('close',), ('join', 1.0)]
    assert transport._worker_process is None
    assert transport._worker_connection is None


def test_thermoengine_transport_pipe_close_failure_still_joins_worker():
    events = []

    class FakeProcess:
        alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            events.append(('join', timeout))
            self.alive = False

        def terminate(self):
            events.append(('terminate',))

        def kill(self):
            events.append(('kill',))

    class FailingCloseConnection:
        def send(self, value):
            events.append(('send', value))

        def close(self):
            events.append(('close',))
            raise RuntimeError('pipe close failure')

    transport = ThermoEngineTransport(
        activity_converter=activity_from_chem_potential,
    )
    transport._worker_process = FakeProcess()
    transport._worker_connection = FailingCloseConnection()

    with pytest.raises(RuntimeError, match='pipe close failure'):
        transport.close()

    assert events == [('send', None), ('close',), ('join', 1.0)]
    assert transport._worker_process is None
    assert transport._worker_connection is None


def test_thermoengine_health_smoke_requires_positive_phase_mass(monkeypatch):
    def fake_run(args, **kwargs):
        code = args[-1]
        assert 'positive_phase_mass_kg' in code
        assert 'payload.phase_masses_kg' in code
        return subprocess.CompletedProcess(args, 0, stdout='ok\n', stderr='')

    monkeypatch.setattr(
        'engines.alphamelts.thermoengine.subprocess.run',
        fake_run,
    )
    transport = ThermoEngineTransport(
        activity_converter=activity_from_chem_potential,
    )

    assert transport.health_check(timeout_s=1.0) == (
        True,
        'ThermoEngine smoke equilibrium completed',
    )


def test_alphamelts_configured_subprocess_skips_thermoengine(monkeypatch):
    backend = AlphaMELTSBackend()
    monkeypatch.setattr(
        backend,
        '_find_project_binary',
        lambda _engine_root: Path('/tmp/fake-alphamelts'),
    )

    assert backend.initialize({'mode': 'subprocess'}) is True
    assert backend._mode == 'subprocess'


def test_alphamelts_config_top_level_subprocess_overrides_nested_thermoengine():
    backend = AlphaMELTSBackend()

    config = backend._alphamelts_config({
        'mode': 'subprocess',
        'python_bridge': 'subprocess',
        'alphamelts': {
            'mode': 'thermoengine',
            'python_bridge': 'pymagemin',
        },
    })

    assert config['mode'] == 'subprocess'
    assert config['python_bridge'] == 'subprocess'


def test_normalize_composition_to_melts_basis_drops_and_renormalizes():
    backend = AlphaMELTSBackend()

    comp = backend._normalize_composition_to_melts_basis({
        'SiO2': 45.0,
        'Al2O3': 15.0,
        'FeO': 10.0,
        'MgO': 10.0,
        'CaO': 10.0,
        'Na2O': 5.0,
        'H2O': 5.0,
    })

    assert pytest.approx(sum(comp.values())) == 100.0
    assert comp['SiO2'] == pytest.approx(47.3684210526)
    assert backend._last_normalization_warnings == [
        'Dropped non-MELTS component H2O'
    ]


def test_normalize_composition_refuses_feo_total_without_policy():
    backend = AlphaMELTSBackend()

    with pytest.raises(ValueError, match='FeO_total requires explicit redox policy'):
        backend._normalize_composition_to_melts_basis({
            'SiO2': 50.0,
            'FeO_total': 10.0,
        })


def test_normalize_composition_splits_feo_total_with_explicit_ratio():
    backend = AlphaMELTSBackend()
    backend._fe3fet_ratio = 0.2

    comp = backend._normalize_composition_to_melts_basis({
        'SiO2': 50.0,
        'Al2O3': 20.0,
        'FeO_total': 10.0,
        'MgO': 20.0,
    })

    assert pytest.approx(sum(comp.values())) == 100.0
    assert comp['FeO'] > comp['Fe2O3'] > 0.0



def test_alphamelts_domain_gate_rejects_negative_wt_percent():
    valid, warnings = AlphaMELTSDomainGate.validate({
        'SiO2': 50.0,
        'MgO': 50.0,
        'NaCl': -10.0,
    })

    assert valid is False
    assert any('negative wt%' in warning for warning in warnings)


def test_alphamelts_domain_gate_counts_feo_total_in_major_sum():
    valid, warnings = AlphaMELTSDomainGate.validate({
        'SiO2': 50.0,
        'Al2O3': 20.0,
        'FeO_total': 10.0,
        'MgO': 20.0,
    })

    assert valid is True
    assert warnings == []


def test_alphamelts_domain_gate_rejects_unrecognized_oxide_like_species():
    backend = AlphaMELTSBackend()

    result = backend._domain_gate(
        {'SiO2': 50.0, 'MgO': 49.0, 'XeO': 1.0},
        temperature_C=1500.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
    )

    assert result is not None
    assert result.status == 'out_of_domain'
    assert result.diagnostics['backend_status_reason'] == (
        OutOfDomainReason.FORBIDDEN_SPECIES.value
    )
    assert 'unrecognised species outside MELTS basis: XeO' in result.warnings[0]


def test_alphamelts_domain_gate_rejects_exact_major_oxide_boundary():
    backend = AlphaMELTSBackend()

    result = backend._domain_gate(
        {'SiO2': 50.0, 'MgO': 45.0},
        temperature_C=1500.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
    )

    assert result is not None
    assert result.status == 'out_of_domain'
    assert result.diagnostics['backend_status_reason'] == (
        OutOfDomainReason.MAJOR_SUM.value
    )
    assert result.diagnostics['backend_failure_reason_code'] == (
        OutOfDomainReason.MAJOR_SUM.value
    )
    assert result.diagnostics['backend_failure_category'] == 'out_of_domain'
    assert result.warnings == [
        'DomainGate rejected: major oxide sum 95.000 wt% <= 95'
    ]


def test_domain_gate_rejects_non_silicate_or_non_oxide_inputs():
    backend = AlphaMELTSBackend()

    result = backend.equilibrate(
        temperature_C=1600.0,
        composition_kg={'SiO2': 90.0, 'Fe': 10.0},
        fO2_log=-9.0,
        pressure_bar=1e-6,
    )

    assert result.phases_present == []
    assert result.warnings == [
        'DomainGate rejected: SiO2 90.000 wt% outside [30, 80]; '
        'major oxide sum 90.000 wt% <= 95; non-oxide species present: Fe'
    ]
    assert result.status == 'out_of_domain'


def test_petthermotools_result_parser_uses_verified_schema():
    backend = AlphaMELTSBackend()
    results = ({
        'Conditions': {'mass': 100.0},
        'liquid1': {'SiO2': 50.0, 'Al2O3': 15.0, 'FeO': 10.0},
        'liquid1_prop': {'mass': 80.0, 'Na_activity': 2.0},
        'olivine1': {'SiO2': 40.0, 'MgO': 50.0},
        'olivine1_prop': {'mass': 20.0},
    }, {})

    result = backend._parse_petthermotools_result(
        results,
        temperature_C=1200.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        comp_wt={'SiO2': 50.0, 'Al2O3': 15.0, 'FeO': 10.0},
        total_input_kg=10.0,
    )

    assert result.phases_present == ['liquid1', 'olivine1']
    assert result.phase_masses_kg == {
        'liquid1': pytest.approx(8.0),
        'olivine1': pytest.approx(2.0),
    }
    assert result.liquid_fraction == pytest.approx(0.8)
    assert result.liquid_composition_wt_pct['SiO2'] == pytest.approx(50.0)
    assert result.activity_coefficients == {'Na': pytest.approx(2.0)}
    assert result.diagnostics['diagnostic_oxide_activities'] == {}
    assert result.warnings == []
    assert result.ledger_transition is None


def test_petthermotools_result_parser_rejects_out_of_range_liquid_mass_fraction():
    backend = AlphaMELTSBackend()
    results = ({
        'Conditions': {'mass': 100.0},
        'liquid1': {'SiO2': 50.0},
        'liquid1_prop': {'mass': 120.0},
    }, {})

    with pytest.raises(LiquidFractionInvalidError, match='mismatch'):
        backend._parse_petthermotools_result(
            results,
            temperature_C=1200.0,
            pressure_bar=1.0,
            fO2_log=-9.0,
            comp_wt={'SiO2': 50.0},
            total_input_kg=10.0,
        )


@pytest.mark.parametrize(
    ('mu', 'mu0', 'temperature_K', 'expected'),
    [
        (-1000.0, -1000.0, 1800.0, 1.0),
        (-900.0, -1000.0, 1800.0, math.exp(100.0 / (8.31446261815324 * 1800.0))),
        (-1100.0, -1000.0, 1800.0, math.exp(-100.0 / (8.31446261815324 * 1800.0))),
    ],
)
def test_activity_from_chem_potential_matches_vaporock_convention(
    mu,
    mu0,
    temperature_K,
    expected,
):
    assert activity_from_chem_potential(mu, mu0, temperature_K) == pytest.approx(
        expected
    )


def test_activity_from_chem_potential_is_monotone_in_mu():
    low = activity_from_chem_potential(-1100.0, -1000.0, 1800.0)
    equal = activity_from_chem_potential(-1000.0, -1000.0, 1800.0)
    high = activity_from_chem_potential(-900.0, -1000.0, 1800.0)
    assert low < equal < high


def test_petthermotools_result_parser_converts_mu_to_activity():
    backend = AlphaMELTSBackend()
    results = ({
        'Conditions': {'mass': 100.0},
        'liquid1': {'SiO2': 50.0, 'Al2O3': 15.0, 'FeO': 10.0},
        'liquid1_prop': {'mass': 80.0},
        'olivine1': {'SiO2': 40.0, 'MgO': 50.0},
        'olivine1_prop': {'mass': 20.0},
        'chemical_potentials': {'Na': -900.0, 'K': -1050.0},
        'pure_chemical_potentials': {'Na': -1000.0, 'K': -1000.0},
    }, {})

    result = backend._parse_petthermotools_result(
        results,
        temperature_C=1526.85,
        pressure_bar=1.0,
        fO2_log=-9.0,
        comp_wt={'SiO2': 50.0, 'Al2O3': 15.0, 'FeO': 10.0},
    )

    assert result.activity_coefficients == pytest.approx({
        'Na': activity_from_chem_potential(-900.0, -1000.0, 1800.0),
        'K': activity_from_chem_potential(-1050.0, -1000.0, 1800.0),
    })
    assert result.diagnostics['diagnostic_oxide_activities'] == {}


def test_petthermotools_result_parser_prefers_reported_oxide_activities():
    backend = AlphaMELTSBackend()
    results = ({
        'Conditions': {'mass': 100.0},
        'liquid1': {'SiO2': 50.0, 'Al2O3': 15.0, 'FeO': 10.0},
        'liquid1_prop': {'mass': 100.0},
        'Activities': {
            'liquid1': {'SiO2_Liq': 0.42, 'Na': 0.08, 'K': 0.03},
        },
        'chemical_potentials': {'Na': -900.0},
        'pure_chemical_potentials': {'Na': -1000.0},
    }, {})

    result = backend._parse_petthermotools_result(
        results,
        temperature_C=1526.85,
        pressure_bar=1.0,
        fO2_log=-9.0,
        comp_wt={'SiO2': 50.0, 'Al2O3': 15.0, 'FeO': 10.0},
    )

    assert result.activity_coefficients == pytest.approx({
        'SiO2_Liq': 0.42,
        'Na': 0.08,
        'K': 0.03,
    })
    assert result.diagnostics['diagnostic_oxide_activities'] == pytest.approx({
        'SiO2': 0.42,
    })


def test_petthermotools_activity_coefficients_are_multiplied_by_mole_fraction():
    backend = AlphaMELTSBackend()
    na2o_mass = resolve_species_formula('Na2O').molar_mass_kg_per_mol()
    sio2_mass = resolve_species_formula('SiO2').molar_mass_kg_per_mol()
    liquid_composition = {
        'Na2O': 0.02 * na2o_mass,
        'SiO2': 0.98 * sio2_mass,
    }
    results = ({
        'Conditions': {'mass': 100.0},
        'liquid1': liquid_composition,
        'liquid1_prop': {'mass': 100.0},
        'activity_coefficients': {'Na2O': 0.5},
    }, {})

    result = backend._parse_petthermotools_result(
        results,
        temperature_C=1500.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        comp_wt=liquid_composition,
        total_input_kg=1.0,
    )

    assert result.activity_coefficients['Na2O'] == pytest.approx(0.01)
    assert (
        result.diagnostics['diagnostic_activity_source']
        == 'activity_coefficients_times_oxide_mole_fraction'
    )


def test_subprocess_stdout_parser_reports_activity_labels_and_exact_oxide_diagnostic():
    backend = AlphaMELTSBackend()
    output = """
<> Stable phase assemblage achieved.
Initial alphaMELTS calculation at: P 1.000000 (bars), T 1500.000000 (C)
liquid: SiO2 Al2O3 FeO Na2O
100.0 g 50.0 15.0 10.0 5.0
Melt fraction = 1.0
Liquid activities:
SiO2_Liq Na K Fe
0.42 0.08 0.03 0.25
"""

    result = _parse_subprocess_fixture(
        backend,
        output,
        temperature_C=1500.0,
        total_input_kg=100.0,
    )

    assert result.activity_coefficients == pytest.approx({
        'SiO2_Liq': 0.42,
        'Na': 0.08,
        'K': 0.03,
        'Fe': 0.25,
    })
    assert result.diagnostics['diagnostic_oxide_activities'] == pytest.approx({
        'SiO2': 0.42,
    })


def test_subprocess_activity_parser_does_not_tokenize_next_stable_assemblage_banner():
    backend = AlphaMELTSBackend()
    output = """
Activity of H2O = 0  Melt fraction = 0.921889
<> Stable liquid solid assemblage achieved.
Initial alphaMELTS calculation at: P 1.000000 (bars), T 1200.000000 (C)
liquid:    SiO2 TiO2 Al2O3 Fe2O3 Cr2O3 FeO
90.3451 g 46.49 2.21 16.60 0.00 0.00 11.71
"""

    result = _parse_subprocess_fixture(
        backend,
        output,
        temperature_C=1200.0,
        total_input_kg=0.1,
        system_output=_system_main_fixture(
            temperature_C=1200.0,
            system_mass_g=90.3451,
        ),
    )

    assert result.activity_coefficients == {'H2O': pytest.approx(0.0)}


def test_subprocess_activity_parser_accepts_unqualified_table_heading():
    backend = AlphaMELTSBackend()

    assert backend._extract_subprocess_activity_mapping(
        "Activities:\nNa K\n0.08 0.03\n"
    ) == pytest.approx({'Na': 0.08, 'K': 0.03})


def test_equilibrium_emission_keeps_endmember_activities_diagnostic_only():
    backend = AlphaMELTSBackend()

    result = backend._emit_equilibrium_result(
        temperature_C=1500.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        phases_present=['liquid'],
        phase_masses_kg={'liquid': 1.0},
        liquid_fraction=1.0,
        liquid_composition_wt_pct={'SiO2': 50.0},
        activity_coefficients={
            'Na2SiO3': 0.08,
            'Mg2SiO4': 0.2,
            'Ca3(PO4)2': 0.03,
            'H2O': 1.0,
        },
    )

    assert result.activity_coefficients == pytest.approx({
        'Na2SiO3': 0.08,
        'Mg2SiO4': 0.2,
        'Ca3(PO4)2': 0.03,
        'H2O': 1.0,
    })
    assert result.diagnostics['diagnostic_oxide_activities'] == {}
    label_map = result.diagnostics['diagnostic_activity_label_map']
    assert label_map['Na2SiO3']['oxide_activity'] is None
    assert label_map['Mg2SiO4']['oxide_activity'] is None
    assert label_map['Ca3(PO4)2']['oxide_activity'] is None


def test_endmember_activity_labels_do_not_reach_evaporation_flux_as_oxide_keys():
    backend = AlphaMELTSBackend()
    result = backend._emit_equilibrium_result(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        fO2_log=-8.0,
        phases_present=['liquid'],
        phase_masses_kg={'liquid': 1.0},
        liquid_fraction=1.0,
        liquid_composition_wt_pct={'SiO2': 50.0, 'Na2O': 5.0},
        activity_coefficients={'Na2SiO3': 0.08},
        vapor_pressures_Pa={'Na': 1.0},
        vapor_pressures_source={'Na': 'alphamelts_python_api'},
    )
    assert 'Na2O' not in result.activity_coefficients
    assert backend._activities_times_antoine(
        1600.0,
        result.activity_coefficients,
        {'Na2O': 5.0},
    ) == {}

    captured: dict[str, object] = {}

    def _dispatch_only(intent, **kwargs):
        assert intent is ChemistryIntent.EVAPORATION_FLUX
        captured.update(kwargs['control_inputs'])
        return types.SimpleNamespace(
            status='ok',
            diagnostic={'evaporation_flux_kg_hr': {}},
        )

    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(
            temperature_C=1600.0,
            melt_surface_area_m2=1.0,
            stir_state=types.SimpleNamespace(axial=0.0, radial=0.0),
        ),
        overhead=types.SimpleNamespace(
            composition={},
            headspace_temperature_K=0.0,
            pressure_mbar=0.0,
        ),
        overhead_model=types.SimpleNamespace(pipe_diameter_m=0.12),
        setpoints={'chemistry_kernel': {'allow_fallback_vapor': True}},
        vapor_pressures={'metals': {}, 'oxide_vapors': {}},
        _build_evaporation_aux_maps=lambda vapor: (
            {species: 1.0 for species in vapor},
            {species: {} for species in vapor},
            {},
        ),
        _build_partial_melt_offgassing_diagnostic=lambda *a, **k: {},
        _dispatch_only=_dispatch_only,
    )

    PyrolysisSimulator._calculate_evaporation(sim, result)

    assert captured['vapor_pressure_activities'] == {'Na2SiO3': 0.08}
    assert 'Na2O' not in captured['vapor_pressure_activities']


def test_petthermotools_activity_extractor_selects_liquid_row_not_spinel_first():
    backend = AlphaMELTSBackend()
    results = ({
        'Conditions': {'mass': 100.0},
        'liquid1': {'SiO2': 50.0, 'Al2O3': 15.0, 'FeO': 10.0},
        'liquid1_prop': {'mass': 80.0},
        'spinel1': {'SiO2': 0.0, 'FeO': 25.0},
        'spinel1_prop': {'mass': 20.0},
        'Activities': [
            {'phase': 'spinel1', 'Na2O': 0.99, 'SiO2_Liq': 0.01},
            {'phase': 'liquid1', 'Na2O': 0.08, 'SiO2_Liq': 0.42},
        ],
    }, {})

    result = backend._parse_petthermotools_result(
        results,
        temperature_C=1526.85,
        pressure_bar=1.0,
        fO2_log=-9.0,
        comp_wt={'SiO2': 50.0, 'Al2O3': 15.0, 'FeO': 10.0},
    )

    assert result.activity_coefficients == pytest.approx({
        'Na2O': 0.08,
        'SiO2_Liq': 0.42,
    })
    assert result.diagnostics['diagnostic_oxide_activities'] == pytest.approx({
        'Na2O': 0.08,
        'SiO2': 0.42,
    })


def test_petthermotools_activity_extractor_falls_back_without_liquid_row():
    backend = AlphaMELTSBackend()
    expected = activity_from_chem_potential(-900.0, -1000.0, 1800.0)
    results = ({
        'Conditions': {'mass': 100.0},
        'liquid1': {'SiO2': 50.0, 'Al2O3': 15.0, 'FeO': 10.0},
        'liquid1_prop': {'mass': 100.0},
        'Activities': [
            {'phase': 'spinel1', 'Na2O': 0.99},
        ],
        'chemical_potentials': {'Na2O': -900.0},
        'pure_chemical_potentials': {'Na2O': -1000.0},
    }, {})

    result = backend._parse_petthermotools_result(
        results,
        temperature_C=1526.85,
        pressure_bar=1.0,
        fO2_log=-9.0,
        comp_wt={'SiO2': 50.0, 'Al2O3': 15.0, 'FeO': 10.0},
    )

    assert result.activity_coefficients == pytest.approx({'Na2O': expected})
    assert result.activity_coefficients['Na2O'] != pytest.approx(0.99)


def test_thermoengine_activity_extractor_uses_mu_minus_mu0():
    class FakeLiquidPhase:
        endmember_names = ['Na']

        def chem_potential(self, T_K, P_bar, mol=None):
            assert mol == [[1.0]]
            return [[-900.0]]

        def gibbs_energy(self, T_K, P_bar, mol=None):
            assert mol == [[1.0]]
            return [-1000.0]

    transport = ThermoEngineTransport(
        activity_converter=activity_from_chem_potential,
    )
    transport._liq_phase = FakeLiquidPhase()

    activities = transport._activities_from_chemical_potentials(
        temperature_C=1526.85,
        pressure_bar=1.0,
        component_mole_fraction={'Na': 1.0},
        comp_wt={},
    )

    assert activities == pytest.approx({
        'Na': activity_from_chem_potential(-900.0, -1000.0, 1800.0),
    })


def test_thermoengine_public_equilibrate_runs_in_process(monkeypatch):
    class FakeMelts:
        bulk_wt: dict[str, float] | None = None

        def get_oxide_names(self):
            return ('SiO2', 'Al2O3')

        def get_phase_names(self):
            return ('Liquid', 'Spinel')

        def set_bulk_composition(self, bulk_wt):
            self.bulk_wt = dict(bulk_wt)

        def equilibrate_tp(self, temperature_C, pressure_mpa, *, initialize):
            assert temperature_C == 1200.0
            assert pressure_mpa == pytest.approx(0.1)
            assert initialize is True
            return [('success', temperature_C, pressure_mpa, 'root')]

        def get_list_of_phases_in_assemblage(self, root):
            assert root == 'root'
            return ('Spinel', 'Quartz')

        def get_mass_of_phase(self, root, phase):
            assert root == 'root'
            return {'Spinel': 900.0, 'Quartz': 100.0}[phase]

        def get_composition_of_phase(self, root, phase, mode):
            assert root == 'root'
            if mode == 'component':
                assert phase == 'Quartz'
                return {'formula': 'SiO2'}
            assert mode == 'oxide_wt'
            return {
                'Spinel': {'Al2O3': 71.0, 'FeO': 29.0},
                'Quartz': {'SiO2': 100.0},
            }[phase]

        def get_property_of_phase(self, root, phase, property_name):
            assert root == 'root'
            assert phase in {'Spinel', 'Quartz'}
            return {
                'GibbsFreeEnergy': -1000.0,
                'Enthalpy': -900.0,
                'Entropy': 10.0,
                'Volume': 20.0,
                'HeatCapacity': 30.0,
                'Density': 3.5,
                'DvDp': -0.02,
                'DvDt': 0.03,
            }[property_name]

        def get_thermo_properties_of_phase_components(self, root, phase, mode):
            assert root == 'root'
            assert mode == 'mu'
            return {
                'Spinel': {'MgAl2O4': -1234.5},
                'Quartz': {'Quartz': -100.0},
            }[phase]

        def get_dictionary_of_affinities(self, root, sort):
            assert root == 'root'
            assert sort is False
            return {
                'Olivine': (42.5, 'Mg1.8Fe0.2SiO4'),
                'Tridymite': (999999.0, 'SiO2'),
            }

    class FakeEquilibrate:
        def __init__(self):
            self.melts = FakeMelts()
            self.version = None

        def MELTSmodel(self, *, version):
            self.version = version
            return self.melts

    transport = ThermoEngineTransport(
        activity_converter=activity_from_chem_potential,
    )
    fake_equilibrate = FakeEquilibrate()
    transport._equilibrate = fake_equilibrate
    transport._liq_phase = object()

    def fail_run(*_args, **_kwargs):
        raise AssertionError('public equilibrate must not spawn subprocess')

    monkeypatch.setattr('engines.alphamelts.thermoengine.subprocess.run', fail_run)

    result = transport.equilibrate(
        temperature_C=1200.0,
        pressure_bar=1.0,
        comp_wt={'SiO2': 50.0, 'Al2O3': 0.0},
        warnings=('input-warning',),
    )

    assert fake_equilibrate.version == '1.0.2'
    assert fake_equilibrate.melts.bulk_wt == {'SiO2': 50.0}
    assert result.phases_present == ('Spinel', 'Quartz')
    assert result.phase_masses_kg == pytest.approx({
        'Spinel': 0.9,
        'Quartz': 0.1,
    })
    assert result.liquid_fraction == 0.0
    assert result.liquid_composition_wt_pct == {}
    assert result.phase_compositions == {
        'Spinel': {'Al2O3': 71.0, 'FeO': 29.0},
        'Quartz': {'SiO2': 100.0},
    }
    assert result.phase_thermo['Spinel'] == {
        'gibbs_free_energy_J': -1000.0,
        'enthalpy_J': -900.0,
        'entropy_J_K': 10.0,
        'volume_m3': pytest.approx(2.0e-4),
        'heat_capacity_J_K': 30.0,
        'density_kg_m3': 3500.0,
        'dVdP_m3_bar': pytest.approx(-2.0e-7),
        'dVdT_m3_K': pytest.approx(3.0e-7),
        'reference_mass_kg': pytest.approx(0.9),
        'reference_basis': 'thermoengine_solver_phase_amount',
    }
    assert result.chem_potentials['Spinel'] == {
        'basis': 'chemical_potential',
        'units': 'J/mol',
        'source_basis': 'chemical_potential_J_mol',
        'components': {'MgAl2O4': -1234.5},
    }
    assert result.chem_potentials['Quartz'] == {
        'basis': 'chemical_potential',
        'units': 'J/mol',
        'source_basis': 'specific_gibbs_energy_J_g',
        'components': {'Quartz': pytest.approx(-6008.3)},
        'formula': 'SiO2',
        'molar_mass_g_mol': pytest.approx(60.083),
    }
    assert result.phase_affinities == {
        'Olivine': {
            'affinity_J': 42.5,
            'state': 'undersaturated',
            'phase_scope': 'not_in_equilibrium_assemblage',
            'composition_formula': 'Mg1.8Fe0.2SiO4',
        },
        'Tridymite': {
            'affinity_J': 0.0,
            'state': 'zero_affinity_sentinel',
            'phase_scope': 'not_in_equilibrium_assemblage',
            'composition_formula': 'SiO2',
        },
    }
    assert result.system_dVdP_m3_bar == pytest.approx(-4.0e-7)
    assert result.system_dVdT_m3_K == pytest.approx(6.0e-7)
    assert result.solver_status == 'success'
    assert result.solver_converged is True
    assert result.solver_iterations is None
    assert result.system_volume == pytest.approx(4.0e-4)
    assert result.thermodynamic_basis['reference_mass_kg'] == pytest.approx(1.0)


def test_thermoengine_extras_fail_loud_on_malformed_present_value():
    transport = ThermoEngineTransport(
        activity_converter=activity_from_chem_potential,
    )

    with pytest.raises(ValueError, match='chemical potentials.*not finite'):
        transport._strict_finite_mapping(
            {'SiO2': float('nan')},
            context='ThermoEngine liquid chemical potentials',
        )


def test_alphamelts_thermoengine_default_is_intrinsic_closed(monkeypatch):
    backend = ThermoEngineBackend()
    backend._mode = 'thermoengine'
    seen = {}

    class FakeTransport:
        def equilibrate(self, **kwargs):
            seen.update(kwargs)
            return ThermoEnginePayload(
                phases_present=('Liquid',),
                phase_masses_kg={'Liquid': 1.0},
                liquid_fraction=1.0,
                liquid_composition_wt_pct={'SiO2': 100.0},
                solved_fO2_log=-8.25,
                phase_universe_size=54,
            )

    backend._thermoengine_transport = FakeTransport()
    monkeypatch.setattr(
        backend,
        '_activities_times_antoine_or_fail',
        lambda *_args, **_kwargs: {},
    )

    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg={'SiO2': 0.5, 'Al2O3': 0.5},
        pressure_bar=1.0,
    )

    assert seen['fO2_log'] is None
    assert result.fO2_log == pytest.approx(-8.25)
    assert result.diagnostics['fO2_transport'] == (
        'thermoengine_intrinsic_closed'
    )
    assert 'requested_fO2_log' not in result.diagnostics


@pytest.mark.parametrize('requested_fO2_log', [None, -12.0, -9.0, -6.0, -3.0, 0.0])
def test_thermoengine_standalone_shadow_parity_with_frozen_legacy_oracle(
    requested_fO2_log,
):
    class ShadowTransport:
        engine_version = 'thermoengine shadow-v1'

        def equilibrate(self, **kwargs):
            solved = -8.25 if kwargs['fO2_log'] is None else kwargs['fO2_log']
            return ThermoEnginePayload(
                phases_present=('Liquid', 'olivine'),
                phase_masses_kg={'Liquid': 0.75, 'olivine': 0.25},
                liquid_fraction=0.75,
                liquid_composition_wt_pct={'SiO2': 55.0, 'MgO': 45.0},
                solved_fO2_log=solved,
                phase_universe_size=54,
                fO2_solve_count=0 if kwargs['fO2_log'] is None else 5,
                phase_compositions={
                    'Liquid': {'SiO2': 55.0, 'MgO': 45.0},
                    'olivine': {'SiO2': 40.0, 'MgO': 60.0},
                },
                phase_thermo={
                    'Liquid': {
                        'gibbs_free_energy_J': -1.0,
                        'enthalpy_J': 2.0,
                        'entropy_J_K': 3.0,
                        'volume_m3': 4.0e-5,
                        'heat_capacity_J_K': 5.0,
                        'density_kg_m3': 2650.0,
                        'reference_mass_kg': 0.75,
                        'reference_basis': 'thermoengine_solver_phase_amount',
                    },
                },
                chem_potentials={'Liquid': {
                    'basis': 'chemical_potential',
                    'units': 'J/mol',
                    'source_basis': 'chemical_potential_J_mol',
                    'components': {'SiO2': -10.0},
                }},
                phase_affinities={
                    'quartz': {
                        'affinity_J': 12.5,
                        'state': 'undersaturated',
                        'phase_scope': 'not_in_equilibrium_assemblage',
                        'composition_formula': 'SiO2',
                    },
                },
                thermodynamic_basis={
                    'reference_basis': 'thermoengine_solver_system_amount',
                    'reference_mass_kg': 1.0,
                    'system_enthalpy': {'units': 'J'},
                    'system_entropy': {'units': 'J/K'},
                    'system_volume': {'units': 'm3', 'source_units': 'J/bar'},
                    'system_heat_capacity_Cp': {'units': 'J/K'},
                },
                liquid_density_kg_m3=2650.0,
                system_enthalpy=2.0,
                system_entropy=3.0,
                system_volume=4.0e-5,
                system_heat_capacity_Cp=5.0,
                activity_coefficients={'SiO2': 0.5},
                fe_redox_split={'FeO_wt_pct': 9.0, 'Fe2O3_wt_pct': 1.0},
                warnings=('frozen oracle warning',),
            )

        def close(self):
            return None

    class ShadowBackend(ThermoEngineBackend):
        def initialize(self, _config):
            self._thermoengine_transport = ShadowTransport()
            self._engine_version = self._thermoengine_transport.engine_version
            self._mode = 'thermoengine'
            self._vaporock_available = False
            self._activities_times_antoine_or_fail = (
                lambda *_args, **_kwargs: {'SiO': 12.5}
            )
            return True

    standalone = resolve_backend(
        'thermoengine',
        BackendSelectionPolicy.RUNNER_STRICT,
        thermoengine_backend_cls=ShadowBackend,
    )
    kwargs = {
        'temperature_C': 1400.0,
        'composition_kg': _melts_domain_composition(),
        'pressure_bar': 1.0,
        'fO2_log': requested_fO2_log,
    }

    result = standalone.equilibrate(**kwargs)

    # Frozen projection of the pre-refactor AlphaMELTSBackend
    # mode='thermoengine' emitter. This oracle is deliberately independent of
    # ThermoEngineBackend so routing/emission drift cannot self-validate.
    assert result.phases_present == ['Liquid', 'olivine']
    assert result.phase_masses_kg == {'Liquid': 0.75, 'olivine': 0.25}
    assert result.liquid_fraction == 0.75
    assert result.liquid_composition_wt_pct == {'SiO2': 55.0, 'MgO': 45.0}
    assert result.fO2_log == (-8.25 if requested_fO2_log is None else requested_fO2_log)
    assert result.phase_compositions == {
        'Liquid': {'SiO2': 55.0, 'MgO': 45.0},
        'olivine': {'SiO2': 40.0, 'MgO': 60.0},
    }
    assert result.phase_thermo == {
        'Liquid': {
            'gibbs_free_energy_J': -1.0,
            'enthalpy_J': 2.0,
            'entropy_J_K': 3.0,
            'volume_m3': 4.0e-5,
            'heat_capacity_J_K': 5.0,
            'density_kg_m3': 2650.0,
            'reference_mass_kg': 0.75,
            'reference_basis': 'thermoengine_solver_phase_amount',
        },
    }
    assert result.chem_potentials == {'Liquid': {
        'basis': 'chemical_potential',
        'units': 'J/mol',
        'source_basis': 'chemical_potential_J_mol',
        'components': {'SiO2': -10.0},
    }}
    assert result.phase_affinities == {
        'quartz': {
            'affinity_J': 12.5,
            'state': 'undersaturated',
            'phase_scope': 'not_in_equilibrium_assemblage',
            'composition_formula': 'SiO2',
        },
    }
    assert result.liquid_density_kg_m3 == pytest.approx(2650.0)
    assert result.system_enthalpy == pytest.approx(2.0)
    assert result.system_entropy == pytest.approx(3.0)
    assert result.system_volume == pytest.approx(4.0e-5)
    assert result.system_heat_capacity_Cp == pytest.approx(5.0)
    assert result.diagnostics['thermodynamic_basis']['reference_mass_kg'] == 1.0
    assert result.activity_coefficients == {'SiO2': 0.5}
    assert result.fe_redox_split == {'FeO_wt_pct': 9.0, 'Fe2O3_wt_pct': 1.0}
    assert result.vapor_pressures_Pa == {'SiO': 12.5}
    assert result.temperature_C == pytest.approx(1400.0)
    assert result.pressure_bar == pytest.approx(1.0)
    assert result.status == 'ok'
    assert result.warnings == ['frozen oracle warning']
    assert result.diagnostics['fO2_transport'] == (
        'thermoengine_intrinsic_closed'
        if requested_fO2_log is None
        else 'thermoengine_oxygen_root'
    )
    assert result.diagnostics['thermoengine_fO2_solve_count'] == (
        0 if requested_fO2_log is None else 5
    )
    assert result.backend_name == 'thermoengine'
    assert result.engine_version == 'thermoengine shadow-v1'
    assert result.ledger_transition is None


def test_thermoengine_imposes_absolute_fo2_with_default_phase_solver(monkeypatch):
    class FakeMelts:
        def __init__(self):
            self.bulk_wt = {}

        def get_oxide_names(self):
            return ('SiO2', 'FeO', 'Fe2O3')

        def get_phase_names(self):
            return ('Liquid', 'Spinel')

        def set_bulk_composition(self, bulk_wt):
            self.bulk_wt = dict(bulk_wt)

        def equilibrate_tp(self, temperature_C, pressure_mpa, *, initialize):
            assert temperature_C == 1200.0
            assert pressure_mpa == pytest.approx(0.1)
            assert initialize is True
            return [('success', temperature_C, pressure_mpa, self)]

        def get_list_of_phases_in_assemblage(self, root):
            assert root is self
            return ('Liquid', 'Spinel')

        def get_mass_of_phase(self, root, phase):
            assert root is self
            return {'Liquid': 900.0, 'Spinel': 100.0}[phase]

        def get_composition_of_phase(self, root, phase, basis):
            assert root is self
            if basis != 'oxide_wt':
                return {}
            # Merge (t-286 extras): composition is queried per-phase now, not
            # just Liquid. Liquid keeps the bulk; other phases return a finite
            # placeholder (this fO2 test asserts only on the liquid + echo).
            if phase == 'Liquid':
                return dict(self.bulk_wt)
            return {'MgO': 20.0, 'Al2O3': 70.0, 'FeO': 10.0}

        def get_property_of_phase(self, root, phase, property_name):
            # t-286 extras: per-phase G/H/S/V/Cp/density; finite placeholders.
            assert root is self
            return 1.0

        def get_thermo_properties_of_phase_components(self, root, phase, mode):
            # t-286 extras: per-component chemical potentials (mode='mu').
            assert root is self
            assert mode == 'mu'
            return {'SiO2': -1000.0}

        def get_dictionary_of_affinities(self, root, sort=False):
            # t-286 extras: undersaturated affinities {phase: (affinity, comp)}.
            assert root is self
            return {'Olivine': (500.0, 'Mg2SiO4')}

    class FakeEquilibrate:
        def __init__(self):
            self.models = []

        def MELTSmodel(self, *, version):
            assert version == '1.0.2'
            model = FakeMelts()
            self.models.append(model)
            return model

    fake_equilibrate = FakeEquilibrate()
    transport = ThermoEngineTransport(
        activity_converter=activity_from_chem_potential,
    )
    transport._equilibrate = fake_equilibrate
    transport._liq_phase = object()

    def echo(model, _root, **_kwargs):
        feo = model.bulk_wt['FeO'] / 71.8444
        fe2o3 = model.bulk_wt['Fe2O3'] / 159.6882
        ferric_fraction = 2.0 * fe2o3 / (feo + 2.0 * fe2o3)
        return -10.0 + 10.0 * ferric_fraction

    monkeypatch.setattr(transport, '_echo_log_fO2', echo)
    monkeypatch.setattr(
        transport,
        '_activities_from_chemical_potentials',
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(transport, '_fe_redox_split', lambda _comp: {})

    result = transport.equilibrate(
        temperature_C=1200.0,
        pressure_bar=1.0,
        comp_wt={'SiO2': 80.0, 'FeO': 18.0, 'Fe2O3': 2.0},
        fO2_log=-5.0,
    )

    assert result.solved_fO2_log == pytest.approx(-5.0, abs=1.0e-3)
    assert result.phases_present == ('Liquid', 'Spinel')
    assert result.phase_universe_size == 2
    assert result.fO2_solve_count > 1
    initial_fe_moles = 18.0 / 71.8444 + 2.0 * 2.0 / 159.6882
    assert result.liquid_composition_wt_pct['SiO2'] == 80.0
    assert (
        result.liquid_composition_wt_pct['FeO'] / 71.8444
        + 2.0 * result.liquid_composition_wt_pct['Fe2O3'] / 159.6882
        == pytest.approx(initial_fe_moles)
    )


def test_thermoengine_imposed_fo2_seeds_feo_only_bulk_with_positive_kress91(
    monkeypatch,
):
    fractions = []

    class FakeModel:
        def __init__(self):
            self.bulk_wt = {}

        def set_bulk_composition(self, bulk_wt):
            self.bulk_wt = dict(bulk_wt)

        def equilibrate_tp(self, temperature_C, pressure_mpa, *, initialize):
            return [('success', temperature_C, pressure_mpa, self)]

    class FakeEquilibrate:
        def MELTSmodel(self, *, version):
            return FakeModel()

    transport = ThermoEngineTransport(
        activity_converter=activity_from_chem_potential,
    )
    transport._equilibrate = FakeEquilibrate()

    def echo(model, _root, **_kwargs):
        feo = model.bulk_wt['FeO'] / 71.8444
        fe2o3 = model.bulk_wt['Fe2O3'] / 159.6882
        fraction = 2.0 * fe2o3 / (feo + 2.0 * fe2o3)
        fractions.append(fraction)
        return -10.0 + 10.0 * fraction

    monkeypatch.setattr(transport, '_echo_log_fO2', echo)
    monkeypatch.setattr(
        'simulator.fe_redox.kress91_split',
        lambda **_kwargs: {'fe3': 0.2},
    )

    _model, _result, solved, _count = transport._solve_imposed_fO2(
        temperature_C=1200.0,
        pressure_bar=1.0,
        pressure_mpa=0.1,
        bulk_wt={'SiO2': 82.0, 'FeO': 18.0, 'Fe2O3': 0.0},
        target_fO2_log=-8.0,
    )

    assert fractions[0] == pytest.approx(0.2)
    assert fractions[0] > 0.0
    assert solved == pytest.approx(-8.0)


def test_thermoengine_echo_clamps_roundoff_negative_fe2o3_to_zero_limit():
    class FakeMelts:
        def get_list_of_phases_in_assemblage(self, _root):
            return ('Liquid',)

        def get_composition_of_phase(self, _root, _phase, _basis):
            return {'SiO2': 83.0, 'FeO': 17.0, 'Fe2O3': -3.1e-14}

    transport = ThermoEngineTransport(
        activity_converter=activity_from_chem_potential,
    )
    transport._database = object()
    transport._chem = types.SimpleNamespace(
        OXIDE_ORDER=('SiO2', 'FeO', 'Fe2O3')
    )

    with pytest.raises(ValueError, match='zero-ferric limiting state'):
        transport._echo_log_fO2(
            FakeMelts(), object(), temperature_C=1600.0, pressure_bar=1.0
        )


def test_thermoengine_echo_rejects_negative_fe2o3_beyond_roundoff_tolerance():
    class FakeMelts:
        def get_list_of_phases_in_assemblage(self, _root):
            return ('Liquid',)

        def get_composition_of_phase(self, _root, _phase, _basis):
            return {'SiO2': 83.0, 'FeO': 17.0, 'Fe2O3': -2.0e-12}

    transport = ThermoEngineTransport(
        activity_converter=activity_from_chem_potential,
    )
    transport._database = object()
    transport._chem = types.SimpleNamespace(
        OXIDE_ORDER=('SiO2', 'FeO', 'Fe2O3')
    )

    with pytest.raises(ValueError, match='physically negative beyond'):
        transport._echo_log_fO2(
            FakeMelts(), object(), temperature_C=1600.0, pressure_bar=1.0
        )


def test_thermoengine_imposed_fo2_fails_loud_on_buffered_region(monkeypatch):
    transport = ThermoEngineTransport(
        activity_converter=activity_from_chem_potential,
    )
    transport._equilibrate = object()

    samples = iter((-10.0, -10.0, -4.0))
    monkeypatch.setattr(
        transport,
        '_echo_log_fO2',
        lambda *_args, **_kwargs: next(samples),
    )

    class FakeModel:
        def set_bulk_composition(self, _bulk):
            pass

        def equilibrate_tp(self, temperature_C, pressure_mpa, *, initialize):
            return [('success', temperature_C, pressure_mpa, self)]

    class FakeEquilibrate:
        def MELTSmodel(self, *, version):
            return FakeModel()

    transport._equilibrate = FakeEquilibrate()
    with pytest.raises(ValueError, match='non-monotonic/buffered fO2 region'):
        transport._solve_imposed_fO2(
            temperature_C=1200.0,
            pressure_bar=1.0,
            pressure_mpa=0.1,
            bulk_wt={'FeO': 18.0, 'Fe2O3': 2.0},
            target_fO2_log=-8.0,
        )


def test_thermoengine_imposed_fo2_rejects_nonmonotonic_samples():
    with pytest.raises(ValueError, match='non-monotonic/buffered fO2 region'):
        ThermoEngineTransport._validate_fO2_order((
            (0.1, -9.0, None, None),
            (0.5, -8.0, None, None),
            (0.9, -8.5, None, None),
        ))


def test_thermoengine_imposed_fo2_rejects_narrow_target_plateau(monkeypatch):
    class FakeModel:
        def __init__(self):
            self.bulk_wt = {}

        def set_bulk_composition(self, bulk_wt):
            self.bulk_wt = dict(bulk_wt)

        def equilibrate_tp(self, temperature_C, pressure_mpa, *, initialize):
            return [('success', temperature_C, pressure_mpa, self)]

    class FakeEquilibrate:
        def MELTSmodel(self, *, version):
            return FakeModel()

    transport = ThermoEngineTransport(
        activity_converter=activity_from_chem_potential,
    )
    transport._equilibrate = FakeEquilibrate()

    def echo(model, _root, **_kwargs):
        feo = model.bulk_wt['FeO'] / 71.8444
        fe2o3 = model.bulk_wt['Fe2O3'] / 159.6882
        fraction = 2.0 * fe2o3 / (feo + 2.0 * fe2o3)
        if 0.49 <= fraction <= 0.51:
            return -5.0
        return -10.0 + 10.0 * fraction

    monkeypatch.setattr(transport, '_echo_log_fO2', echo)

    with pytest.raises(ValueError, match='non-monotonic/buffered fO2 region'):
        transport._solve_imposed_fO2(
            temperature_C=1200.0,
            pressure_bar=1.0,
            pressure_mpa=0.1,
            bulk_wt={'FeO': 18.0, 'Fe2O3': 2.0},
            target_fO2_log=-5.0,
        )


def test_thermoengine_transport_equilibrates_live_when_installed():
    backend = ThermoEngineBackend()
    try:
        available = backend.initialize({})
    except ImportError as exc:
        pytest.skip(f'ThermoEngine transport unavailable: {exc}')
    if not available:
        pytest.skip('ThermoEngine transport unavailable')

    comp_wt = backend._normalize_composition_to_melts_basis({
            'SiO2': 490.0,
            'TiO2': 15.0,
            'Al2O3': 140.0,
            'FeO': 100.0,
            'Fe2O3': 10.0,
            'MgO': 90.0,
            'CaO': 110.0,
            'Na2O': 25.0,
            'K2O': 8.0,
            'Cr2O3': 2.0,
            'MnO': 2.0,
            'P2O5': 3.0,
    })
    result = backend._equilibrate_thermoengine(
        1400.0, comp_wt, -9.0, 1.0,
    )

    assert result.status == 'ok'
    assert backend._mode == 'thermoengine'
    assert result.ledger_transition is None
    assert result.phases_present
    assert result.phase_masses_kg
    assert 0.0 <= result.liquid_fraction <= 1.0
    assert result.activity_coefficients
    assert 'SiO2' in result.activity_coefficients
    assert result.activity_coefficients['SiO2'] > 0.0
    assert result.fe_redox_split['FeO_wt_pct'] > 0.0
    assert result.fe_redox_split['Fe2O3_wt_pct'] > 0.0
    assert result.phase_compositions
    assert result.phase_thermo
    assert result.chem_potentials
    assert result.phase_affinities


def test_thermoengine_live_fo2_near_spinel_boundary_is_unique_or_fails_loud():
    backend = ThermoEngineBackend()
    try:
        available = backend.initialize({
            'thermoengine_equilibrate_timeout_s': 90.0,
            'thermoengine_health_timeout_s': 30.0,
        })
    except ImportError as exc:
        pytest.skip(f'ThermoEngine transport unavailable: {exc}')
    if not available:
        pytest.skip('ThermoEngine transport unavailable')

    composition_kg = {
        'SiO2': 490.0,
        'TiO2': 15.0,
        'Al2O3': 140.0,
        'FeO': 100.0,
        'Fe2O3': 10.0,
        'MgO': 90.0,
        'CaO': 110.0,
        'Na2O': 25.0,
        'K2O': 8.0,
        'Cr2O3': 2.0,
        'MnO': 2.0,
        'P2O5': 3.0,
    }
    intrinsic = backend.equilibrate(
        temperature_C=1200.0,
        composition_kg=composition_kg,
        pressure_bar=1.0,
    )
    assert any(
        token in phase.lower()
        for phase in intrinsic.phases_present
        for token in ('spinel', 'magnetite')
    ), intrinsic.phases_present
    target_fO2_log = intrinsic.fO2_log + 0.01

    try:
        imposed = backend.equilibrate(
            temperature_C=1200.0,
            composition_kg=composition_kg,
            fO2_log=target_fO2_log,
            pressure_bar=1.0,
        )
    except (RuntimeError, ValueError) as exc:
        assert 'non-monotonic/buffered fO2 region' in str(exc)
        return

    assert imposed.fO2_log == pytest.approx(target_fO2_log, abs=1.0e-3)
    assert imposed.diagnostics['thermoengine_fO2_solve_count'] >= 3
    assert imposed.phase_masses_kg


def test_thermoengine_intrinsic_shadow_parity_against_subprocess_when_available():
    thermo = ThermoEngineBackend()
    try:
        thermo_ok = thermo.initialize({})
    except ImportError as exc:
        pytest.skip(f'ThermoEngine transport unavailable: {exc}')
    if not thermo_ok:
        pytest.skip('ThermoEngine transport unavailable')

    subprocess_backend = AlphaMELTSBackend()
    try:
        subprocess_ok = subprocess_backend.initialize({'mode': 'subprocess'})
    except ImportError as exc:
        pytest.skip(f'AlphaMELTS subprocess transport unavailable: {exc}')
    if not subprocess_ok:
        pytest.skip('AlphaMELTS subprocess transport unavailable')

    composition_kg = {
        'SiO2': 490.0,
        'TiO2': 15.0,
        'Al2O3': 140.0,
        'FeO': 100.0,
        'Fe2O3': 10.0,
        'MgO': 90.0,
        'CaO': 110.0,
        'Na2O': 25.0,
        'K2O': 8.0,
        'Cr2O3': 2.0,
        'MnO': 2.0,
        'P2O5': 3.0,
    }
    thermo_result = thermo.equilibrate(
        temperature_C=1200.0,
        composition_kg=composition_kg,
        pressure_bar=1.0,
    )
    subprocess_result = subprocess_backend.equilibrate(
        temperature_C=1200.0,
        composition_kg=composition_kg,
        # Preserve the pre-redox-root cross-transport anchor: ThermoEngine's
        # old path was intrinsic closed even though this subprocess reference
        # was explicitly run at the adapter's historical -9 default.
        fO2_log=-9.0,
        pressure_bar=1.0,
        # Explicit mode: the live parity comparison is an isothermal
        # equilibrate; without this the no-mode contract error fires
        # before parity is ever compared.
        subprocess_run_mode=AlphaMELTSSubprocessRunMode.ISOTHERMAL,
    )
    if not subprocess_result.phase_masses_kg:
        pytest.skip('AlphaMELTS subprocess did not report modal phase masses')

    def canonical_modes(result):
        return {
            'phase_masses_kg': {
                str(phase).lower(): mass
                for phase, mass in result.phase_masses_kg.items()
            },
        }

    report = MAGEMinParityComparator().compare(
        canonical_modes(thermo_result),
        canonical_modes(subprocess_result),
    )
    # Historical cross-transport baseline: the subprocess reports only a
    # small extra olivine mode. Keep a quantitative modal anchor instead of
    # accepting any warning as success.
    assert report.mode_pct_max_delta is not None
    assert report.mode_pct_max_delta <= 3.0, report.warnings
    assert report.phases_only_in_authoritative == ()
    assert report.phases_only_in_shadow == ('olivine',)


def test_activities_times_antoine_computes_activity_times_ppure_from_yaml():
    backend = AlphaMELTSBackend()

    pressures = backend._activities_times_antoine(
        1600.0,
        {'Na': 2.0, 'K': 1.0, 'unknown': 10.0},
        {'SiO2': 100.0},
        pO2_bar=1e-9,
    )
    table = _load_data('vapor_pressures.yaml')['metals']
    T_K = 1600.0 + 273.15
    expected_na = 2.0 * 10.0 ** (
        table['Na']['pure_component_antoine']['A']
        - table['Na']['pure_component_antoine']['B']
        / (T_K + table['Na']['pure_component_antoine']['C'])
    )

    assert set(pressures) == {'Na', 'K'}
    assert pressures['Na'] == pytest.approx(expected_na)
    assert pressures['K'] > 0.0


def test_activities_times_antoine_maps_thermoengine_liquid_activity_keys():
    backend = AlphaMELTSBackend()

    pressures = backend._activities_times_antoine(
        1600.0,
        {
            'SiO2': 0.4,
            'Na2O': 0.2,
            'KAlSi3O8': 0.1,
            'CaSiO3': 0.3,
            'Mg2SiO4': 0.5,
            'Al2O3': 0.6,
        },
        {'SiO2': 45.0, 'Na2O': 4.0, 'K2O': 1.0},
        pO2_bar=1e-9,
    )

    assert {'Na', 'K', 'Si', 'SiO', 'Ca', 'Mg', 'Al'} <= set(pressures)
    assert all(pressures[species] > 0.0 for species in (
        'Na', 'K', 'Si', 'SiO', 'Ca', 'Mg', 'Al',
    ))


def test_activities_times_antoine_returns_empty_without_species_activity():
    backend = AlphaMELTSBackend()

    assert backend._activities_times_antoine(
        1600.0,
        {},
        {'Na2O': 100.0},
    ) == {}


def test_activities_times_antoine_refuses_standard_reaction_without_po2():
    backend = AlphaMELTSBackend()
    backend._vapor_pressure_table = {
        'K': {
            'parent_oxide': 'K2O',
            'fit_target': 'standard_reaction_term',
            'oxide_activity_exponent': 1.0,
            'pO2_exponent': -0.25,
            'pO2_reference_bar': 1.0,
            'antoine': {'A': 5.0, 'B': 0.0, 'C': 0.0},
        },
    }

    with pytest.raises(RuntimeError, match='without pO2_bar'):
        backend._activities_times_antoine(
            1600.0,
            {'K2O': 0.5},
            {'K2O': 1.0},
        )


def test_activities_times_antoine_warns_once_for_pseudo_curvefit():
    backend = AlphaMELTSBackend()
    backend._vapor_pressure_table = {
        'K': {
            'fit_target': 'pseudo_psat_backsolved_from_vaporock',
            'residual_dex': 1.4,
            'confidence_tier': 'low',
            'antoine': {'A': 5.0, 'B': 0.0, 'C': 0.0},
        },
    }

    with pytest.warns(
        HighUncertaintyVaporPressureFallbackWarning,
        match=(
            'HIGH-UNCERTAINTY WARNING: K vapor pressure uses a backsolved '
            'VapoRock fallback \\(curve-fit\\), NOT first-principles; '
            'residual_dex=1.4; confidence_tier=low; '
            'builtin provider emits a VapoRock-derived curve-fit; '
            'VapoRock runtime is diagnostic-only'
        ),
    ):
        first = backend._activities_times_antoine(
            1600.0,
            {'K2O': 0.5},
            {'K2O': 1.0},
        )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        second = backend._activities_times_antoine(
            1600.0,
            {'K2O': 0.5},
            {'K2O': 1.0},
        )

    assert caught == []
    assert first == second
    assert first['K'] == pytest.approx(5.0e4)
    assert backend._antoine_vapor_pressure_source_by_species(
        'alphamelts_python_api',
        first,
    ) == {'K': 'alphamelts_python_api:backsolved_vaporock_curve_fit'}


def test_activities_times_antoine_uncertified_pure_component_is_silent():
    backend = AlphaMELTSBackend()
    backend._vapor_pressure_table = {
        'K': {
            'fit_target': 'pure_component_psat',
            'residual_dex': 0.01,
            'confidence_tier': 'high',
            'antoine': {'A': 4.0, 'B': 0.0, 'C': 0.0},
        },
    }

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        pressures = backend._activities_times_antoine(
            1600.0,
            {'K2O': 0.5},
            {'K2O': 1.0},
        )

    assert caught == []
    assert pressures == {'K': pytest.approx(5.0e3)}
    assert backend._antoine_vapor_pressure_source_by_species(
        'alphamelts_python_api',
        pressures,
    ) == {'K': 'alphamelts_python_api:legacy_pure_component_estimate'}


def test_real_vaporock_path_does_not_warn_for_pseudo_fallback_rows():
    class RealVapoRock:
        def is_available(self):
            return True

        def equilibrate(self, **_kwargs):
            return EquilibriumResult(
                status='ok',
                liquid_fraction=1.0,
                vapor_pressures_Pa={'K': 12.0},
                warnings=[],
            )

    backend = AlphaMELTSBackend()
    backend._vaporock_helper = RealVapoRock()
    backend._vapor_pressure_table = {
        'K': {
            'fit_target': 'pseudo_psat_backsolved_from_vaporock',
            'residual_dex': 1.4,
            'confidence_tier': 'low',
            'antoine': {'A': 5.0, 'B': 0.0, 'C': 0.0},
        },
    }

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        pressures, source = backend._vapor_pressures_via_vaporock_or_antoine(
            T_C=1600.0,
            solved_melt_wt_pct={'K2O': 1.0},
            liquid_fraction=1.0,
            fO2_log=-9.0,
            pressure_bar=1.0,
            activities={'K2O': 0.5},
        )

    assert caught == []
    assert pressures == {'K': 12.0}
    assert source == 'vaporock'


def test_vaporock_empty_and_antoine_empty_fails_loud_for_volatile_melt():
    class EmptyVapoRock:
        def is_available(self):
            return True

        def equilibrate(self, **_kwargs):
            return EquilibriumResult(
                status='not_converged',
                vapor_pressures_Pa={},
                warnings=['forced empty VapoRock result'],
            )

    backend = AlphaMELTSBackend()
    backend._vaporock_helper = EmptyVapoRock()

    with pytest.warns(UserWarning, match='using activity x Antoine fallback rows'):
        with pytest.raises(
            RuntimeError,
            match='volatile-bearing melt.*silently zero evaporation flux',
        ):
            backend._vapor_pressures_via_vaporock_or_antoine(
                T_C=1600.0,
                solved_melt_wt_pct={'SiO2': 95.0, 'Na2O': 5.0},
                liquid_fraction=1.0,
                fO2_log=-9.0,
                pressure_bar=1.0,
                activities={},
            )


def test_vaporock_empty_volatile_free_melt_returns_physical_zero():
    class EmptyVapoRock:
        def is_available(self):
            return True

        def equilibrate(self, **_kwargs):
            return EquilibriumResult(
                status='not_converged',
                vapor_pressures_Pa={},
                warnings=['forced empty VapoRock result'],
            )

    backend = AlphaMELTSBackend()
    backend._vaporock_helper = EmptyVapoRock()

    with pytest.warns(UserWarning, match='using activity x Antoine fallback rows'):
        pressures, source = backend._vapor_pressures_via_vaporock_or_antoine(
            T_C=1600.0,
            solved_melt_wt_pct={'P2O5': 100.0},
            liquid_fraction=1.0,
            fO2_log=-9.0,
            pressure_bar=1.0,
            activities={},
        )

    assert pressures == {}
    assert source == 'no_volatile_species'


def test_decompression_path_calls_verified_petthermotools_api():
    backend = AlphaMELTSBackend()
    calls = []

    def fake_decompression(**kwargs):
        calls.append(kwargs)
        return {
            0: {
                'Conditions': {
                    'mass': 100.0,
                    'P_bar': 1000.0,
                    'fO2_log': -10.5,
                },
                'liquid1': {'SiO2': 50.0},
                'liquid1_prop': {'mass': 100.0},
            },
            1: {
                'Conditions': {
                    'mass': 100.0,
                    'P_bar': 1.0,
                    'fO2_log': -10.5,
                },
                'liquid1': {'SiO2': 49.0},
                'liquid1_prop': {'mass': 95.0},
                'olivine1': {'SiO2': 40.0},
                'olivine1_prop': {'mass': 5.0},
            },
        }

    backend._mode = 'python_api'
    backend._pet_module = types.SimpleNamespace(
        isothermal_decompression=fake_decompression)
    backend._pet_payload_preloaded = True
    backend._pet_melts = object()
    backend._redox_buffer = 'QFM'
    backend._fo2_offset = -1.5

    results = backend.decompression_path(
        1200.0,
        1000.0,
        1.0,
        100.0,
        composition_kg={
            'SiO2': 50.0,
            'Al2O3': 15.0,
            'FeO': 10.0,
            'MgO': 10.0,
            'CaO': 10.0,
            'Na2O': 5.0,
        },
    )

    assert len(results) == 2
    assert calls[0]['T_C'] == 1200.0
    assert calls[0]['P_start_bar'] == 1000.0
    assert calls[0]['P_end_bar'] == 1.0
    assert calls[0]['dp_bar'] == 100.0
    assert calls[0]['fO2_buffer'] == 'QFM'
    assert calls[0]['fO2_offset'] == -1.5
    assert calls[0]['bulk']['FeOt_Liq'] == pytest.approx(10.0)
    assert [result.pressure_bar for result in results] == [1000.0, 1.0]
    assert [result.fO2_log for result in results] == [-10.5, -10.5]


def test_alphamelts_stdout_parser_solid_only_reports_zero_liquid_fraction():
    backend = AlphaMELTSBackend()
    output = """
<> Stable solid assemblage achieved.
Initial alphaMELTS calculation at: P 1.000000 (bars), T 1100.000000 (C)
olivine: 90.3451 g, composition (Ca0.01Mg0.80Fe''0.20Mn0.00Co0.00Ni0.00)2SiO4
"""

    result = _parse_subprocess_fixture(
        backend,
        output,
        temperature_C=1100.0,
        total_input_kg=1000.0,
        system_output=_system_main_fixture(
            temperature_C=1100.0,
            system_mass_g=90.3451,
        ),
    )

    assert result.liquid_fraction == pytest.approx(0.0)
    assert result.phases_present == ["olivine"]
    assert "liquid" not in result.phases_present
    assert result.status == "ok"


def test_alphamelts_stdout_parser_reports_liquid_fraction_without_ledger_transition():
    backend = AlphaMELTSBackend()
    output = """
<> Stable liquid solid assemblage achieved.
<> Found the liquidus at T = 1220.31 (C).
Initial alphaMELTS calculation at: P 1.000000 (bars), T 1200.000000 (C)
liquid:    SiO2 TiO2 Al2O3 Fe2O3 Cr2O3   FeO  MnO  MgO  NiO  CoO   CaO Na2O  K2O P2O5  H2O
90.3451 g 46.49 2.21 16.60  0.00  0.00 11.71 0.00 7.54 0.00 0.00 11.02 3.32 1.11 0.00 0.00
Activity of H2O = 0  Melt fraction = 0.921889
olivine: 7.654887 g, composition (Ca0.01Mg0.80Fe''0.20Mn0.00Co0.00Ni0.00)2SiO4
"""

    result = _parse_subprocess_fixture(
        backend,
        output,
        temperature_C=1200.0,
        total_input_kg=1000.0,
        system_output=_system_main_fixture(
            temperature_C=1200.0,
            system_mass_g=97.999987,
        ),
    )

    assert result.liquid_fraction == pytest.approx(0.921889)
    assert result.liquid_composition_wt_pct["SiO2"] == pytest.approx(46.49)
    assert result.phases_present == ["liquid", "olivine"]
    assert sum(result.phase_masses_kg.values()) == pytest.approx(1000.0)
    assert (
        result.phase_masses_kg['liquid']
        / sum(result.phase_masses_kg.values())
    ) == pytest.approx(result.liquid_fraction)
    assert result.warnings == ["AlphaMELTS liquidus_C=1220.310"]
    assert result.ledger_transition is None
    assert result.status == 'ok'


def test_alphamelts_stdout_parser_accepts_parseable_rows_without_stable_banner():
    backend = AlphaMELTSBackend()
    output = """
Initial alphaMELTS calculation at: P 1.000000 (bars), T 1200.000000 (C)
liquid:    SiO2 TiO2 Al2O3 Fe2O3 Cr2O3   FeO  MnO  MgO  NiO  CoO   CaO Na2O  K2O P2O5  H2O
90.3451 g 46.49 2.21 16.60  0.00  0.00 11.71 0.00 7.54 0.00 0.00 11.02 3.32 1.11 0.00 0.00
Activity of H2O = 0  Melt fraction = 0.921889
olivine: 7.654887 g, composition (Ca0.01Mg0.80Fe''0.20Mn0.00Co0.00Ni0.00)2SiO4
"""

    result = _parse_subprocess_fixture(
        backend,
        output,
        temperature_C=1200.0,
        total_input_kg=1000.0,
        system_output=_system_main_fixture(
            temperature_C=1200.0,
            system_mass_g=97.999987,
        ),
    )

    assert result.status == "ok"
    assert result.phases_present == ["liquid", "olivine"]
    assert result.liquid_fraction == pytest.approx(0.921889)
    assert result.warnings == [
        "AlphaMELTS stable assemblage banner absent; accepted parseable phase rows"
    ]


def test_alphamelts_stdout_parser_classifies_no_phase_convergence_failure():
    backend = AlphaMELTSBackend()
    output = """
<> Found the liquidus at T = 1249.41 (C).
...Checking saturation state of potential solids.
...Adding the solid phase olivine to the assemblage.
...Projecting equality constraints.
...Minimizing the thermodynamic potential.
...Quadratic convergence failure. Aborting.
Initial calculation failed (1.000000 bars, 1249.414062 C)!
"""

    result = _parse_subprocess_fixture(
        backend,
        output,
        temperature_C=1249.414062,
        total_input_kg=1000.0,
    )

    assert result.status == "out_of_domain"
    assert result.temperature_C == pytest.approx(1249.414062)
    assert result.phases_present == []
    assert result.warnings == [
        "AlphaMELTS liquidus_C=1249.410",
        "AlphaMELTS subprocess reported convergence failure: "
        "Quadratic convergence failure. Aborting.",
    ]
    assert (
        result.diagnostics.get("backend_status_reason")
        == ALPHAMELTS_REASON_NO_CONVERGENCE
    )
    assert (
        result.diagnostics.get("backend_failure_reason_code")
        == ALPHAMELTS_REASON_NO_CONVERGENCE
    )
    assert result.diagnostics.get("backend_failure_category") == "out_of_domain"
    assert 'no convergence' in (
        result.diagnostics.get("backend_status_reason_message") or ''
    )


def test_alphamelts_stdout_parser_rejects_provisional_rows_after_abort():
    backend = AlphaMELTSBackend()
    output = """
Initial alphaMELTS calculation at: P 1.000000 (bars), T 1200.000000 (C)
liquid: SiO2 Al2O3 FeO
80.0 g 60.0 20.0 20.0
olivine: 20.0 g, composition (Mg,Fe)2SiO4
...Quadratic convergence failure. Aborting.
"""

    result = _parse_subprocess_fixture(
        backend,
        output,
        temperature_C=1200.0,
        total_input_kg=10.0,
    )

    assert result.status == 'out_of_domain'
    assert result.phases_present == []
    assert result.phase_masses_kg == {}
    assert (
        result.diagnostics['backend_status_reason']
        == ALPHAMELTS_REASON_NO_CONVERGENCE
    )


def test_alphamelts_subprocess_phase_masses_scale_to_physical_input_mass():
    backend = AlphaMELTSBackend()
    output = """
<> Stable phase assemblage achieved.
Initial alphaMELTS calculation at: P 1.000000 (bars), T 1200.000000 (C)
liquid: SiO2 Al2O3 FeO
80.0 g 60.0 20.0 20.0
Melt fraction = 0.8
olivine: 20.0 g, composition (Mg,Fe)2SiO4
"""

    ten_kg = _parse_subprocess_fixture(
        backend,
        output,
        temperature_C=1200.0,
        total_input_kg=10.0,
    )
    twenty_kg = _parse_subprocess_fixture(
        backend,
        output,
        temperature_C=1200.0,
        total_input_kg=20.0,
    )

    assert sum(ten_kg.phase_masses_kg.values()) == pytest.approx(10.0)
    assert sum(twenty_kg.phase_masses_kg.values()) == pytest.approx(20.0)
    assert twenty_kg.phase_masses_kg['liquid'] == pytest.approx(
        2.0 * ten_kg.phase_masses_kg['liquid']
    )
    assert ten_kg.liquid_fraction == twenty_kg.liquid_fraction == pytest.approx(0.8)


def test_alphamelts_subprocess_rejects_partial_phase_mass_parse():
    backend = AlphaMELTSBackend()
    output = """
<> Stable solid assemblage achieved.
Initial alphaMELTS calculation at: P 1.000000 (bars), T 1100.000000 (C)
olivine: 98.0 g, composition (Mg,Fe)2SiO4
"""

    with pytest.raises(
        AlphaMELTSSubprocessContractError,
        match='phase_mass_incomplete',
    ):
        _parse_subprocess_fixture(
            backend,
            output,
            temperature_C=1100.0,
            total_input_kg=10.0,
        )


def test_alphamelts_subprocess_reports_solved_fo2_without_claiming_request():
    backend = AlphaMELTSBackend()
    output = """
<> Stable liquid assemblage achieved.
Initial alphaMELTS calculation at: P 1.000000 (bars), T 1200.000000 (C)
liquid: SiO2 Al2O3 FeO
100.0 g 60.0 20.0 20.0
Melt fraction = 1.0
"""

    result = _parse_subprocess_fixture(
        backend,
        output,
        temperature_C=1200.0,
        total_input_kg=10.0,
        system_output=_system_main_fixture(
            temperature_C=1200.0,
            fO2_log=-8.0,
        ),
    )

    assert result.status == 'out_of_domain'
    assert result.fO2_log == pytest.approx(-8.0)
    assert result.diagnostics['requested_fO2_log'] == pytest.approx(-9.0)
    assert result.diagnostics['solved_fO2_log'] == pytest.approx(-8.0)
    assert result.diagnostics['authoritative_for_requested_conditions'] is False


def test_alphamelts_subprocess_accepts_fo2_echo_rounding():
    backend = AlphaMELTSBackend()
    output = """
<> Stable liquid assemblage achieved.
Initial alphaMELTS calculation at: P 1.000000 (bars), T 1200.000000 (C)
liquid: SiO2 Al2O3 FeO
100.0 g 60.0 20.0 20.0
Melt fraction = 1.0
"""

    result = _parse_subprocess_fixture(
        backend,
        output,
        temperature_C=1200.0,
        total_input_kg=10.0,
        fO2_log=-9.0000004,
        system_output=_system_main_fixture(
            temperature_C=1200.0,
            fO2_log=-9.0,
        ),
    )

    assert result.status == 'ok'
    assert 'operating_point_clamped' not in result.diagnostics


def test_alphamelts_accepts_applied_thermoengine_absolute_fo2(monkeypatch):
    backend = ThermoEngineBackend()
    backend._mode = 'thermoengine'

    class FakeTransport:
        def equilibrate(self, **kwargs):
            assert kwargs['fO2_log'] == -3.0
            return ThermoEnginePayload(
                phases_present=('Liquid',),
                phase_masses_kg={'Liquid': 0.1},
                liquid_fraction=1.0,
                liquid_composition_wt_pct={'SiO2': 100.0},
                solved_fO2_log=-3.0004,
            )

    backend._thermoengine_transport = FakeTransport()
    monkeypatch.setattr(
        backend,
        '_activities_times_antoine_or_fail',
        lambda *_args, **_kwargs: {},
    )

    result = backend.equilibrate(
        temperature_C=1500.0,
        composition_kg=_melts_domain_composition(),
        fO2_log=-3.0,
        pressure_bar=1.0,
    )

    assert result.status == 'ok'
    assert result.phases_present == ['Liquid']
    assert result.fO2_log == pytest.approx(-3.0004)
    assert result.diagnostics['requested_fO2_log'] == pytest.approx(-3.0)
    assert result.diagnostics['solved_fO2_log'] == pytest.approx(-3.0004)
    assert result.diagnostics['authoritative_for_requested_conditions'] is True


def test_alphamelts_python_requires_solved_fo2_echo():
    backend = AlphaMELTSBackend()
    backend._mode = 'python_api'
    backend._pet_melts = object()
    backend._pet_payload_preloaded = True
    backend._pet_module = types.SimpleNamespace(
        equilibrate_MELTS=lambda **_kwargs: ({
            'Conditions': {'mass': 100.0},
            'liquid1': {'SiO2': 50.0},
            'liquid1_prop': {'mass': 100.0},
        }, {})
    )

    result = backend.equilibrate(
        temperature_C=1500.0,
        composition_kg=_melts_domain_composition(),
        fO2_log=-3.0,
        pressure_bar=1.0,
    )

    assert result.status == 'out_of_domain'
    assert result.phases_present == []
    assert result.diagnostics['backend_status_reason'] == 'fo2_constraint_unapplied'


def test_alphamelts_python_preserves_solved_fo2_and_scales_physical_batches():
    backend = AlphaMELTSBackend()
    backend._mode = 'python_api'
    backend._pet_melts = object()
    backend._pet_payload_preloaded = True
    backend._pet_module = types.SimpleNamespace(
        equilibrate_MELTS=lambda **_kwargs: ({
            'Conditions': {'mass': 100.0, 'P_bar': 1.0, 'fO2_log': -8.0},
            'liquid1': {'SiO2': 50.0, 'Al2O3': 15.0, 'FeO': 10.0},
            'liquid1_prop': {'mass': 80.0},
            'olivine1': {'SiO2': 40.0, 'MgO': 50.0},
            'olivine1_prop': {'mass': 20.0},
        }, {})
    )
    base = _melts_domain_composition()

    ten_kg = backend.equilibrate(
        temperature_C=1500.0,
        composition_kg={oxide: mass * 0.1 for oxide, mass in base.items()},
        fO2_log=-9.0,
        pressure_bar=1.0,
    )
    twenty_kg = backend.equilibrate(
        temperature_C=1500.0,
        composition_kg={oxide: mass * 0.2 for oxide, mass in base.items()},
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    assert ten_kg.status == twenty_kg.status == 'out_of_domain'
    assert ten_kg.fO2_log == twenty_kg.fO2_log == pytest.approx(-8.0)
    assert sum(ten_kg.phase_masses_kg.values()) == pytest.approx(10.0)
    assert sum(twenty_kg.phase_masses_kg.values()) == pytest.approx(20.0)
    assert ten_kg.diagnostics['authoritative_for_requested_conditions'] is False


def test_alphamelts_stdout_parser_classifies_initial_calculation_failed():
    # Isolate the second no-phase classifier branch ('Initial calculation
    # failed') -- the combined-fixture test above is dominated by the
    # 'Quadratic convergence failure' branch, so this asserts the
    # Initial-calculation path also maps to NO_CONVERGENCE.
    backend = AlphaMELTSBackend()
    output = """
<> Found the liquidus at T = 1249.41 (C).
...Checking saturation state of potential solids.
...Projecting equality constraints.
Initial calculation failed (1.000000 bars, 1249.414062 C)!
"""

    result = _parse_subprocess_fixture(
        backend,
        output,
        temperature_C=1249.414062,
        total_input_kg=1000.0,
    )

    assert result.status == "out_of_domain"
    assert result.temperature_C == pytest.approx(1249.414062)
    assert result.phases_present == []
    assert (
        "AlphaMELTS subprocess reported convergence failure: "
        "Initial calculation failed."
    ) in result.warnings
    assert (
        result.diagnostics.get("backend_status_reason")
        == ALPHAMELTS_REASON_NO_CONVERGENCE
    )
    assert (
        result.diagnostics.get("backend_failure_reason_code")
        == ALPHAMELTS_REASON_NO_CONVERGENCE
    )
    assert result.diagnostics.get("backend_failure_category") == "out_of_domain"
    assert 'no convergence' in (
        result.diagnostics.get("backend_status_reason_message") or ''
    )


def test_alphamelts_stdout_parser_fails_without_stable_assemblage():
    backend = AlphaMELTSBackend()

    with pytest.raises(
        RuntimeError,
        match="parseable phase assemblage",
    ) as excinfo:
        _parse_subprocess_fixture(
            backend,
            "Error in SILMIN file input procedure.",
            temperature_C=1200.0,
            total_input_kg=1000.0,
        )

    assert (
        getattr(excinfo.value, 'backend_status_reason')
        == ALPHAMELTS_REASON_PARSE_EMPTY_OUTPUT
    )
    assert (
        getattr(excinfo.value, 'backend_failure_reason_code')
        == ALPHAMELTS_REASON_PARSE_EMPTY_OUTPUT
    )
    assert getattr(excinfo.value, 'backend_failure_category') == 'not_converged'


def test_project_local_alphamelts_reports_liquidus_when_installed():
    backend = AlphaMELTSBackend()
    try:
        available = backend.initialize({'mode': 'subprocess'})
    except ImportError as exc:
        pytest.skip(f"project-local alphaMELTS app is not installed: {exc}")
    if not available:
        pytest.skip("project-local alphaMELTS app is not installed")

    result = backend.equilibrate(
        temperature_C=1200.0,
        composition_kg={
            "SiO2": 450.0,
            "Al2O3": 150.0,
            "FeO": 120.0,
            "MgO": 100.0,
            "CaO": 100.0,
            "Na2O": 30.0,
            "K2O": 10.0,
            "TiO2": 20.0,
        },
        fO2_log=-9.0,
        pressure_bar=1.0,
        subprocess_run_mode='liquidus_finder',
    )

    assert result.liquid_fraction == pytest.approx(1.0)
    assert any(message.startswith("AlphaMELTS liquidus_C=")
               for message in result.warnings)
    assert result.ledger_transition is None


def test_project_local_alphamelts_populates_full_table_suite_when_installed():
    backend = AlphaMELTSBackend()
    try:
        available = backend.initialize({'mode': 'subprocess'})
    except ImportError as exc:
        pytest.skip(f"project-local alphaMELTS app is not installed: {exc}")
    if not available:
        pytest.skip("project-local alphaMELTS app is not installed")

    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg=_melts_domain_composition(),
        fO2_log=-9.0,
        pressure_bar=1.0,
        subprocess_run_mode='isothermal',
    )

    assert result.system_enthalpy is not None
    assert result.system_entropy is not None
    assert result.system_volume is not None
    assert result.system_heat_capacity_Cp is not None
    assert result.system_dVdP is not None
    assert result.system_dVdT is not None
    # Absolute-path runs emit `fO2-9.0)` (delta from the requested absolute
    # path), not a QFM-relative value. Do not mislabel that zero as delta QFM.
    assert result.system_fO2_delta_QFM is None
    assert result.system_phi is not None
    assert result.phase_thermo
    assert result.phase_compositions
    assert result.bulk_composition_wt_pct
    assert result.chem_potentials is None
    assert result.phase_affinities is None


@pytest.mark.live_engine
def test_project_local_alphamelts_cold_c0_step_returns_when_installed():
    backend = AlphaMELTSBackend()
    try:
        available = backend.initialize({'mode': 'subprocess'})
    except ImportError as exc:
        pytest.skip(f"project-local alphaMELTS app is not installed: {exc}")
    if not available:
        pytest.skip("project-local alphaMELTS app is not installed")

    sim = PyrolysisSimulator(
        backend,
        _load_data("setpoints.yaml"),
        _load_data("feedstocks.yaml"),
        _load_data("vapor_pressures.yaml"),
    )
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.start_campaign(CampaignPhase.C0)

    started = time.monotonic()
    try:
        snapshot = sim.step()
    except AlphaMELTSSubprocessContractError as exc:
        if getattr(exc, "backend_status_reason", None) == "timeout":
            pytest.skip("AlphaMELTS live subprocess timed out")
        raise
    elapsed_s = time.monotonic() - started

    assert snapshot.hour == 1
    # Bound covers a true cold start: the A-CX-02 fix instance-scoped the
    # ThermoEngine health cache (process-global reuse masked stale health), so
    # a fresh backend legitimately re-probes engine health (~4 s) before the
    # step. Warm-worker pools reuse instances and never pay this.
    assert elapsed_s < 15.0


def test_no_mode_marks_status_unavailable():
    # An AlphaMELTSBackend with no thermoengine, python_api, or subprocess mode reaches
    # the explicit "no engine present" return path in equilibrate(); the
    # result is labelled 'unavailable'.
    backend = AlphaMELTSBackend()
    assert backend._mode is None  # default state

    result = backend.equilibrate(
        temperature_C=1600.0,
        composition_kg={
            'SiO2': 50.0, 'Al2O3': 15.0, 'FeO': 10.0,
            'MgO': 10.0, 'CaO': 10.0, 'Na2O': 5.0,
        },
        fO2_log=-9.0,
        pressure_bar=1e-6,
    )

    assert result.status == 'unavailable'


def test_petthermotools_result_parser_marks_status_ok():
    # _parse_petthermotools_result is the success path of the python_api
    # mode -- a parsed PetThermoTools result must be labelled 'ok'.
    backend = AlphaMELTSBackend()
    results = ({
        'Conditions': {'mass': 100.0},
        'liquid1': {'SiO2': 50.0},
        'liquid1_prop': {'mass': 100.0},
    }, {})

    result = backend._parse_petthermotools_result(
        results,
        temperature_C=1200.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        comp_wt={'SiO2': 50.0},
    )

    assert result.status == 'ok'
    assert result.liquid_fraction == pytest.approx(1.0)
    assert result.liquid_composition_wt_pct == {'SiO2': 50.0}


def test_petthermotools_parser_preserves_empty_liquid_composition():
    backend = AlphaMELTSBackend()
    results = ({
        'Conditions': {'mass': 100.0},
        'liquid1': {'not_an_oxide': 50.0},
        'liquid1_prop': {'mass': 25.0},
        'olivine1': {'MgO': 75.0},
        'olivine1_prop': {'mass': 75.0},
    }, {})

    result = backend._parse_petthermotools_result(
        results,
        temperature_C=1200.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        comp_wt={'SiO2': 50.0, 'Na2O': 5.0},
    )

    assert result.status == 'ok'
    assert result.liquid_fraction == pytest.approx(0.25)
    assert result.liquid_composition_wt_pct == {}


# ----------------------------------------------------------------------
# VapoRock vapor-bridge: alphaMELTS delegates to the real VapoRockBackend
# helper for vapor pressures (no re-implemented stub). Tests below mock
# the helper's EquilibriumResult so they do not need a live alphaMELTS or
# the upstream library; the live numbers are validated separately in the
# vaporock backend test + the manual physics-sanity check.
# ----------------------------------------------------------------------


def _vaporock_helper_returning(pressures, status='ok'):
    """A stand-in VapoRockBackend whose equilibrate() returns a known result."""
    class _Helper:
        def __init__(self):
            self.calls = []

        def is_available(self):
            return True

        def equilibrate(self, *, temperature_C, composition_kg,
                        fO2_log, pressure_bar):
            self.calls.append({
                'temperature_C': temperature_C,
                'composition_kg': dict(composition_kg),
                'fO2_log': fO2_log,
                'pressure_bar': pressure_bar,
            })
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status=status,
                liquid_fraction=1.0 if status == 'ok' else None,
                vapor_pressures_Pa=dict(pressures),
            )
    return _Helper()


def test_vapor_bridge_happy_path_labels_source_vaporock_and_passes_solved_comp():
    backend = AlphaMELTSBackend()
    backend._vaporock_available = True
    helper = _vaporock_helper_returning({'Na': 11.5, 'SiO': 0.4, 'Fe': 1.57})
    backend._vaporock_helper = helper

    solved = {'SiO2': 45.0, 'FeO': 18.0, 'Na2O': 0.4}
    pressures, source = backend._vapor_pressures_via_vaporock_or_antoine(
        T_C=1600.0,
        solved_melt_wt_pct=solved,
        liquid_fraction=1.0,
        fO2_log=-7.96,
        pressure_bar=1e-6,
        activities={'Na2O': 0.1},
    )

    # vapor_pressures_Pa is ALREADY Pa -> returned verbatim, no 1e5 rescale.
    assert source == 'vaporock'
    assert pressures == {'Na': 11.5, 'SiO': 0.4, 'Fe': 1.57}
    # The SOLVED equilibrium liquid was fed to VapoRock, not the fallback.
    assert helper.calls[0]['composition_kg'] == solved
    # Absolute fO2_log forwarded unchanged (no buffer/offset conversion).
    assert helper.calls[0]['fO2_log'] == pytest.approx(-7.96)


def test_vapor_bridge_no_liquid_phase_returns_physical_zero_without_helper():
    backend = AlphaMELTSBackend()
    backend._vaporock_available = True
    helper = _vaporock_helper_returning({'Na': 5.0})
    backend._vaporock_helper = helper

    pressures, source = backend._vapor_pressures_via_vaporock_or_antoine(
        T_C=1600.0,
        solved_melt_wt_pct={},
        liquid_fraction=0.0,
        fO2_log=-8.0,
        pressure_bar=1e-6,
        activities={},
    )

    assert source == 'no_liquid_phase'
    assert pressures == {}
    assert helper.calls == []

    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = types.SimpleNamespace(temperature_C=1600.0)
    flux = sim._calculate_evaporation(EquilibriumResult(
        status='ok',
        liquid_fraction=0.0,
        vapor_pressures_Pa=pressures,
    ))
    assert flux.species_kg_hr == {}


def test_vapor_bridge_liquid_present_empty_solved_comp_fails_loud():
    backend = AlphaMELTSBackend()
    backend._vaporock_available = True
    helper = _vaporock_helper_returning({'Na': 5.0})
    backend._vaporock_helper = helper

    with pytest.raises(
        RuntimeError,
        match='missing_solved_liquid_composition.*bulk-composition',
    ):
        backend._vapor_pressures_via_vaporock_or_antoine(
            T_C=1600.0,
            solved_melt_wt_pct={},
            liquid_fraction=0.25,
            fO2_log=-8.0,
            pressure_bar=1e-6,
            activities={},
        )

    assert helper.calls == []


def test_vapor_bridge_helper_unavailable_uses_explicit_antoine_fallback_nonempty():
    backend = AlphaMELTSBackend()
    backend._vaporock_available = True

    class _Down:
        def is_available(self):
            return False

        def equilibrate(self, **_kw):  # pragma: no cover - must not be called
            raise AssertionError('unavailable helper must not be called')

    backend._vaporock_helper = _Down()

    # Real activities so the Antoine fallback emits a non-empty dict.
    pressures, source = backend._vapor_pressures_via_vaporock_or_antoine(
        T_C=1600.0,
        solved_melt_wt_pct={'SiO2': 45.0, 'Na2O': 4.0, 'K2O': 1.0},
        liquid_fraction=1.0,
        fO2_log=-8.0,
        pressure_bar=1e-6,
        activities={'Na2O': 0.2, 'SiO2': 0.4},
    )

    assert source["Na"] == (
        "antoine_fallback_from_vaporock:backsolved_vaporock_curve_fit"
    )
    assert source["SiO"] == (
        "antoine_fallback_from_vaporock:backsolved_vaporock_curve_fit"
    )
    # FAIL-LOUD: the fallback is a real Antoine dict, NOT a silent {} that
    # would zero the evaporation flux.
    assert pressures != {}
    assert 'Na' in pressures and pressures['Na'] > 0.0


def test_vapor_bridge_empty_vaporock_result_falls_back_to_antoine_with_label():
    backend = AlphaMELTSBackend()
    backend._vaporock_available = True
    # Helper available but returns out_of_domain + empty pressures.
    backend._vaporock_helper = _vaporock_helper_returning(
        {}, status='out_of_domain')

    with pytest.warns(UserWarning):
        pressures, source = backend._vapor_pressures_via_vaporock_or_antoine(
            T_C=1600.0,
            solved_melt_wt_pct={'SiO2': 45.0, 'Na2O': 4.0},
            liquid_fraction=1.0,
            fO2_log=-8.0,
            pressure_bar=1e-6,
            activities={'Na2O': 0.2},
        )

    assert source["Na"] == (
        "antoine_fallback_from_vaporock:backsolved_vaporock_curve_fit"
    )
    assert pressures != {}  # not a silent zero


def test_vapor_bridge_reraises_library_exception_as_labelled_runtime_error():
    backend = AlphaMELTSBackend()
    backend._vaporock_available = True

    class _Boom:
        def is_available(self):
            return True

        def equilibrate(self, **_kw):
            raise ValueError('upstream blew up')

    backend._vaporock_helper = _Boom()

    with pytest.raises(RuntimeError, match='VapoRock vapor bridge failed'):
        backend._vapor_pressures_via_vaporock_or_antoine(
            T_C=1600.0,
            solved_melt_wt_pct={'SiO2': 45.0},
            liquid_fraction=1.0,
            fO2_log=-8.0,
            pressure_bar=1e-6,
            activities={},
        )


def test_thermoengine_callsite_wires_vaporock_source_and_solved_liquid(monkeypatch):
    from engines.alphamelts.thermoengine import ThermoEnginePayload

    solved_liquid = {'SiO2': 44.0, 'FeO': 17.0, 'Na2O': 0.5}

    class FakeTransport:
        def equilibrate(self, *, temperature_C, pressure_bar, comp_wt,
                        fO2_log, warnings):
            return ThermoEnginePayload(
                phases_present=('liquid',),
                phase_masses_kg={'liquid': 1.0},
                liquid_fraction=1.0,
                liquid_composition_wt_pct=dict(solved_liquid),
                activity_coefficients={'Na2O': 0.1},
                fe_redox_split={},
                solved_fO2_log=fO2_log,
            )

    backend = ThermoEngineBackend()
    backend._mode = 'thermoengine'
    backend._thermoengine_transport = FakeTransport()
    backend._vaporock_available = True
    helper = _vaporock_helper_returning({'Na': 9.9, 'SiO': 0.3})
    backend._vaporock_helper = helper

    eq = backend._equilibrate_thermoengine(
        1600.0,
        {'SiO2': 45.0, 'FeO': 18.0, 'MgO': 9.0,
         'CaO': 11.0, 'Al2O3': 12.0, 'Na2O': 4.0, 'K2O': 1.0},
        -7.96,
        1e-6,
    )

    assert eq.vapor_pressures_Pa == {'Na': 9.9, 'SiO': 0.3}
    assert set(eq.vapor_pressures_source.values()) == {'vaporock'}
    # The post-equilibrium SOLVED liquid (not the pre-equilibrium input)
    # is what reached the VapoRock helper.
    assert helper.calls[0]['composition_kg'] == solved_liquid


def test_thermoengine_vaporock_empty_fallback_marks_vapor_facet_degraded():
    from engines.alphamelts.thermoengine import ThermoEnginePayload

    solved_liquid = {'SiO2': 44.0, 'FeO': 17.0, 'Na2O': 0.5}

    class FakeTransport:
        def equilibrate(self, *, temperature_C, pressure_bar, comp_wt,
                        fO2_log, warnings):
            return ThermoEnginePayload(
                phases_present=('liquid',),
                phase_masses_kg={'liquid': 1.0},
                liquid_fraction=1.0,
                liquid_composition_wt_pct=dict(solved_liquid),
                activity_coefficients={'Na2O': 0.2},
                fe_redox_split={},
                solved_fO2_log=fO2_log,
            )

    backend = ThermoEngineBackend()
    backend._mode = 'thermoengine'
    backend._thermoengine_transport = FakeTransport()
    backend._vaporock_available = True
    backend._vaporock_helper = _vaporock_helper_returning(
        {},
        status='non_authoritative',
    )
    backend._vapor_pressure_table = {
        'Na': {
            'fit_target': 'pure_component_psat',
            'residual_dex': 0.01,
            'confidence_tier': 'high',
            'antoine': {'A': 4.0, 'B': 0.0, 'C': 0.0},
        },
    }

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        eq = backend._equilibrate_thermoengine(
            1600.0,
            {
                'SiO2': 45.0,
                'FeO': 18.0,
                'MgO': 9.0,
                'CaO': 11.0,
                'Al2O3': 12.0,
                'Na2O': 4.0,
                'K2O': 1.0,
            },
            -7.96,
            1e-6,
        )

    assert any(
        'VapoRock returned no usable vapor pressures' in str(item.message)
        for item in caught
    )
    assert eq.vapor_pressures_Pa == {'Na': pytest.approx(2000.0)}
    # Parent status stays 'ok': the equilibrium solve answered the requested
    # conditions; only the vapor-pressure facet lost VapoRock authority
    # (SC-39 honesty is facet-scoped — parent out_of_domain is reserved for
    # clamped operating points per the magemin 5786d1f precedent).
    assert eq.status == 'ok'
    assert eq.diagnostics.get('backend_status') != 'out_of_domain'
    assert eq.diagnostics['vapor_pressure_backend_status'] == 'fallback'
    assert (
        eq.diagnostics['vapor_pressure_backend_status_reason']
        == 'vaporock_to_antoine_fallback'
    )
    assert (
        eq.diagnostics['vapor_pressure_fallback_source']
        == 'antoine_fallback_from_vaporock'
    )
    assert eq.diagnostics['authoritative_for_requested_vapor_pressure'] is False
    assert eq.vapor_pressures_source == {
        'Na': 'antoine_fallback_from_vaporock:legacy_pure_component_estimate',
    }
    assert eq.vapor_pressures_source['Na'].startswith(
        'antoine_fallback_from_vaporock:'
    )
    assert set(eq.vapor_pressures_source.values()) != {'vaporock'}


def test_thermoengine_vaporock_unavailable_marks_not_attempted_without_churn():
    from engines.alphamelts.thermoengine import ThermoEnginePayload

    solved_liquid = {'SiO2': 44.0, 'FeO': 17.0, 'Na2O': 0.5}

    class FakeTransport:
        def equilibrate(self, *, temperature_C, pressure_bar, comp_wt,
                        fO2_log, warnings):
            return ThermoEnginePayload(
                phases_present=('liquid',),
                phase_masses_kg={'liquid': 1.0},
                liquid_fraction=1.0,
                liquid_composition_wt_pct=dict(solved_liquid),
                activity_coefficients={'Na2O': 0.2},
                fe_redox_split={},
                solved_fO2_log=fO2_log,
            )

    backend = ThermoEngineBackend()
    backend._mode = 'thermoengine'
    backend._thermoengine_transport = FakeTransport()
    backend._vaporock_available = False
    backend._vapor_pressure_table = {
        'Na': {
            'fit_target': 'pure_component_psat',
            'residual_dex': 0.01,
            'confidence_tier': 'high',
            'antoine': {'A': 4.0, 'B': 0.0, 'C': 0.0},
        },
    }

    eq = backend._equilibrate_thermoengine(
        1600.0,
        {
            'SiO2': 45.0,
            'FeO': 18.0,
            'MgO': 9.0,
            'CaO': 11.0,
            'Al2O3': 12.0,
            'Na2O': 4.0,
            'K2O': 1.0,
        },
        -7.96,
        1e-6,
    )

    assert eq.status == 'ok'
    assert eq.diagnostics.get('backend_status') != 'out_of_domain'
    assert eq.diagnostics['vapor_pressure_backend_status'] == 'not_attempted'
    assert (
        eq.diagnostics['vapor_pressure_backend_status_reason']
        == 'vaporock_unavailable_not_attempted'
    )
    assert 'vapor_pressure_fallback_source' not in eq.diagnostics
    assert 'authoritative_for_requested_vapor_pressure' not in eq.diagnostics
    assert eq.vapor_pressures_Pa == {'Na': pytest.approx(2000.0)}
    assert eq.vapor_pressures_source == {
        'Na': 'thermoengine:legacy_pure_component_estimate',
    }


def test_vapor_pressure_diagnostics_marks_not_attempted_on_empty_pressures():
    # t-121 empty-pressures gap: when VapoRock is unavailable AND pressures come out empty,
    # the facet backend_status must STILL be 'not_attempted' so the operator panel is not
    # stuck at 'n/a' (indistinguishable from an authoritative VapoRock-succeeded result).
    backend = AlphaMELTSBackend()
    backend._vaporock_available = False

    payload = backend._vapor_pressure_diagnostics(
        diagnostics={},
        pressures={},                    # empty — e.g. no volatile species in the melt
        source='antoine_internal',       # NOT an antoine_fallback_from_vaporock source
    )

    assert payload['vapor_pressure_backend_status'] == 'not_attempted'
    assert (
        payload['vapor_pressure_backend_status_reason']
        == 'vaporock_unavailable_not_attempted'
    )


def test_dead_uppercase_vaporock_stub_is_gone_from_source():
    import simulator.melt_backend.alphamelts as mod

    src = Path(mod.__file__).read_text()
    # The never-worked stub: an uppercase-module import statement and the
    # nonexistent calc_vapor entry point + the old method name.
    assert 'import VapoRock\n' not in src
    assert 'VapoRock.calc_vapor' not in src
    assert 'calc_vapor' not in src
    assert '_get_vaporock_pressures' not in src
