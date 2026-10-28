"""Pin Fe-bearing imposed-fO2 behaviour before the Fe-free gas-only path.

The numbers below are the current adapter output for one Fe-bearing melt
at a printed absolute fO2. A later Fe-free branch must leave this path
byte-identical: the solver still receives the requested fO2, and the
activities and the AlphaMELTS input file stay these values.
"""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from engines.alphamelts.thermoengine import ThermoEnginePayload
from simulator.config import resolve_alphamelts_python_api_model
from simulator.melt_backend.alphamelts import (
    ALPHAMELTS_REASON_SUBPROCESS_DIED,
    AlphaMELTSBackend,
    AlphaMELTSSubprocessRunMode,
)
from simulator.melt_backend.thermoengine import ThermoEngineBackend


# Binary-exact wt% after normalisation (each mass is a dyadic rational).
_FE_BEARING_KG = {
    'SiO2': 0.5,
    'Al2O3': 0.125,
    'FeO': 0.125,
    'MgO': 0.125,
    'CaO': 0.0625,
    'Na2O': 0.0625,
}
_FE_BEARING_ACTIVITIES = {'SiO2': 0.42, 'FeO': 0.17, 'Na2O': 0.08}
_FE_BEARING_LIQUID_WT = {
    'SiO2': 50.0,
    'Al2O3': 12.5,
    'FeO': 12.5,
    'MgO': 12.5,
    'CaO': 6.25,
    'Na2O': 6.25,
}
_FE_BEARING_NA_PA = 1.25e-3
_REQUESTED_FO2_LOG = -9.0

# Captured from AlphaMELTSBackend._write_melts_file on this composition
# at 1400 C, 1 bar, absolute fO2 -9, before the Fe-free gas-only change.
_FE_BEARING_MELTS_FILE = """\
Title: regolith_pyrolysis_simulator
Initial Composition: Al2O3 12.5000
Initial Composition: CaO 6.2500
Initial Composition: FeO 12.5000
Initial Composition: MgO 12.5000
Initial Composition: Na2O 6.2500
Initial Composition: SiO2 50.0000
Initial Temperature: 1400.000000
Initial Pressure: 1
Log fO2 Path: Absolute
Log fO2 Offset: -9
"""


def _thermoengine_backend(transport) -> ThermoEngineBackend:
    backend = ThermoEngineBackend()
    backend._mode = 'thermoengine'
    backend._thermoengine_transport = transport
    backend._vaporock_available = True
    return backend


def test_fe_bearing_thermoengine_imposed_fo2_keeps_solver_request_and_activities():
    seen: dict[str, object] = {}

    class FakeTransport:
        def equilibrate(self, **kwargs):
            seen['fO2_log'] = kwargs['fO2_log']
            seen['feo'] = kwargs['comp_wt']['FeO']
            seen['fe2o3'] = kwargs['comp_wt']['Fe2O3']
            return ThermoEnginePayload(
                phases_present=('Liquid',),
                phase_masses_kg={'Liquid': 1.0},
                liquid_fraction=1.0,
                liquid_composition_wt_pct=dict(_FE_BEARING_LIQUID_WT),
                activity_coefficients=dict(_FE_BEARING_ACTIVITIES),
                fe_redox_split={'FeO': 12.5, 'Fe2O3': 0.0},
                solved_fO2_log=_REQUESTED_FO2_LOG,
                solved_fO2_reason=None,
                solver_converged=True,
            )

        def close(self):
            return None

    backend = _thermoengine_backend(FakeTransport())

    def vapor(**kwargs):
        seen['vapor_fO2_log'] = kwargs['fO2_log']
        return {'Na': _FE_BEARING_NA_PA}, 'pinned_vapor_source'

    backend._vapor_pressures_via_vaporock_or_antoine = vapor

    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg=dict(_FE_BEARING_KG),
        fO2_log=_REQUESTED_FO2_LOG,
        pressure_bar=1.0,
    )

    assert seen['fO2_log'] == _REQUESTED_FO2_LOG
    assert seen['vapor_fO2_log'] == _REQUESTED_FO2_LOG
    assert seen['feo'] == 12.5
    assert seen['fe2o3'] == 0.0
    assert result.status == 'ok'
    assert result.fO2_log == _REQUESTED_FO2_LOG
    assert result.activity_coefficients == _FE_BEARING_ACTIVITIES
    assert result.liquid_composition_wt_pct == _FE_BEARING_LIQUID_WT
    assert result.vapor_pressures_Pa['Na'] == _FE_BEARING_NA_PA
    assert result.fe_redox_split == {'FeO': 12.5, 'Fe2O3': 0.0}
    assert result.diagnostics['fO2_transport'] == 'thermoengine_oxygen_root'
    assert 'fo2_gas_only' not in result.diagnostics


def test_fe_bearing_alphamelts_python_imposed_fo2_keeps_echo_and_activities():
    backend = AlphaMELTSBackend()
    backend._mode = 'python_api'
    backend._python_api_model = resolve_alphamelts_python_api_model(
        backend._raw_model_name
    )
    backend._pet_melts = object()
    backend._pet_payload_preloaded = True
    backend._pet_module = object()
    backend._vaporock_available = True
    seen: dict[str, object] = {}

    def fake_petthermo(operation, *, args=(), kwargs=None):
        del args
        call = dict(kwargs or {})
        seen['operation'] = operation
        seen['fO2_buffer'] = call.get('fO2_buffer')
        seen['fO2_offset'] = call.get('fO2_offset')
        assert 'fO2_log' not in call
        return {
            'Conditions': {
                'mass': 100.0,
                'P_bar': 1.0,
                'fO2_log': _REQUESTED_FO2_LOG,
            },
            'liquid1': dict(_FE_BEARING_LIQUID_WT),
            'liquid1_prop': {'mass': 100.0},
            'activity_coefficients': dict(_FE_BEARING_ACTIVITIES),
        }

    def vapor(**kwargs):
        seen['vapor_fO2_log'] = kwargs['fO2_log']
        return {'Na': _FE_BEARING_NA_PA}, 'pinned_vapor_source'

    backend._run_petthermotools_isolated = fake_petthermo
    backend._vapor_pressures_via_vaporock_or_antoine = vapor

    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg=dict(_FE_BEARING_KG),
        fO2_log=_REQUESTED_FO2_LOG,
        pressure_bar=1.0,
    )

    assert seen['operation'] == 'equilibrate_MELTS'
    assert seen['fO2_buffer'] is None
    assert seen['fO2_offset'] is None
    assert seen['vapor_fO2_log'] == _REQUESTED_FO2_LOG
    assert result.status == 'ok'
    assert result.fO2_log == _REQUESTED_FO2_LOG
    assert result.liquid_composition_wt_pct == _FE_BEARING_LIQUID_WT
    assert result.vapor_pressures_Pa['Na'] == _FE_BEARING_NA_PA
    assert 'fo2_gas_only' not in result.diagnostics


@pytest.mark.skipif(__import__('os').name != 'posix', reason='signal contract is POSIX')
def test_fe_bearing_alphamelts_subprocess_imposed_fo2_melts_file_is_unchanged(
    tmp_path: Path,
):
    copy_path = tmp_path / 'input.melts'
    marker = tmp_path / 'invoked'
    stub = tmp_path / 'alphamelts'
    stub.write_text(
        '#!/bin/sh\n'
        f'cp input.melts "{copy_path}"\n'
        f'printf invoked > "{marker}"\n'
        'kill -SEGV $$\n'
    )
    stub.chmod(0o755)
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = stub
    backend._timeout_s = 1.0
    backend._engine_version = 'alphamelts-test-stub'

    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg=dict(_FE_BEARING_KG),
        fO2_log=_REQUESTED_FO2_LOG,
        pressure_bar=1.0,
        subprocess_run_mode=AlphaMELTSSubprocessRunMode.ISOTHERMAL,
    )

    assert marker.is_file()
    assert result.diagnostics['backend_status_reason'] == (
        ALPHAMELTS_REASON_SUBPROCESS_DIED
    )
    assert copy_path.read_text(encoding='utf-8') == _FE_BEARING_MELTS_FILE


_FE_FREE_KG = {
    'SiO2': 0.5,
    'Al2O3': 0.0625,
    'CaO': 0.25,
    'MgO': 0.125,
    'Na2O': 0.0625,
}
_FE_FREE_LIQUID_WT = {
    'SiO2': 50.0,
    'Al2O3': 6.25,
    'CaO': 25.0,
    'MgO': 12.5,
    'Na2O': 6.25,
}
_FE_FREE_ACTIVITIES = {
    'SiO2': 0.5,
    'Al2O3': 0.1,
    'CaO': 0.2,
    'MgO': 0.15,
    'Na2O': 0.2,
}
_GAS_ONLY_NOTICE = (
    'the engine did not resolve intrinsic fO2; '
    'the external fO2 was imposed on the gas only.'
)


def _na_catalog_po2_exponent() -> float:
    """NaO0.5(melt) = Na(g) + 1/4 O2, read from the vapor catalog."""
    import yaml

    payload = yaml.safe_load(
        Path('data/vapor_pressures.yaml').read_text(encoding='utf-8')
    )
    species = payload['families']['metals_na_family']['physical_properties'][
        'species'
    ]['Na']
    return float(species['pO2_exponent'])


def _na_slope(low_fo2: float, low_pa: float, high_fo2: float, high_pa: float) -> float:
    return (math.log10(high_pa) - math.log10(low_pa)) / (high_fo2 - low_fo2)


def test_fe_free_threshold_matches_the_emission_floor():
    from simulator.melt_backend.alphamelts import MELTS_OXIDE_ALIASES
    from simulator.melt_backend.base import (
        MELTS_CARRIED_COMPONENT_MIN_WT_PCT,
        imposed_absolute_fo2_without_iron_couple,
    )

    def decision(comp, **kwargs):
        return imposed_absolute_fo2_without_iron_couple(
            comp,
            oxide_aliases=MELTS_OXIDE_ALIASES,
            fO2_log=kwargs.get('fO2_log', -9.0),
            redox_mode=kwargs.get('redox_mode'),
            redox_buffer=kwargs.get('redox_buffer'),
            fo2_offset=kwargs.get('fo2_offset'),
        )

    assert decision({'SiO2': 50.0, 'Al2O3': 15.0, 'CaO': 35.0}) == ('gas_only', ())
    assert decision({'SiO2': 50.0, 'FeO': 18.0, 'MgO': 32.0}) is None
    assert decision({'SiO2': 70.0, 'FeO': MELTS_CARRIED_COMPONENT_MIN_WT_PCT, 'MgO': 29.999}) == (
        'gas_only',
        (),
    )
    assert decision({
        'SiO2': 70.0,
        'FeO': MELTS_CARRIED_COMPONENT_MIN_WT_PCT + 1.0e-7,
        'MgO': 29.0,
    }) is None
    other = decision({'SiO2': 70.0, 'Cr2O3': 0.0005, 'MgO': 29.9995})
    assert other is not None and other[0] == 'other_redox'
    assert 'Cr2O3' in other[1]
    assert decision(
        {'SiO2': 50.0, 'Al2O3': 50.0},
        redox_buffer='QFM',
    ) is None
    assert decision({'SiO2': 50.0, 'Al2O3': 50.0}, fO2_log=float('nan')) is None


def _assert_gas_only_notice(result) -> None:
    from simulator.battery.enums import Engine, NoticeKind, Quantity
    from simulator.battery.score import cell_notices
    from simulator.diagnostic_helpers.binary_pot_battery import (
        engine_flags_from_result,
    )

    assert result.status == 'ok'
    assert result.fO2_log is None
    assert _GAS_ONLY_NOTICE in result.warnings
    assert result.diagnostics['fo2_gas_only']['notice'] == _GAS_ONLY_NOTICE
    assert result.diagnostics['fO2_transport'] == 'external_on_gas_only'
    flags, _authority, _band = engine_flags_from_result(result)
    assert any(
        row.get('kind') == NoticeKind.ENGINE_MODEL_CAVEAT.value
        and row.get('reason') == _GAS_ONLY_NOTICE
        for row in flags
    )
    scored = cell_notices(
        Quantity.P_PARTIAL,
        Engine.THERMOENGINE,
        SimpleNamespace(notices=flags),
    )
    assert any(
        notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
        and notice.reason == _GAS_ONLY_NOTICE
        for notice in scored
    )


def test_thermoengine_fe_free_absolute_fo2_uses_unconstrained_melt_and_printed_gas():
    from simulator.melt_backend.base import REDOX_MODE_NOT_AN_INPUT

    seen: list[object] = []

    class FakeTransport:
        def equilibrate(self, **kwargs):
            seen.append(kwargs['fO2_log'])
            return ThermoEnginePayload(
                phases_present=('Liquid',),
                phase_masses_kg={'Liquid': 1.0},
                liquid_fraction=1.0,
                liquid_composition_wt_pct=dict(_FE_FREE_LIQUID_WT),
                activity_coefficients=dict(_FE_FREE_ACTIVITIES),
                solved_fO2_log=None,
                solved_fO2_reason='undefined_zero_ferric_liquid',
                solver_converged=True,
            )

        def close(self):
            return None

    backend = _thermoengine_backend(FakeTransport())
    backend._vaporock_available = False

    unconstrained = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg=dict(_FE_FREE_KG),
        fO2_log=None,
        pressure_bar=1.0,
        redox_mode=REDOX_MODE_NOT_AN_INPUT,
    )
    low = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg=dict(_FE_FREE_KG),
        fO2_log=-11.0,
        pressure_bar=1.0,
    )
    high = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg=dict(_FE_FREE_KG),
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    assert seen == [None, None, None]
    assert low.activity_coefficients == unconstrained.activity_coefficients
    assert low.liquid_composition_wt_pct == unconstrained.liquid_composition_wt_pct
    assert low.liquid_composition_wt_pct == _FE_FREE_LIQUID_WT
    assert 'fo2_gas_only' not in unconstrained.diagnostics
    _assert_gas_only_notice(low)
    _assert_gas_only_notice(high)
    assert low.requested_fO2_log == -11.0
    assert high.requested_fO2_log == -9.0
    slope = _na_slope(-11.0, low.vapor_pressures_Pa['Na'], -9.0, high.vapor_pressures_Pa['Na'])
    assert slope == pytest.approx(_na_catalog_po2_exponent(), abs=1.0e-9)


def test_alphamelts_python_fe_free_absolute_fo2_matches_unconstrained_melt():
    from simulator.melt_backend.base import REDOX_MODE_NOT_AN_INPUT

    backend = AlphaMELTSBackend()
    backend._mode = 'python_api'
    backend._python_api_model = resolve_alphamelts_python_api_model(
        backend._raw_model_name
    )
    backend._pet_melts = object()
    backend._pet_payload_preloaded = True
    backend._pet_module = object()
    backend._vaporock_available = False
    buffers: list[object] = []

    def fake_petthermo(operation, *, args=(), kwargs=None):
        del operation, args
        call = dict(kwargs or {})
        buffers.append(call.get('fO2_buffer'))
        assert 'fO2_log' not in call
        return {
            'Conditions': {'mass': 100.0, 'P_bar': 1.0},
            'liquid1': dict(_FE_FREE_LIQUID_WT),
            'liquid1_prop': {'mass': 100.0},
            'melt_oxide_activities': dict(_FE_FREE_ACTIVITIES),
        }

    backend._run_petthermotools_isolated = fake_petthermo
    common = dict(
        temperature_C=1400.0,
        composition_kg=dict(_FE_FREE_KG),
        pressure_bar=1.0,
    )
    unconstrained = backend.equilibrate(
        **common,
        fO2_log=None,
        redox_mode=REDOX_MODE_NOT_AN_INPUT,
    )
    low = backend.equilibrate(**common, fO2_log=-11.0)
    high = backend.equilibrate(**common, fO2_log=-9.0)

    assert buffers == [None, None, None]
    assert low.liquid_composition_wt_pct == unconstrained.liquid_composition_wt_pct
    assert low.activity_coefficients == _FE_FREE_ACTIVITIES
    assert unconstrained.vapor_pressures_Pa == {}
    _assert_gas_only_notice(low)
    slope = _na_slope(-11.0, low.vapor_pressures_Pa['Na'], -9.0, high.vapor_pressures_Pa['Na'])
    assert slope == pytest.approx(_na_catalog_po2_exponent(), abs=1.0e-9)


def _fe_free_subprocess_stub(path: Path) -> None:
    script = r'''#!/bin/sh
cat > System_main_tbl.txt << 'EOF'
System Thermodynamic Data:
index Pressure Temperature mass fO2(absolute)
1 1.0 1400.000000 100.0 -3.000
EOF
cat > Phase_main_tbl.txt << 'EOF'
index 1 Pressure 1.00 Temperature 1400.00 Al2O3 CaO MgO Na2O SiO2
liquid1 100.0 0.0 0.0 1.0 0.0 Liquid 6.25 25.0 12.5 6.25 50.0
EOF
cat > Liquid_comp_tbl.txt << 'EOF'
Liquid Composition:
index Pressure Temperature mass Al2O3 CaO MgO Na2O SiO2
1 1.00 1400.00 100.0 6.25 25.0 12.5 6.25 50.0
EOF
cat > Bulk_comp_tbl.txt << 'EOF'
Bulk Composition:
index Pressure Temperature mass Al2O3 CaO MgO Na2O SiO2
1 1.00 1400.00 100.0 6.25 25.0 12.5 6.25 50.0
EOF
cat > Solid_comp_tbl.txt << 'EOF'
Solid Composition:
index Pressure Temperature mass Al2O3 CaO MgO Na2O SiO2
1 1.00 1400.00 0.000000 ---
EOF
cat << 'EOF'
<> Stable liquid assemblage achieved.
Initial alphaMELTS calculation at: P 1.000000 (bars), T 1400.000000 (C)
liquid: Al2O3 CaO MgO Na2O SiO2
100.0 g 6.25 25.0 12.5 6.25 50.0
Melt fraction = 1.0
EOF
'''
    path.write_text(script)
    path.chmod(0o755)


@pytest.mark.skipif(__import__('os').name != 'posix', reason='stub binary is POSIX')
def test_alphamelts_subprocess_fe_free_absolute_fo2_returns_na_at_printed_fo2(tmp_path: Path):
    from simulator.melt_backend.base import REDOX_MODE_NOT_AN_INPUT

    stub = tmp_path / 'alphamelts'
    _fe_free_subprocess_stub(stub)
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = stub
    backend._timeout_s = 5.0
    backend._engine_version = 'alphamelts-test-stub'
    common = dict(
        temperature_C=1400.0,
        composition_kg=dict(_FE_FREE_KG),
        pressure_bar=1.0,
        subprocess_run_mode=AlphaMELTSSubprocessRunMode.ISOTHERMAL,
    )
    unconstrained = backend.equilibrate(
        **common,
        fO2_log=None,
        redox_mode=REDOX_MODE_NOT_AN_INPUT,
    )
    low = backend.equilibrate(**common, fO2_log=-11.0)
    high = backend.equilibrate(**common, fO2_log=-9.0)

    assert low.liquid_composition_wt_pct == pytest.approx(_FE_FREE_LIQUID_WT)
    assert low.liquid_composition_wt_pct == pytest.approx(
        unconstrained.liquid_composition_wt_pct
    )
    assert unconstrained.vapor_pressures_Pa == {}
    assert low.printed_fO2_echo_log is None
    assert low.requested_fO2_log == -11.0
    _assert_gas_only_notice(low)
    # The table echo is -3. Using it would not match the catalog slope
    # between the two printed absolutes.
    assert low.diagnostics.get('intrinsic_fO2_log') == pytest.approx(-3.0)
    slope = _na_slope(-11.0, low.vapor_pressures_Pa['Na'], -9.0, high.vapor_pressures_Pa['Na'])
    assert slope == pytest.approx(_na_catalog_po2_exponent(), abs=1.0e-6)


def test_other_redox_without_iron_refuses_before_either_engine(tmp_path: Path):
    marker = tmp_path / 'invoked'
    stub = tmp_path / 'alphamelts'
    stub.write_text(f'#!/bin/sh\nprintf invoked > "{marker}"\n')
    stub.chmod(0o755)
    composition = {
        'SiO2': 0.5,
        'Al2O3': 0.125,
        'CaO': 0.25,
        'MgO': 0.124999,
        'Cr2O3': 0.000001,
    }
    alphamelts = AlphaMELTSBackend()
    alphamelts._mode = 'subprocess'
    alphamelts._binary_path = stub
    alphamelts._timeout_s = 1.0
    calls: list[object] = []

    class FakeTransport:
        def equilibrate(self, **kwargs):
            calls.append(kwargs)
            raise AssertionError('other redox must not reach ThermoEngine')

        def close(self):
            return None

    thermo = _thermoengine_backend(FakeTransport())
    calls_by_backend = (
        (
            alphamelts,
            {'subprocess_run_mode': AlphaMELTSSubprocessRunMode.ISOTHERMAL},
        ),
        (thermo, {}),
    )
    for backend, extra in calls_by_backend:
        result = backend.equilibrate(
            temperature_C=1400.0,
            composition_kg=dict(composition),
            fO2_log=-9.0,
            pressure_bar=1.0,
            **extra,
        )
        assert result.status == 'out_of_domain'
        assert result.diagnostics['backend_status_reason'] == 'unsupported_input'
        assert result.diagnostics['backend_failure_reason_code'] == (
            'redox_active_without_iron_couple'
        )
        assert result.diagnostics['redox_active_components'] == ['Cr2O3']
        assert 'Cr2O3' in result.warnings[0]
        assert result.vapor_pressures_Pa == {}
    assert calls == []
    assert not marker.exists()


def test_alphamelts_low_silica_alkali_absolute_fo2_still_refuses_before_launch(tmp_path: Path):
    marker = tmp_path / 'invoked'
    stub = tmp_path / 'alphamelts'
    stub.write_text(f'#!/bin/sh\nprintf invoked > "{marker}"\n')
    stub.chmod(0o755)
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = stub
    backend._timeout_s = 1.0
    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg={'SiO2': 0.30, 'Na2O': 0.70},
        fO2_log=-9.0,
        pressure_bar=1.0,
        subprocess_run_mode=AlphaMELTSSubprocessRunMode.ISOTHERMAL,
    )
    assert not marker.exists()
    assert result.status == 'out_of_domain'
    assert result.diagnostics['backend_status_reason'] == 'silicate_window'
    assert any('crash floor' in warning.lower() for warning in result.warnings)
