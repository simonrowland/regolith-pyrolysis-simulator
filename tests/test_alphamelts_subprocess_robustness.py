from __future__ import annotations

import math
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from engines.alphamelts.subprocess_runner import equilibrate_via_subprocess
from simulator.diagnostic_helpers.binary_pot_battery import (
    _solve_vapor_engine_cell_oxygen_balance,
)
from simulator.melt_backend.alphamelts import (
    ALPHAMELTS_REASON_FE_FREE_ABSOLUTE_FO2_CRASH,
    ALPHAMELTS_REASON_SUBPROCESS_DIED,
    ALPHAMELTS_REASON_TIMEOUT,
    AlphaMELTSBackend,
    AlphaMELTSSubprocessRunMode,
)


def _composition_mol_by_account() -> dict[str, dict[str, float]]:
    return {
        'process.cleaned_melt': {
            'SiO2': 1.0,
            'Al2O3': 0.2,
            'FeO': 0.15,
            'MgO': 0.2,
            'CaO': 0.15,
            'Na2O': 0.05,
        }
    }


def _backend(binary: Path, *, timeout_s: float) -> AlphaMELTSBackend:
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = binary
    backend._timeout_s = timeout_s
    backend._engine_version = 'alphamelts-test-stub'
    return backend


def _run(
    backend: AlphaMELTSBackend,
    composition_mol_by_account: dict[str, dict[str, float]] | None = None,
):
    return equilibrate_via_subprocess(
        backend,
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        composition_mol_by_account=(
            composition_mol_by_account or _composition_mol_by_account()
        ),
        species_formula_registry={},
        run_mode=AlphaMELTSSubprocessRunMode.ISOTHERMAL,
    )


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.skipif(os.name != 'posix', reason='process-group contract is POSIX')
def test_hanging_alphamelts_times_out_kills_group_and_reaps(tmp_path, monkeypatch):
    pid_file = tmp_path / 'alphamelts-pids'
    stub = tmp_path / 'alphamelts'
    stub.write_text(
        '#!/bin/sh\n'
        'sleep 60 &\n'
        'child=$!\n'
        'printf "%s %s\\n" "$$" "$child" > "$ALPHAMELTS_TEST_PID_FILE"\n'
        'wait "$child"\n'
    )
    stub.chmod(0o755)
    monkeypatch.setenv('ALPHAMELTS_TEST_PID_FILE', str(pid_file))
    backend = _backend(stub, timeout_s=1.0)

    started = time.monotonic()
    result = _run(backend)
    elapsed = time.monotonic() - started

    assert elapsed < 1.2
    assert result.status == 'out_of_domain'
    assert result.diagnostics['backend_status_reason'] == ALPHAMELTS_REASON_TIMEOUT
    failure = result.diagnostics['subprocess_failure']
    assert failure['timeout_s'] == pytest.approx(1.0)
    assert failure['process_group_killed'] is True
    assert failure['launcher_reaped'] is True
    assert pid_file.is_file()
    pids = [int(value) for value in pid_file.read_text().split()]
    deadline = time.monotonic() + 1.0
    while any(_pid_exists(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not any(_pid_exists(pid) for pid in pids)


@pytest.mark.skipif(os.name != 'posix', reason='signal contract is POSIX')
def test_sigsegv_alphamelts_returns_composition_and_crash_point(tmp_path):
    stub = tmp_path / 'alphamelts'
    stub.write_text('#!/bin/sh\nkill -SEGV $$\n')
    stub.chmod(0o755)
    backend = _backend(stub, timeout_s=1.0)

    result = _run(backend)

    assert result.status == 'out_of_domain'
    assert result.diagnostics['backend_status_reason'] == (
        ALPHAMELTS_REASON_SUBPROCESS_DIED
    )
    assert result.diagnostics['backend_failure_category'] == 'engine_crash'
    assert result.diagnostics['subprocess_failure'] == {
        'stage': 'alphamelts_subprocess_execute',
        'command': [str(stub), '1'],
        'returncode': -11,
        'signal': 'SIGSEGV',
    }
    crash_point = result.diagnostics['out_of_domain_crash_point']
    assert crash_point['stage'] == 'alphamelts_subprocess_execute'
    assert crash_point['composition_mol_by_account'] == (
        _composition_mol_by_account()
    )
    assert any('SIGSEGV' in warning for warning in result.warnings)


def test_known_crashing_alkali_silica_binary_refuses_before_launch(tmp_path):
    marker = tmp_path / 'invoked'
    stub = tmp_path / 'alphamelts'
    stub.write_text(f'#!/bin/sh\nprintf invoked > "{marker}"\n')
    stub.chmod(0o755)
    backend = _backend(stub, timeout_s=1.0)
    composition = {
        'process.cleaned_melt': {
            'Na2O': 0.247,
            'SiO2': 0.753,
        }
    }

    result = _run(backend, composition)

    assert result.status != 'out_of_domain'
    assert result.status == 'not_converged'
    assert result.diagnostics['backend_status_reason'] == (
        ALPHAMELTS_REASON_FE_FREE_ABSOLUTE_FO2_CRASH
    )
    assert result.diagnostics['backend_failure_category'] == 'engine_crash'
    assert result.diagnostics['subprocess_input_guard']['predicate'] == (
        'fe_free_and_imposed_absolute_fo2'
    )
    assert result.diagnostics['subprocess_input_guard']['matching_scope'] == (
        'two_component_na2o_or_k2o_silica_family'
    )
    assert result.diagnostics['subprocess_input_guard']['not_the_predicate'] == (
        'no_Fe'
    )
    assert result.diagnostics['subprocess_input_guard']['active_components'] == [
        'Na2O',
        'SiO2',
    ]
    assert 'Fe-free' in result.warnings[0]
    assert 'imposed' in result.warnings[0]
    assert 'two-component alkali-silica' not in result.warnings[0]
    assert result.diagnostics['out_of_domain_crash_point'][
        'composition_mol_by_account'
    ] == composition
    assert not marker.exists()


def test_real_subprocess_projection_closes_dry_alkali_silica_oxygen_balance(
    monkeypatch,
):
    import simulator.diagnostic_helpers.binary_pot_battery as battery

    _solve_cell_oxygen_balance = battery._solve_cell_oxygen_balance
    owner_calls = []

    def capture_owner_call(pressure_model, species_metadata, **kwargs):
        result = _solve_cell_oxygen_balance(
            pressure_model,
            species_metadata,
            **kwargs,
        )
        owner_calls.append(
            (pressure_model, species_metadata, dict(kwargs), result)
        )
        return result

    monkeypatch.setattr(
        battery,
        '_solve_cell_oxygen_balance',
        capture_owner_call,
    )

    backend = AlphaMELTSBackend()
    solved_liquid_wt_pct = {'Na2O': 20.0, 'K2O': 20.0, 'SiO2': 60.0}
    temperature_K = 1800.0
    temperature_C = temperature_K - 273.15
    parsed_cells = {}

    def cell_at_po2(pO2_bar: float):
        if pO2_bar in parsed_cells:
            return parsed_cells[pO2_bar]
        requested_fO2_log = math.log10(pO2_bar)
        printed_echo_log = float(f'{requested_fO2_log:.3f}')
        system_output = (
            'System Thermodynamic Data:\n'
            'index Pressure Temperature mass fO2(absolute)\n'
            f'1 1.0 {temperature_C:.6f} 100.0 {printed_echo_log:.3f}\n'
        )
        table_outputs = {
            'System_main_tbl.txt': system_output,
            'Phase_main_tbl.txt': (
                'index 1 Pressure 1.00 Temperature 1526.85 Na2O K2O SiO2\n'
                'liquid1 100.0 0.0 0.0 1.0 0.0 Liquid 20.0 20.0 60.0\n'
            ),
            'Solid_comp_tbl.txt': (
                'Solid Composition:\n'
                'index Pressure Temperature mass Na2O K2O SiO2\n'
                '1 1.00 1526.85 0.000000 ---\n'
            ),
            'Bulk_comp_tbl.txt': (
                'Bulk Composition:\n'
                'index Pressure Temperature mass Na2O K2O SiO2\n'
                '1 1.00 1526.85 100.0 20.0 20.0 60.0\n'
            ),
            'Liquid_comp_tbl.txt': (
                'Liquid Composition:\n'
                'index Pressure Temperature mass Na2O K2O SiO2\n'
                '1 1.00 1526.85 100.0 20.0 20.0 60.0\n'
            ),
        }
        stdout = (
            '<> Stable liquid assemblage achieved.\n'
            'Initial alphaMELTS calculation at: P 1.000000 (bars), '
            f'T {temperature_C:.6f} (C)\n'
            'liquid: Na2O K2O SiO2\n'
            '100.0 g 20.0 20.0 60.0\n'
            'Melt fraction = 1.0\n'
        )
        eq = backend._parse_single_point_stdout(
            stdout,
            requested_temperature_C=temperature_C,
            pressure_bar=1.0,
            fO2_log=requested_fO2_log,
            total_input_kg=0.1,
            run_mode=AlphaMELTSSubprocessRunMode.ISOTHERMAL,
            system_output=system_output,
            fO2_constraint={
                'path': 'Absolute',
                'offset': float(f'{requested_fO2_log:.12g}'),
            },
            table_outputs=table_outputs,
        )
        assert eq.status == 'ok'
        assert eq.liquid_composition_wt_pct == pytest.approx(
            solved_liquid_wt_pct
        )
        pressures_pa, sources, _diagnostic = (
            backend._builtin_vapor_projection_for_subprocess(eq)
        )
        assert pressures_pa['O'] > 0.0
        assert sources['O'] == 'NASA_CEA_O2_dissociation_equilibrium'
        cell = SimpleNamespace(
            status='ok',
            gas_partial_pressures_Pa=pressures_pa,
            invalid_gas_partial_pressure_components=(),
            requested_fO2_log=getattr(eq, 'requested_fO2_log', None),
            printed_fO2_echo_log=getattr(
                eq,
                'printed_fO2_echo_log',
                eq.fO2_log,
            ),
        )
        parsed_cells[pO2_bar] = cell
        return cell

    pO2_bar, pressures_bar, balance, _cell_fraction, _cell_info = (
        _solve_vapor_engine_cell_oxygen_balance(
            cell_at_po2,
            temperature_K=temperature_K,
            cell_material=None,
            fixed_point_activities=True,
        )
    )

    assert 0.0 < pO2_bar < 1.0
    assert pressures_bar['O'] > 0.0
    assert balance['residual'] < 1e-8

    fresh_cell = cell_at_po2(pO2_bar)
    requested_po2_bar = 10.0 ** float(
        f'{fresh_cell.requested_fO2_log:.12g}'
    )
    printed_echo_po2_bar = 10.0 ** fresh_cell.printed_fO2_echo_log
    assert fresh_cell.gas_partial_pressures_Pa['O2'] / 1.0e5 == pytest.approx(
        requested_po2_bar,
        rel=1e-12,
    )
    assert abs(math.log10(requested_po2_bar) - fresh_cell.printed_fO2_echo_log) > 0.0
    assert fresh_cell.gas_partial_pressures_Pa['O2'] / 1.0e5 != pytest.approx(
        printed_echo_po2_bar,
        rel=1e-10,
    )
    assert owner_calls
    fresh_pressure_model, fresh_metadata, fresh_kwargs, _last_owner_result = (
        owner_calls[-1]
    )
    fresh_po2, fresh_pressures, fresh_balance, fresh_wall_fraction, _fresh_info = (
        _solve_cell_oxygen_balance(
            fresh_pressure_model,
            fresh_metadata,
            **fresh_kwargs,
        )
    )

    assert abs(math.log10(fresh_po2) - math.log10(pO2_bar)) <= 1e-12
    assert pressures_bar == fresh_pressures
    assert balance == fresh_balance
    assert _cell_fraction == fresh_wall_fraction
    assert balance['residual'] == fresh_balance['residual']
    assert balance['dominant_O_carriers'] == fresh_balance['dominant_O_carriers']
    assert balance['dominant_metal_carriers'] == fresh_balance[
        'dominant_metal_carriers'
    ]


# t-1469 crash families. Reason strings are the adapter constants' values.
# getattr falls back to that value until the constant exists, so this module
# still collects; the launch assertion is what fails while the guard is absent.
_CAO_SIO2_BINARY_CRASH = 'subprocess_cao_sio2_binary_crash'
_FE_FREE_SILICATE_ABSOLUTE_FO2_CRASH = (
    'subprocess_fe_free_silicate_absolute_fo2_crash'
)


def _crash_reason(constant_name: str, value: str) -> str:
    import simulator.melt_backend.alphamelts as alphamelts

    published = getattr(alphamelts, constant_name, value)
    assert published == value
    return str(published)


def _marker_backend(tmp_path: Path):
    marker = tmp_path / 'invoked'
    stub = tmp_path / 'alphamelts'
    stub.write_text(f'#!/bin/sh\nprintf invoked > "{marker}"\n')
    stub.chmod(0o755)
    return _backend(stub, timeout_s=1.0), marker


def _melt(moles: dict[str, float]) -> dict[str, dict[str, float]]:
    return {'process.cleaned_melt': dict(moles)}


def test_cao_sio2_binary_refuses_before_launch_with_imposed_fo2(tmp_path):
    """Ledger family CaO+SiO2 (76 rows). SIGABRT with absolute fO2 imposed.

    Reproduction composition is the lunar row kems-053-stolyarova-1991 at
    1993 K, mole CaO 0.51 / SiO2 0.49 (ledger line 4013276).
    """
    backend, marker = _marker_backend(tmp_path)
    composition = _melt({'CaO': 0.51, 'SiO2': 0.49})

    result = _run(backend, composition)

    assert not marker.exists()
    assert result.status == 'not_converged'
    reason = _crash_reason(
        'ALPHAMELTS_REASON_CAO_SIO2_BINARY_CRASH',
        _CAO_SIO2_BINARY_CRASH,
    )
    assert result.diagnostics['backend_status_reason'] == reason
    assert result.diagnostics['backend_status_reason'] != (
        ALPHAMELTS_REASON_FE_FREE_ABSOLUTE_FO2_CRASH
    )
    assert result.diagnostics['backend_failure_category'] == 'engine_crash'
    guard = result.diagnostics['subprocess_input_guard']
    assert guard['predicate'] == 'two_component_cao_sio2'
    assert guard['matching_scope'] == 'cao_sio2_binary'
    assert guard['independent_of_imposed_fo2'] is True
    assert guard['measured_signal'] == 'SIGABRT'
    assert guard['active_components'] == ['CaO', 'SiO2']
    assert 'CaO-SiO2' in result.warnings[0]


def test_cao_sio2_binary_refuses_before_launch_without_imposed_fo2(tmp_path):
    """Same binary SIGABRTs when absolute fO2 is omitted."""
    from simulator.melt_backend.base import REDOX_MODE_NOT_AN_INPUT

    backend, marker = _marker_backend(tmp_path)

    result = backend.equilibrate(
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=None,
        composition_mol={'CaO': 0.51, 'SiO2': 0.49},
        redox_mode=REDOX_MODE_NOT_AN_INPUT,
        subprocess_run_mode=AlphaMELTSSubprocessRunMode.ISOTHERMAL,
    )

    assert not marker.exists()
    assert result.status == 'not_converged'
    assert result.diagnostics['backend_status_reason'] == _crash_reason(
        'ALPHAMELTS_REASON_CAO_SIO2_BINARY_CRASH',
        _CAO_SIO2_BINARY_CRASH,
    )
    assert result.diagnostics['backend_failure_category'] == 'engine_crash'
    assert result.diagnostics['subprocess_input_guard']['active_components'] == [
        'CaO',
        'SiO2',
    ]


@pytest.mark.parametrize(
    ('label', 'moles'),
    (
        ('mullite', {'Al2O3': 0.6, 'SiO2': 0.4}),
        (
            'cas',
            {
                'Al2O3': 0.125891472868,
                'CaO': 0.434108527132,
                'SiO2': 0.44,
            },
        ),
        (
            'hastie',
            {
                'Na2O': 0.1642467099008939,
                'CaO': 0.12814024887559882,
                'SiO2': 0.7076130412235073,
            },
        ),
    ),
)
def test_measured_fe_free_silicate_absolute_fo2_refuses_before_launch(
    tmp_path,
    label: str,
    moles: dict[str, float],
):
    """SIGSEGV only when absolute fO2 is imposed. Not the alkali reason.

    mullite: shornikov-1994 / shults-1994, 34 rows.
    cas: stolyarova-1996, 142 rows.
    hastie: kems-020, 3 rows, inside the certified T and SiO2 band.
    """
    del label
    backend, marker = _marker_backend(tmp_path)

    result = _run(backend, _melt(moles))

    assert not marker.exists()
    assert result.status == 'not_converged'
    reason = _crash_reason(
        'ALPHAMELTS_REASON_FE_FREE_SILICATE_ABSOLUTE_FO2_CRASH',
        _FE_FREE_SILICATE_ABSOLUTE_FO2_CRASH,
    )
    assert result.diagnostics['backend_status_reason'] == reason
    assert result.diagnostics['backend_status_reason'] != (
        ALPHAMELTS_REASON_FE_FREE_ABSOLUTE_FO2_CRASH
    )
    assert result.diagnostics['backend_failure_category'] == 'engine_crash'
    guard = result.diagnostics['subprocess_input_guard']
    assert guard['predicate'] == 'fe_free_silicate_and_imposed_absolute_fo2'
    assert guard['measured_signal'] == 'SIGSEGV'
    assert guard['not_the_predicate'] == 'no_Fe'
    assert guard['active_components'] == sorted(moles)
    assert sorted(moles) in guard['component_sets']
    assert 'imposed' in result.warnings[0]


def test_mgo_sio2_absolute_fo2_is_outside_the_new_matching_scope(tmp_path):
    """Same SIGSEGV trigger, not one of the measured 414 sets, so it still launches."""
    backend, marker = _marker_backend(tmp_path)

    _run(backend, _melt({'MgO': 0.5, 'SiO2': 0.5}))

    assert marker.exists()


def test_fe_bearing_control_with_imposed_fo2_still_launches(tmp_path):
    backend, marker = _marker_backend(tmp_path)

    _run(backend)

    assert marker.exists()


def test_fe_free_silicate_without_imposed_fo2_still_launches(tmp_path):
    """CAS computes when absolute fO2 is omitted. The guard must not fire."""
    from simulator.melt_backend.base import REDOX_MODE_NOT_AN_INPUT

    backend, marker = _marker_backend(tmp_path)

    backend.equilibrate(
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=None,
        composition_mol={
            'Al2O3': 0.125891472868,
            'CaO': 0.434108527132,
            'SiO2': 0.44,
        },
        redox_mode=REDOX_MODE_NOT_AN_INPUT,
        subprocess_run_mode=AlphaMELTSSubprocessRunMode.ISOTHERMAL,
    )

    assert marker.exists()


def _qualification_handle(tmp_path: Path):
    from simulator.diagnostic_helpers.binary_pot_battery import EngineHandle

    backend, marker = _marker_backend(tmp_path)
    handle = EngineHandle(
        name='alphamelts',
        backend=backend,
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=False,
    )
    return handle, marker


def test_qualification_cao_sio2_crash_stays_engine_crash(tmp_path):
    """Lunar T makes the battery set qualification. The crash category must stay."""
    from simulator.diagnostic_helpers.binary_pot_battery import (
        REFUSAL_ENGINE_CRASH,
        BinaryPot,
        Po2Request,
        equilibrate_cell,
    )

    handle, marker = _qualification_handle(tmp_path)
    pot = BinaryPot(
        pot_id='stolyarova-1991-1993k',
        kato_1993_table4_system=None,
        why='lunar CaO-SiO2 oxygen-closure start cell',
        composition_wt_pct={'CaO': 49.2752, 'SiO2': 50.7248},
    )

    cell = equilibrate_cell(
        handle,
        pot,
        temperature_K=1993.0,
        po2=Po2Request(mode='commanded', po2_bar=1.0e-6),
        qualification=True,
        isolated=False,
    )

    assert not marker.exists()
    assert cell.status == 'refusal'
    assert cell.refusal_reason == REFUSAL_ENGINE_CRASH
    assert cell.engine_reason == _crash_reason(
        'ALPHAMELTS_REASON_CAO_SIO2_BINARY_CRASH',
        _CAO_SIO2_BINARY_CRASH,
    )
    assert cell.exit_code is None
    assert cell.exit_signal is None


@pytest.mark.parametrize(
    ('pot_id', 'composition_wt_pct'),
    (
        ('cao-tio2', {'CaO': 53.4883, 'TiO2': 46.5117}),
        ('alumina-cao', {'Al2O3': 50.0, 'CaO': 50.0}),
        ('spinel', {'Al2O3': 71.8, 'MgO': 28.2}),
        ('alumina', {'Al2O3': 100.0}),
        ('lime', {'CaO': 100.0}),
    ),
)
def test_qualification_does_not_launch_sio2_free_melt(
    tmp_path,
    pot_id: str,
    composition_wt_pct: dict[str, float],
):
    """SiO2=0 is the basis gate. Qualification must not replace that gate."""
    from engines.domain_reason import OutOfDomainReason
    from simulator.diagnostic_helpers.binary_pot_battery import (
        REFUSAL_GATE_REFUSED_IN_ADAPTER,
        BinaryPot,
        Po2Request,
        equilibrate_cell,
    )

    handle, marker = _qualification_handle(tmp_path)
    pot = BinaryPot(
        pot_id=pot_id,
        kato_1993_table4_system=None,
        why='SiO2-free oxygen-closure start cell',
        composition_wt_pct=composition_wt_pct,
    )

    cell = equilibrate_cell(
        handle,
        pot,
        temperature_K=2278.0,
        po2=Po2Request(mode='commanded', po2_bar=1.0e-6),
        qualification=True,
        isolated=False,
    )

    assert not marker.exists()
    assert cell.status == 'refusal'
    assert cell.refusal_reason == REFUSAL_GATE_REFUSED_IN_ADAPTER
    assert cell.engine_reason == OutOfDomainReason.SILICATE_WINDOW.value
    notice = next(
        item for item in cell.notices if item.get('kind') == 'melts_domain_gate'
    )
    assert 'AlphaMELTSBackend._domain_gate' not in ' '.join(
        notice['bypassable_from_harness']
    )
    assert '_domain_gate' in ' '.join(notice['not_bypassable_from_harness'])


def test_qualification_worker_does_not_launch_sio2_free_melt(tmp_path):
    import io

    from simulator.diagnostic_helpers.binary_pot_battery import (
        run_isolated_cell_worker,
    )

    handle, marker = _qualification_handle(tmp_path)
    protocol = io.StringIO()
    run_isolated_cell_worker(
        {
            'pot_id': 'cao-tio2-worker',
            'composition_wt_pct': {'CaO': 53.4883, 'TiO2': 46.5117},
            'why': 'isolated qualification must keep the basis gate',
            'po2': {'mode': 'commanded', 'po2_bar': 1.0e-6},
            'engine': 'alphamelts',
            'qualification': True,
            'temperature_K': 2278.0,
            'timeout_s': 5.0,
            'arm': 'qualification',
        },
        handles={'alphamelts': handle},
        protocol_stdout=protocol,
    )

    assert not marker.exists()
    assert 'silicate_window' in protocol.getvalue()
    assert 'subprocess_died' not in protocol.getvalue()


def test_qualification_band_miss_with_iron_still_launches(tmp_path):
    """SiO2 outside [30, 80] is notice+run. Removing the bypass must not refuse it."""
    from simulator.diagnostic_helpers.binary_pot_battery import (
        BinaryPot,
        Po2Request,
        equilibrate_cell,
    )

    handle, marker = _qualification_handle(tmp_path)
    pot = BinaryPot(
        pot_id='qual-sio2-20',
        kato_1993_table4_system=None,
        why='band miss that the domain gate admits',
        composition_wt_pct={'SiO2': 20.0, 'FeO': 48.0, 'MgO': 32.0},
    )

    equilibrate_cell(
        handle,
        pot,
        temperature_K=1700.0,
        po2=Po2Request(mode='commanded', po2_bar=1.0e-6),
        qualification=True,
        isolated=False,
    )

    assert marker.exists()
