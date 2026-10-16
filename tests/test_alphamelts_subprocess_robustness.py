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


def test_real_subprocess_projection_closes_dry_alkali_silica_oxygen_balance():
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _engine_gas_pressures_bar_for_oxygen_balance,
        _solve_cell_oxygen_balance,
        _vapor_engine_oxygen_balance_species_metadata,
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
    fresh_metadata = _vapor_engine_oxygen_balance_species_metadata(
        tuple(fresh_cell.gas_partial_pressures_Pa)
    )

    def fresh_pressure_model(trial_po2_bar: float) -> dict[str, float]:
        trial_cell = cell_at_po2(trial_po2_bar)
        return _engine_gas_pressures_bar_for_oxygen_balance(
            trial_cell.gas_partial_pressures_Pa,
            fresh_metadata,
        )

    fresh_po2, fresh_pressures, fresh_balance, fresh_wall_fraction, _fresh_info = (
        _solve_cell_oxygen_balance(
            fresh_pressure_model,
            fresh_metadata,
            temperature_K=temperature_K,
            cell_material=None,
            bracket=tuple(balance['bracket']),
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
