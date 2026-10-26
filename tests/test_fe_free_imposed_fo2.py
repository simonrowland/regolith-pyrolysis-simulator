"""Pin Fe-bearing imposed-fO2 behaviour before the Fe-free gas-only path.

The numbers below are the current adapter output for one Fe-bearing melt
at a printed absolute fO2. A later Fe-free branch must leave this path
byte-identical: the solver still receives the requested fO2, and the
activities and the AlphaMELTS input file stay these values.
"""

from __future__ import annotations

from pathlib import Path

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
